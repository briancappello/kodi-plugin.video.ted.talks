"""
TED Talks Kodi plugin — main controller.

Handles the plugin:// URL dispatch, directory listing, and video playback.
Uses a local SQLite database as the primary data source, with fallback
to the TED search API when the DB is empty or stale.
"""

import io
import logging
import os
import sys
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin

from . import settings
from .model.db import TedDatabase
from .model import ted_api
from .model import talk_page
from .model.fetcher import Fetcher

__handle__ = int(sys.argv[1])

DB_FILENAME = "ted_catalog.db"
PAGE_SIZE = 24
VIDEO_SORT_METHODS = ["dateadded", "title", "views", "none"]


def _log(message, level="info"):
    settings.report(message, level=level)


def _get_db():
    """Open the catalog database, creating it if needed."""
    db_dir = settings.__temp_path__ or ""
    if not db_dir:
        import xbmcvfs
        import xbmcaddon

        addon = xbmcaddon.Addon(id=settings.__plugin_id__)
        db_dir = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, DB_FILENAME)

    # On first run, copy the bundled DB if no DB exists yet
    if not os.path.exists(db_path):
        bundled = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "resources",
            "data",
            DB_FILENAME,
        )
        if os.path.exists(bundled):
            import shutil

            shutil.copy2(bundled, db_path)
            _log("Copied bundled catalog database to %s" % db_path)

    return TedDatabase(db_path)


class UI:
    """Helper class wrapping Kodi plugin API calls."""

    def __init__(self, fetcher):
        self.fetcher = fetcher

    @staticmethod
    def localized(string_id):
        return settings.get_localized_string(string_id)

    @staticmethod
    def create_action_url(mode, url="", **kwargs):
        params = {"mode": mode}
        if url:
            params["url"] = url
        params.update(kwargs)
        qs = urllib.parse.urlencode(params)
        return "plugin://%s/?%s" % (settings.__plugin_id__, qs)

    @staticmethod
    def add_directory_item(url, listitem, is_folder=True):
        xbmcplugin.addDirectoryItem(__handle__, url, listitem, is_folder)

    @staticmethod
    def next_page_item(mode, **kwargs):
        """Create a 'Next page >>' item pinned to the bottom of the listing."""
        li = xbmcgui.ListItem("Next page >>", offscreen=True)
        li.setProperty("SpecialSort", "bottom")
        url = UI.create_action_url(mode, **kwargs)
        UI.add_directory_item(url, li, is_folder=True)

    @staticmethod
    def end_directory(content="videos", sort_methods=None, update_listing=False):
        if content:
            xbmcplugin.setContent(__handle__, content)
        aliases = {
            "none": xbmcplugin.SORT_METHOD_UNSORTED,
            "date": xbmcplugin.SORT_METHOD_DATE,
            "dateadded": xbmcplugin.SORT_METHOD_DATEADDED,
            "title": xbmcplugin.SORT_METHOD_LABEL,
            "episode": xbmcplugin.SORT_METHOD_EPISODE,
            "views": xbmcplugin.SORT_METHOD_PLAYCOUNT,
        }
        for sm in sort_methods or ["none"]:
            xbmcplugin.addSortMethod(__handle__, aliases.get(sm, 0))
        xbmcplugin.endOfDirectory(__handle__, updateListing=update_listing)

    def talk_listitem(self, talk_row, is_folder=False):
        """
        Create a ListItem from a database talk row (sqlite3.Row).
        Works for both folder items (browse into) and playable items.
        """
        title = talk_row["title"] or ""
        speakers = talk_row["speaker_names"] or ""
        label = "%s | %s" % (title, speakers) if speakers else title

        li = xbmcgui.ListItem(label, offscreen=True)

        info = {"title": title, "mediatype": "video"}
        if talk_row["description"]:
            info["plot"] = talk_row["description"]
        if talk_row["published_at"]:
            info["dateadded"] = (
                talk_row["published_at"].replace("T", " ").replace("Z", "")[:19]
            )
        if talk_row["duration"]:
            li.addStreamInfo("video", {"duration": int(talk_row["duration"])})
        if speakers:
            info["cast"] = [s.strip() for s in speakers.split(",")]
        if talk_row["view_count"]:
            info["playcount"] = int(talk_row["view_count"])

        li.setInfo(type="video", infoLabels=info)

        thumb = talk_row["thumb_url"] or ""
        if thumb:
            li.setArt({"thumb": thumb, "icon": thumb})

        if not is_folder:
            li.setProperty("IsPlayable", "true")

        return li


def _api_fallback_newest(db, page=0):
    """
    Fallback: fetch newest talks from the API, write-through to DB.
    Returns list of sqlite3.Row-like dicts.
    """
    try:
        result = ted_api.search(page=page, hits_per_page=PAGE_SIZE)
        if result["hits"]:
            db.upsert_talks(result["hits"])
            for talk in result["hits"]:
                if talk.get("speakers_str"):
                    db.set_talk_speakers_from_api(
                        talk["object_id"], talk["speakers_str"]
                    )
            db.conn.commit()
        return db.get_newest(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    except Exception as e:
        _log("API fallback failed: %s" % e, level="error")
        return []


def _api_fallback_search(db, query, page=0):
    """Fallback: search via API, write-through to DB, return DB results."""
    try:
        result = ted_api.search(query=query, page=page, hits_per_page=PAGE_SIZE)
        if result["hits"]:
            db.upsert_talks(result["hits"])
            for talk in result["hits"]:
                if talk.get("speakers_str"):
                    db.set_talk_speakers_from_api(
                        talk["object_id"], talk["speakers_str"]
                    )
            db.conn.commit()
        # Search DB for actual ranked results
        return db.search_talks(query, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    except Exception as e:
        _log("API fallback failed: %s" % e, level="error")
        return []


def _resolve_youtube(url):
    """
    Resolve a YouTube URL to a direct stream URL.
    Tries youtube_dl (from Kodi script.module.youtube.dl), then yt_dlp,
    then falls back to the YouTube plugin URL.
    Returns a playable URL or None.
    """
    # Try youtube_dl (bundled with script.module.youtube.dl Kodi addon)
    try:
        try:
            import youtube_dl
        except ImportError:
            # Try to find it in the Kodi addons directory
            import sys as _sys

            try:
                import xbmcvfs

                ydl_path = xbmcvfs.translatePath(
                    "special://home/addons/script.module.youtube.dl/lib"
                )
                if ydl_path not in _sys.path:
                    _sys.path.insert(0, ydl_path)
                import youtube_dl
            except Exception:
                raise ImportError("youtube_dl not available")

        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
            "logger": logging.getLogger("youtube_dl"),
        }
        # Patch stdout/stderr for youtube_dl — Kodi's wrappers lack isatty()
        import sys as _sys

        _orig_stdout, _orig_stderr = _sys.stdout, _sys.stderr
        _sys.stdout = io.StringIO()
        _sys.stderr = io.StringIO()
        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                stream_url = info.get("url")
                if stream_url:
                    _log("Resolved YouTube via youtube_dl")
                    return stream_url
        finally:
            _sys.stdout, _sys.stderr = _orig_stdout, _orig_stderr
    except ImportError:
        pass
    except Exception as e:
        _log("youtube_dl failed: %s" % e, level="warn")

    # Try yt-dlp
    try:
        import yt_dlp

        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url")
            if stream_url:
                _log("Resolved YouTube via yt-dlp")
                return stream_url
    except ImportError:
        pass
    except Exception as e:
        _log("yt-dlp failed: %s" % e, level="warn")

    # Fall back to YouTube plugin URL
    try:
        yt_id = url.split("v=")[1].split("&")[0]
        plugin_url = "plugin://plugin.video.youtube/play/?video_id=%s" % yt_id
        _log("Falling back to YouTube plugin for %s" % yt_id)
        return plugin_url
    except Exception:
        pass

    return None


# -----------------------------------------------------------------------
# Actions
# -----------------------------------------------------------------------
# Actions
# -----------------------------------------------------------------------


def show_categories(ui):
    """Show the main menu."""
    items = [
        (30001, "newest", {}),  # Newest Talks
        (30004, "search", {}),  # Search
        (30002, "speakers", {}),  # Speakers
        (30007, "topics", {}),  # Topics
    ]
    for string_id, mode, extra in items:
        title = ui.localized(string_id)
        li = xbmcgui.ListItem(title, offscreen=True)
        li.setArt({"icon": settings.__plugin_icon__})
        url = UI.create_action_url(mode, **extra)
        UI.add_directory_item(url, li, is_folder=True)

    UI.end_directory(content="", sort_methods=["none"])


def action_newest(ui, db, args):
    """List newest talks from the DB (or API fallback)."""
    page = int(args.get("page", "0"))
    offset = page * PAGE_SIZE

    talks = db.get_newest(limit=PAGE_SIZE, offset=offset)

    # Write-through fallback if DB is empty
    if not talks:
        talks = _api_fallback_newest(db, page)

    for talk in talks:
        slug = talk["slug"]
        url = UI.create_action_url("play", url=slug)
        li = ui.talk_listitem(talk)
        UI.add_directory_item(url, li, is_folder=False)

    if len(talks) == PAGE_SIZE:
        UI.next_page_item("newest", page=str(page + 1))

    UI.end_directory("videos", VIDEO_SORT_METHODS, update_listing=(page > 0))


def action_search(ui, db, args):
    """Search talks via FTS5 (or API fallback)."""
    query = args.get("q", "")
    page = int(args.get("page", "0"))

    if not query:
        # Show keyboard
        kb = xbmc.Keyboard(settings.get_current_search(), ui.localized(30004))
        kb.doModal()
        if not kb.isConfirmed():
            return
        query = kb.getText().strip()
        if not query:
            return
        settings.set_current_search(query)

    # Try DB first
    talks = db.search_talks(query, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    # Fallback to API if DB has few results
    if len(talks) < 3 and page == 0:
        api_talks = _api_fallback_search(db, query, page)
        if len(api_talks) > len(talks):
            talks = api_talks

    for talk in talks:
        slug = talk["slug"]
        url = UI.create_action_url("play", url=slug)
        li = ui.talk_listitem(talk)
        UI.add_directory_item(url, li, is_folder=False)

    if len(talks) == PAGE_SIZE:
        UI.next_page_item("search", q=query, page=str(page + 1))

    UI.end_directory("videos", VIDEO_SORT_METHODS, update_listing=(page > 0))


def action_topics(ui, db, args):
    """List topics or talks within a topic."""
    topic = args.get("topic", "")

    if not topic:
        # Show topic list
        topics = db.get_topics()

        # If DB has no topics yet, try fetching tags from the API
        if not topics:
            try:
                tags = ted_api.get_all_tags(max_values=500)
                for name in tags:
                    db.conn.execute(
                        "INSERT OR IGNORE INTO topics(name) VALUES (?)", (name,)
                    )
                db.conn.commit()
                topics = db.get_topics()
            except Exception as e:
                _log("Failed to fetch tags: %s" % e, level="error")

        # If still no topics with talk counts, show all topics from the table
        if not topics:
            rows = db.conn.execute(
                "SELECT id, name, slug, 0 as talk_count FROM topics ORDER BY name"
            ).fetchall()
            topics = rows

        for t in topics:
            label = (
                "%s (%d)" % (t["name"], t["talk_count"])
                if t["talk_count"]
                else t["name"]
            )
            li = xbmcgui.ListItem(label, offscreen=True)
            url = UI.create_action_url("topics", topic=t["name"])
            UI.add_directory_item(url, li, is_folder=True)

        UI.end_directory("files", ["title"])
        return

    # Show talks for a topic
    page = int(args.get("page", "0"))
    talks = db.get_talks_by_topic(topic, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    # If no talks found, try API with tag filter
    if not talks:
        try:
            result = ted_api.search(
                facet_filters=["tags:%s" % topic],
                page=page,
                hits_per_page=PAGE_SIZE,
            )
            if result["hits"]:
                db.upsert_talks(result["hits"])
                for talk in result["hits"]:
                    if talk.get("speakers_str"):
                        db.set_talk_speakers_from_api(
                            talk["object_id"], talk["speakers_str"]
                        )
                db.conn.commit()
                talks = db.get_talks_by_topic(
                    topic, limit=PAGE_SIZE, offset=page * PAGE_SIZE
                )
        except Exception as e:
            _log("Topic API fallback failed: %s" % e, level="error")

    for talk in talks:
        slug = talk["slug"]
        url = UI.create_action_url("play", url=slug)
        li = ui.talk_listitem(talk)
        UI.add_directory_item(url, li, is_folder=False)

    if len(talks) == PAGE_SIZE:
        UI.next_page_item("topics", topic=topic, page=str(page + 1))

    xbmcplugin.setPluginCategory(__handle__, topic)
    UI.end_directory("videos", VIDEO_SORT_METHODS, update_listing=(page > 0))


def action_speakers(ui, db, args):
    """List speakers or talks by a speaker."""
    speaker = args.get("speaker", "")

    if not speaker:
        # Show speaker list
        page = int(args.get("page", "0"))
        speakers = db.get_speakers(limit=PAGE_SIZE * 4, offset=page * PAGE_SIZE * 4)

        for s in speakers:
            label = "%s (%d)" % (s["name"], s["talk_count"])
            li = xbmcgui.ListItem(label, offscreen=True)
            if s["thumb_url"]:
                li.setArt({"thumb": s["thumb_url"]})
            else:
                li.setArt({"icon": settings.__speaker_icon__})
            url = UI.create_action_url("speakers", speaker=s["name"])
            UI.add_directory_item(url, li, is_folder=True)

        if len(speakers) == PAGE_SIZE * 4:
            UI.next_page_item("speakers", page=str(page + 1))

        UI.end_directory("artists", ["title"], update_listing=(page > 0))
        return

    # Show talks by speaker
    page = int(args.get("page", "0"))
    talks = db.get_talks_by_speaker(speaker, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    for talk in talks:
        slug = talk["slug"]
        url = UI.create_action_url("play", url=slug)
        li = ui.talk_listitem(talk)
        UI.add_directory_item(url, li, is_folder=False)

    if len(talks) == PAGE_SIZE:
        UI.next_page_item("speakers", speaker=speaker, page=str(page + 1))

    xbmcplugin.setPluginCategory(__handle__, speaker)
    UI.end_directory("videos", VIDEO_SORT_METHODS, update_listing=(page > 0))


def action_play(ui, db, args):
    """
    Resolve and play a video. Fetches the talk page for the stream URL
    and enriches the DB as a side effect.
    """
    slug = args.get("url", "")
    if not slug:
        _log("play: missing slug", level="error")
        xbmcplugin.setResolvedUrl(__handle__, False, xbmcgui.ListItem())
        return

    url = "https://www.ted.com/talks/%s" % slug

    try:
        html = ui.fetcher.get_HTML(url)
    except Exception as e:
        _log("Failed to fetch talk page: %s" % e, level="error")
        xbmcplugin.setResolvedUrl(__handle__, False, xbmcgui.ListItem())
        return

    # Extract stream info
    subtitle_langs = settings.get_subtitle_languages()
    info = talk_page.extract_stream_info(html, subtitle_languages=subtitle_langs)

    stream = info.get("stream")

    # Handle YouTube-hosted talks
    if stream and "youtube.com" in stream:
        resolved = _resolve_youtube(stream)
        if resolved:
            stream = resolved
        else:
            # Could not resolve — show error
            _log("YouTube video %s could not be resolved" % slug, level="error")
            settings.report(
                "This talk is hosted on YouTube and could not be resolved",
                "TED Talks: Cannot play YouTube video",
                level="error",
            )
            xbmcplugin.setResolvedUrl(__handle__, False, xbmcgui.ListItem())
            return

    if not stream:
        _log("No stream found for %s" % slug, level="error")
        settings.report(
            "No video stream found",
            "TED Talks error: No stream available",
            level="error",
        )
        xbmcplugin.setResolvedUrl(__handle__, False, xbmcgui.ListItem())
        return

    # Resolve subtitles
    subtitles_path = None
    if info.get("subtitles"):
        subtitles_path = talk_page.resolve_subtitles(
            ui.fetcher.get_HTML,
            info["subtitles"],
            cache_dir=settings.__temp_path__,
        )

    # Build ListItem for playback
    li = xbmcgui.ListItem(info.get("title", ""), path=stream, offscreen=True)
    li.setInfo(
        type="video",
        infoLabels={
            "title": info.get("title", ""),
            "plot": info.get("description", ""),
            "date": info.get("date", ""),
            "aired": info.get("aired", ""),
            "dateadded": info.get("dateadded", ""),
            "genre": info.get("genre", ""),
            "mediatype": "video",
        },
    )
    if info.get("duration"):
        li.addStreamInfo("video", {"duration": int(info["duration"])})
    if info.get("thumb"):
        li.setArt({"thumb": info["thumb"], "icon": info["thumb"]})
    if subtitles_path:
        li.setSubtitles([subtitles_path])

    xbmcplugin.setResolvedUrl(__handle__, True, li)

    # Enrich the DB as a side effect (non-blocking for the user)
    try:
        enrichment = talk_page.extract_enrichment(html)
        if enrichment:
            db.enrich_talk(slug, enrichment)
    except Exception as e:
        _log("Enrichment failed for %s: %s" % (slug, e), level="warn")


# -----------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------


class Main:
    def __init__(self, args_map=None):
        self.args = args_map or {}

    def run(self):
        fetcher = Fetcher(_log)
        ui = UI(fetcher)
        db = _get_db()

        try:
            mode = self.args.get("mode", "")

            if not mode:
                show_categories(ui)
            elif mode == "newest":
                action_newest(ui, db, self.args)
            elif mode == "search":
                action_search(ui, db, self.args)
            elif mode == "topics":
                action_topics(ui, db, self.args)
            elif mode == "speakers":
                action_speakers(ui, db, self.args)
            elif mode == "play":
                action_play(ui, db, self.args)
            else:
                _log("Unknown mode: %s" % mode, level="error")
        finally:
            db.close()
