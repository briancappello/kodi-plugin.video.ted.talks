"""
Client for the TED Talks search API (Algolia-backed).

Endpoint: POST https://zenith-prod-alt.ted.com/api/search
No authentication required.

This module is designed to work both inside Kodi (using the requests
module from script.module.requests) and standalone for CLI tools.
"""

import json
import os
import time
import logging

try:
    import requests
except ImportError:
    # Fallback for environments without the requests module
    import urllib.request

    requests = None

logger = logging.getLogger(__name__)

API_URL = "https://zenith-prod-alt.ted.com/api/search"
DEFAULT_HITS_PER_PAGE = 24

# Use system CA bundle if available, otherwise fall back to certifi
_CA_BUNDLE = None
for _path in ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]:
    if os.path.exists(_path):
        _CA_BUNDLE = _path
        break

# Photo size preferences by aspect ratio ID (16:9 is best for Kodi)
# Aspect ratio IDs observed: 2 = 1920x1080, 3 = 2400x1800, 36 = 1350x675
_PREFERRED_ASPECT_RATIOS = [36, 2, 3]  # 2:1, 16:9, 4:3


def _post_json(url, payload):
    """POST JSON and return parsed response. Works with or without requests."""
    body = json.dumps(payload).encode("utf-8")
    if requests is not None:
        resp = requests.post(
            url, json=payload, timeout=(6, 12), verify=_CA_BUNDLE or True
        )
        resp.raise_for_status()
        return resp.json()
    else:
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _pick_thumbnail(photos):
    """
    Pick the best thumbnail URL from the API's photos structure.
    Returns a URL string or None.
    """
    if not photos:
        return None
    for photo_set in photos:
        sizes = photo_set.get("photo_sizes", [])
        if not sizes:
            continue
        # Try preferred aspect ratios first
        by_ratio = {s.get("talkstar_aspect_ratio_id"): s for s in sizes}
        for ratio_id in _PREFERRED_ASPECT_RATIOS:
            if ratio_id in by_ratio:
                return by_ratio[ratio_id].get("url")
        # Fallback: pick the widest image
        best = max(sizes, key=lambda s: s.get("width", 0))
        return best.get("url")
    return None


def _normalize_hit(hit, rank_offset=0, index=0):
    """
    Convert an API hit dict into a normalized talk dict suitable for
    database insertion.
    """
    return {
        "object_id": hit.get("objectID", ""),
        "slug": hit.get("slug", ""),
        "title": hit.get("title", "").strip(),
        "duration": _parse_duration(hit.get("duration")),
        "speakers_str": (hit.get("speakers") or "").strip(),
        "thumb_url": _pick_thumbnail(hit.get("photos")),
        "api_rank": rank_offset + index,
    }


def _parse_duration(val):
    """Parse duration from the API (string of seconds with decimals)."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def search(
    query="",
    page=0,
    hits_per_page=DEFAULT_HITS_PER_PAGE,
    index_name="newest",
    facet_filters=None,
):
    """
    Search the TED API.

    Args:
        query: Search string (empty = browse all).
        page: Zero-based page number.
        hits_per_page: Results per page.
        index_name: Algolia index name ("newest" for browse-by-date).
        facet_filters: Optional list of facet filter strings,
                       e.g. ["tags:science"].

    Returns:
        dict with keys:
            hits: list of normalized talk dicts
            total: total number of matching talks
            page: current page number
            pages: total number of pages
    """
    params = {
        "hitsPerPage": hits_per_page,
        "page": page,
        "query": query,
    }
    if facet_filters:
        params["facetFilters"] = facet_filters

    payload = [{"indexName": index_name, "params": params}]

    try:
        data = _post_json(API_URL, payload)
    except Exception as e:
        logger.error("TED API request failed: %s", e)
        raise

    result = data.get("results", [{}])[0]
    hits_raw = result.get("hits", [])
    rank_offset = page * hits_per_page

    hits = [
        _normalize_hit(h, rank_offset=rank_offset, index=i)
        for i, h in enumerate(hits_raw)
    ]

    return {
        "hits": hits,
        "total": result.get("nbHits", 0),
        "page": result.get("page", page),
        "pages": result.get("nbPages", 0),
    }


def get_all_tags(max_values=500):
    """
    Fetch all available tags (topics) from the API via faceting.

    Returns:
        dict mapping tag name to talk count, sorted by count descending.
    """
    params = {
        "hitsPerPage": 0,
        "page": 0,
        "query": "",
        "facets": ["tags"],
        "maxValuesPerFacet": max_values,
    }
    payload = [{"indexName": "newest", "params": params}]

    data = _post_json(API_URL, payload)
    result = data.get("results", [{}])[0]
    tags = result.get("facets", {}).get("tags", {})

    return dict(sorted(tags.items(), key=lambda x: -x[1]))


def fetch_all_talks(
    hits_per_page=DEFAULT_HITS_PER_PAGE,
    max_pages=None,
    progress_callback=None,
    delay=0.1,
):
    """
    Generator that paginates through the entire TED catalog.

    Args:
        hits_per_page: Results per page.
        max_pages: Stop after this many pages (None = all).
        progress_callback: Called with (page, total_pages) after each page.
        delay: Seconds to wait between API calls.

    Yields:
        Normalized talk dicts, one at a time.
    """
    page = 0
    total_pages = None

    while True:
        if max_pages is not None and page >= max_pages:
            return

        result = search(page=page, hits_per_page=hits_per_page)

        if total_pages is None:
            total_pages = result["pages"]

        for hit in result["hits"]:
            yield hit

        if progress_callback:
            progress_callback(page, total_pages)

        page += 1
        if page >= result["pages"]:
            return

        if delay > 0:
            time.sleep(delay)
