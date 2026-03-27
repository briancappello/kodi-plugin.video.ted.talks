#!/usr/bin/env python3
"""
Standalone CLI tool to build/update the TED catalog SQLite database.

This runs outside of Kodi and does not depend on any Kodi modules.
It uses the same db.py and talk_page.py modules as the addon, but
uses httpx + asyncio for fast parallel HTTP fetching.

Usage:
    python3 sync_catalog.py [output_path]
    python3 sync_catalog.py --no-enrich [output_path]

Requires: pip install httpx
"""

import os
import sys
import time
import asyncio
import argparse
import logging

import httpx

# Add the addon root to the Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resources.lib.model.db import TedDatabase
from resources.lib.model import talk_page
from resources.lib.model.talk_page import topic_label

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Silence httpx request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TED_API_URL = "https://zenith-prod-alt.ted.com/api/search"
TED_TALK_URL = "https://www.ted.com/talks/%s"

# Concurrency limits
API_CONCURRENCY = 20
ENRICH_CONCURRENCY = 30
HITS_PER_PAGE = 24


def _normalize_hit(hit, rank_offset=0, index=0):
    """Convert an API hit to a talk dict for the DB."""
    photos = hit.get("photos", [])
    thumb_url = None
    if photos:
        sizes = photos[0].get("photo_sizes", [])
        if sizes:
            by_ratio = {s.get("talkstar_aspect_ratio_id"): s for s in sizes}
            for ratio_id in [36, 2, 3]:
                if ratio_id in by_ratio:
                    thumb_url = by_ratio[ratio_id].get("url")
                    break
            if not thumb_url:
                thumb_url = max(sizes, key=lambda s: s.get("width", 0)).get("url")

    duration = hit.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (ValueError, TypeError):
        duration = None

    return {
        "object_id": hit.get("objectID", ""),
        "slug": hit.get("slug", ""),
        "title": (hit.get("title") or "").strip(),
        "duration": duration,
        "speakers_str": (hit.get("speakers") or "").strip(),
        "thumb_url": thumb_url,
        "api_rank": rank_offset + index,
    }


async def fetch_api_page(client, page):
    """Fetch one page from the TED search API."""
    payload = [
        {
            "indexName": "newest",
            "params": {
                "hitsPerPage": HITS_PER_PAGE,
                "page": page,
                "query": "",
            },
        }
    ]
    resp = await client.post(TED_API_URL, json=payload)
    resp.raise_for_status()
    return resp.json()["results"][0]


async def full_sync(db, concurrency=API_CONCURRENCY):
    """Fetch the entire TED catalog from the API using async HTTP."""
    logger.info("Starting catalog sync...")

    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch first page to get total
        first = await fetch_api_page(client, 0)
        total_pages = first["nbPages"]
        total_hits = first["nbHits"]
        logger.info("Found %d talks across %d pages", total_hits, total_pages)

        # Process first page
        _ingest_api_page(db, first, page=0)

        # Fetch remaining pages concurrently
        sem = asyncio.Semaphore(concurrency)
        completed = 1  # first page already done

        async def _fetch_and_ingest(page):
            nonlocal completed
            async with sem:
                try:
                    result = await fetch_api_page(client, page)
                    _ingest_api_page(db, result, page)
                except Exception as e:
                    logger.warning("Error on page %d: %s", page, e)
            completed += 1
            pct = completed * 100 // total_pages
            print(
                "\r  Catalog: %d/%d pages (%d%%)" % (completed, total_pages, pct),
                end="",
                flush=True,
            )

        tasks = [_fetch_and_ingest(p) for p in range(1, total_pages)]
        await asyncio.gather(*tasks)
        print()  # newline after progress

    db.mark_synced("last_full_sync")
    logger.info("Catalog sync complete: %d talks in DB", db.get_talk_count())


def _ingest_api_page(db, result, page):
    """Normalize and upsert one page of API results into the DB."""
    rank_offset = page * HITS_PER_PAGE
    talks = [
        _normalize_hit(h, rank_offset=rank_offset, index=i)
        for i, h in enumerate(result.get("hits", []))
    ]
    db.upsert_talks(talks)
    for talk in talks:
        if talk.get("speakers_str"):
            db.set_talk_speakers_from_api(talk["object_id"], talk["speakers_str"])
    db.conn.commit()


async def sync_topics(db):
    """Fetch topics from ted.com/topics and insert with proper labels and slugs."""
    logger.info("Syncing topics...")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get("https://www.ted.com/topics")
        resp.raise_for_status()

    import re

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', resp.text, re.S)
    if not m:
        logger.warning("Could not find __NEXT_DATA__ on topics page")
        return

    import json

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
            # Try to update existing topic by slug first (handles case mismatches)
            db.conn.execute("UPDATE topics SET name = ? WHERE slug = ?", (label, slug))
            # Insert if it doesn't exist yet
            db.conn.execute(
                """INSERT OR IGNORE INTO topics(name, slug) VALUES (?, ?)""",
                (label, slug),
            )
            count += 1
    db.conn.commit()
    logger.info("Synced %d topics", count)


async def enrich_talks(db, max_talks=None, concurrency=ENRICH_CONCURRENCY):
    """Enrich unenriched talks by fetching their talk pages with async HTTP."""
    query = "SELECT slug FROM talks WHERE enriched_at IS NULL"
    if max_talks:
        query += " LIMIT %d" % max_talks
    rows = db.conn.execute(query).fetchall()

    if not rows:
        logger.info("No talks to enrich")
        return

    total = len(rows)
    logger.info("Enriching %d talks (concurrency: %d)...", total, concurrency)
    enriched = 0
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=concurrency + 5),
    ) as client:

        async def _enrich_one(slug):
            nonlocal enriched, errors
            async with sem:
                try:
                    url = TED_TALK_URL % slug
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = talk_page.extract_enrichment(resp.text)
                        if data:
                            db.enrich_talk(slug, data)
                            enriched += 1
                        else:
                            errors += 1
                    else:
                        errors += 1
                        if resp.status_code == 429:
                            await asyncio.sleep(2)
                except Exception as e:
                    errors += 1
                    logger.debug("Failed to enrich %s: %s", slug, e)

            done = enriched + errors
            pct = done * 100 // total
            print(
                "\r  Enriching: %d/%d (%d%%) [errors: %d]" % (done, total, pct, errors),
                end="",
                flush=True,
            )

        tasks = [_enrich_one(row["slug"]) for row in rows]
        await asyncio.gather(*tasks)
        print()  # newline after progress

    logger.info("Enrichment complete: %d enriched, %d errors", enriched, errors)


async def async_main(args):
    db = TedDatabase(args.output)
    try:
        await full_sync(db)
        await sync_topics(db)
        await enrich_talks(
            db,
            max_talks=args.enrich_limit,
            concurrency=args.concurrency,
        )
        # Re-sync topics to fix labels from enrichment
        await sync_topics(db)
    finally:
        db.close()
    logger.info("Database written to %s", args.output)


def main():
    parser = argparse.ArgumentParser(
        description="Build/update the TED catalog SQLite database."
    )
    parser.add_argument(
        "output",
        nargs="?",
        default="./ted_catalog.db",
        help="Output database path (default: ./ted_catalog.db)",
    )
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=None,
        help="Max talks to enrich (default: all unenriched)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=ENRICH_CONCURRENCY,
        help="Number of concurrent HTTP requests for enrichment (default: %d)"
        % ENRICH_CONCURRENCY,
    )
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
