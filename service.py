"""
Background service for the TED Talks addon.

Runs at Kodi startup and periodically syncs the TED catalog
from the Algolia search API into a local SQLite database.

On cold start (empty DB): performs a full catalog sync.
Every 12 hours: performs an incremental sync of newest talks.
"""

import os
import time
import logging
import concurrent.futures

import xbmc
import xbmcgui
import xbmcvfs
import xbmcaddon

from resources.lib.model.db import TedDatabase
from resources.lib.model import ted_api

logger = logging.getLogger("plugin.video.ted.talks.service")

ADDON_ID = "plugin.video.ted.talks"
DB_FILENAME = "ted_catalog.db"
SYNC_INTERVAL_HOURS = 12
INCREMENTAL_PAGES = 3  # Fetch first N pages for incremental sync
ENRICHMENT_WORKERS = 5  # Thread pool size for enrichment


class TedPlayer(xbmc.Player):
    """
    Monitors playback to show a post-playback review dialog
    when a TED talk finishes.
    """

    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path
        self._current_slug = None

    def onAVStarted(self):
        """Called when playback actually starts (video is rendering)."""
        try:
            slug = xbmcgui.Window(10000).getProperty("ted_talks_playing_slug")
            if slug:
                self._current_slug = slug
                xbmc.log("TED Talks: Now playing %s" % slug, xbmc.LOGINFO)
        except Exception:
            pass

    def onPlayBackEnded(self):
        """Called when playback reaches the end naturally."""
        self._on_playback_done(completed=True)

    def onPlayBackStopped(self):
        """Called when user stops playback."""
        self._on_playback_done(completed=False)

    def _on_playback_done(self, completed):
        """Handle playback end — save progress and show review dialog."""
        slug = self._current_slug
        if not slug:
            return

        try:
            db = TedDatabase(self.db_path)

            if completed:
                db.mark_watched(slug)
            else:
                # Save resume point
                try:
                    resume_at = self.getTime()
                    total_time = self.getTotalTime()
                    # Consider it watched if > 90% through
                    if total_time > 0 and resume_at / total_time > 0.9:
                        db.mark_watched(slug)
                    else:
                        db.update_playback(slug, resume_at, total_time)
                except Exception:
                    pass  # Player may not have time info anymore

            db.close()
        except Exception as e:
            xbmc.log("TED Talks: Playback tracking error: %s" % e, xbmc.LOGWARNING)

        self._show_review_dialog()

    def _show_review_dialog(self):
        """Show the post-playback favorite dialog."""
        slug = self._current_slug
        self._current_slug = None

        # Clear the window property
        try:
            xbmcgui.Window(10000).clearProperty("ted_talks_playing_slug")
        except Exception:
            pass

        if not slug:
            return

        try:
            db = TedDatabase(self.db_path)
            talk = db.get_talk_by_slug(slug)
            if not talk:
                db.close()
                return

            title = talk["title"] or "this talk"
            is_fav = db.is_favorite(slug)

            if is_fav:
                # Already a favorite, offer to unfavorite
                result = xbmcgui.Dialog().yesno(
                    "TED Talks",
                    "%s is in your favorites." % title,
                    yeslabel="Remove Favorite",
                    nolabel="OK",
                )
                if result:
                    db.set_favorite(slug, False)
                    xbmc.log("TED Talks: Unfavorited %s" % slug, xbmc.LOGINFO)
            else:
                result = xbmcgui.Dialog().yesno(
                    "TED Talks",
                    'Add "%s" to favorites?' % title,
                    yeslabel="Favorite",
                    nolabel="No Thanks",
                )
                if result:
                    db.set_favorite(slug, True)
                    xbmc.log("TED Talks: Favorited %s" % slug, xbmc.LOGINFO)

            db.close()
        except Exception as e:
            xbmc.log("TED Talks: Review dialog error: %s" % e, xbmc.LOGWARNING)


def _get_db_path():
    """Get the path to the SQLite database in addon_data."""
    addon = xbmcaddon.Addon(id=ADDON_ID)
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not os.path.exists(profile):
        os.makedirs(profile, exist_ok=True)
    db_path = os.path.join(profile, DB_FILENAME)

    # On first run, copy the bundled DB if available
    if not os.path.exists(db_path):
        bundled = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "resources",
            "data",
            DB_FILENAME,
        )
        if os.path.exists(bundled):
            import shutil

            shutil.copy2(bundled, db_path)
            xbmc.log("TED Talks: Copied bundled catalog database", xbmc.LOGINFO)

    return db_path


def _sync_page(db, page, hits_per_page=24):
    """Fetch one page from the API and upsert into the DB."""
    result = ted_api.search(page=page, hits_per_page=hits_per_page)
    talks = result["hits"]
    db.upsert_talks(talks)
    # Set speakers from the API's speakers string
    for talk in talks:
        if talk.get("speakers_str"):
            db.set_talk_speakers_from_api(talk["object_id"], talk["speakers_str"])
    db.conn.commit()
    return result


def full_sync(db, monitor):
    """
    Perform a full catalog sync. Fetches all pages from the API.
    Checks monitor.abortRequested() between pages for clean shutdown.
    """
    xbmc.log("TED Talks: Starting full catalog sync", xbmc.LOGINFO)

    # Get first page to determine total
    result = _sync_page(db, 0)
    total_pages = result["pages"]
    xbmc.log(
        "TED Talks: Full sync - %d talks across %d pages"
        % (result["total"], total_pages),
        xbmc.LOGINFO,
    )

    for page in range(1, total_pages):
        if monitor.abortRequested():
            xbmc.log("TED Talks: Sync aborted by Kodi shutdown", xbmc.LOGINFO)
            return False
        try:
            _sync_page(db, page)
        except Exception as e:
            xbmc.log("TED Talks: Error on page %d: %s" % (page, e), xbmc.LOGWARNING)
            time.sleep(1)
            continue
        # Small delay to avoid hammering the API
        time.sleep(0.1)

    db.mark_synced("last_full_sync")
    xbmc.log(
        "TED Talks: Full sync complete - %d talks in DB" % db.get_talk_count(),
        xbmc.LOGINFO,
    )
    return True


def incremental_sync(db, monitor):
    """
    Fetch the newest few pages from the API to pick up recent talks.
    """
    xbmc.log("TED Talks: Starting incremental sync", xbmc.LOGINFO)

    for page in range(INCREMENTAL_PAGES):
        if monitor.abortRequested():
            return False
        try:
            _sync_page(db, page)
        except Exception as e:
            xbmc.log(
                "TED Talks: Incremental sync error on page %d: %s" % (page, e),
                xbmc.LOGWARNING,
            )
            break
        time.sleep(0.1)

    db.mark_synced("last_incremental_sync")
    xbmc.log("TED Talks: Incremental sync complete", xbmc.LOGINFO)
    return True


def sync_topics(db):
    """Fetch topics from ted.com/topics and populate the topics table."""
    import re
    import json
    import requests as req
    from resources.lib.model.talk_page import topic_label

    # Use system CA bundle if available
    ca_bundle = None
    for p in ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]:
        if os.path.exists(p):
            ca_bundle = p
            break

    xbmc.log("TED Talks: Syncing topics from ted.com", xbmc.LOGINFO)
    try:
        resp = req.get(
            "https://www.ted.com/topics", timeout=15, verify=ca_bundle or True
        )
        if not resp.ok:
            xbmc.log(
                "TED Talks: Failed to fetch topics page: %d" % resp.status_code,
                xbmc.LOGWARNING,
            )
            return
    except Exception as e:
        xbmc.log("TED Talks: Failed to fetch topics page: %s" % e, xbmc.LOGWARNING)
        return

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', resp.text, re.S)
    if not m:
        xbmc.log("TED Talks: No __NEXT_DATA__ found on topics page", xbmc.LOGWARNING)
        return

    data = json.loads(m.group(1))
    topics_list = data.get("props", {}).get("pageProps", {}).get("list", [])

    count = 0
    for group in topics_list:
        for item in group.get("items", []):
            raw_name = item.get("name", "")
            slug = item.get("slug", "")
            if not raw_name:
                continue
            label = topic_label(raw_name, slug)
            # Update existing by slug, or insert new
            db.conn.execute("UPDATE topics SET name = ? WHERE slug = ?", (label, slug))
            db.conn.execute(
                "INSERT OR IGNORE INTO topics(name, slug) VALUES (?, ?)",
                (label, slug),
            )
            count += 1
    db.conn.commit()
    xbmc.log("TED Talks: Synced %d topics" % count, xbmc.LOGINFO)


def enrich_unenriched(db, monitor, max_talks=None):
    """
    Enrich talks that haven't been enriched yet by fetching their
    talk pages. Runs in a thread pool for parallelism.
    If max_talks is None, enriches all unenriched talks.
    """
    import os
    import requests
    from resources.lib.model import talk_page

    # Use system CA bundle if available
    ca_bundle = None
    for p in ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]:
        if os.path.exists(p):
            ca_bundle = p
            break
    verify = ca_bundle or True

    if max_talks:
        rows = db.conn.execute(
            "SELECT slug FROM talks WHERE enriched_at IS NULL LIMIT ?",
            (max_talks,),
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT slug FROM talks WHERE enriched_at IS NULL"
        ).fetchall()

    if not rows:
        return

    xbmc.log(
        "TED Talks: Enriching %d talks" % len(rows),
        xbmc.LOGINFO,
    )

    def _fetch_one(slug):
        """Fetch talk page HTML in a worker thread (no DB access)."""
        try:
            url = "https://www.ted.com/talks/%s" % slug
            resp = requests.get(url, timeout=15, verify=verify)
            if resp.ok:
                data = talk_page.extract_enrichment(resp.text)
                if data:
                    return (slug, data)
        except Exception as e:
            logger.debug("Failed to enrich %s: %s", slug, e)
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as pool:
        futures = {}
        for row in rows:
            if monitor.abortRequested():
                break
            f = pool.submit(_fetch_one, row["slug"])
            futures[f] = row["slug"]

        # Write results to DB from the main thread (serialized)
        for future in concurrent.futures.as_completed(futures, timeout=300):
            if monitor.abortRequested():
                break
            result = future.result()
            if result:
                slug, data = result
                try:
                    db.enrich_talk(slug, data)
                except Exception as e:
                    logger.debug("DB enrich failed for %s: %s", slug, e)

    xbmc.log("TED Talks: Enrichment batch complete", xbmc.LOGINFO)


def run():
    """Main service loop."""
    monitor = xbmc.Monitor()
    db_path = _get_db_path()

    xbmc.log("TED Talks: Service starting, DB at %s" % db_path, xbmc.LOGINFO)

    # Wait a bit for Kodi to fully start before doing heavy I/O
    if monitor.waitForAbort(10):
        return

    db = TedDatabase(db_path)

    # Start playback monitor for post-playback review dialog
    player = TedPlayer(db_path)

    try:
        # Cold start: full sync if never completed a full sync
        if db.get_sync_meta("last_full_sync") is None:
            full_sync(db, monitor)
            if not monitor.abortRequested():
                sync_topics(db)
            if not monitor.abortRequested():
                enrich_unenriched(db, monitor)
        elif db.needs_sync("last_full_sync", max_age_hours=SYNC_INTERVAL_HOURS):
            incremental_sync(db, monitor)

        # Main loop
        while not monitor.abortRequested():
            # Sleep for the sync interval
            if monitor.waitForAbort(SYNC_INTERVAL_HOURS * 3600):
                break

            # Periodic incremental sync
            if not monitor.abortRequested():
                incremental_sync(db, monitor)

            # Refresh topics periodically
            if not monitor.abortRequested():
                sync_topics(db)

            # Enrich some unenriched talks each cycle
            if not monitor.abortRequested():
                enrich_unenriched(db, monitor, max_talks=50)

    finally:
        db.close()
        xbmc.log("TED Talks: Service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    run()
