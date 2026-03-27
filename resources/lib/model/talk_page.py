"""
Extract data from individual TED talk pages via __NEXT_DATA__ JSON.

Used for two purposes:
1. Stream resolution at play time (stream URL, subtitles)
2. Metadata enrichment (description, published_at, topics, speaker details)

This module is designed to work both inside Kodi (using the Fetcher class)
and standalone (using requests or urllib directly).
"""

import re
import os
import json
import logging
import tempfile

logger = logging.getLogger(__name__)

TED_URL = "https://www.ted.com"

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.S)


def topic_label(name, slug=""):
    """
    Derive the display label for a topic from __NEXT_DATA__.

    The data has two patterns:
    - name="activism", slug="activism" → both lowercase, apply title case → "Activism"
    - name="AI", slug="ai" → name has intentional casing, keep as "AI"
    - name="Africa", slug="africa" → name has intentional casing, keep as "Africa"

    Rule: if name is all lowercase, apply title case. Otherwise keep as-is.

    Can be called with a topic_node dict or with separate name/slug strings.
    """
    if isinstance(name, dict):
        # Called with a topic node dict
        slug = name.get("slug", "")
        name = name.get("name", "")
    name = name.strip()
    if not name:
        return ""
    if name == name.lower():
        return name.title()
    return name


def _fetch_next_data(html):
    """Extract and parse the __NEXT_DATA__ JSON from a TED talk page HTML."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse __NEXT_DATA__: %s", e)
        return {}


def extract_enrichment(html):
    """
    Extract enrichment metadata from a TED talk page.

    Args:
        html: Raw HTML string of the talk page.

    Returns:
        dict with keys:
            description (str)
            published_at (str, ISO 8601)
            recorded_at (str, ISO 8601 date)
            view_count (int)
            topics (list of str)
            speakers (list of dict with name, slug, thumb_url, bio)
    """
    data = _fetch_next_data(html)
    page_props = data.get("props", {}).get("pageProps", {})
    video_data = page_props.get("videoData", {})

    if not video_data:
        return {}

    # Extract topics
    topics_raw = video_data.get("topics", {})
    if isinstance(topics_raw, dict):
        topic_nodes = topics_raw.get("nodes", [])
    elif isinstance(topics_raw, list):
        topic_nodes = topics_raw
    else:
        topic_nodes = []
    topics = [
        {"name": topic_label(t), "slug": t.get("slug", "")}
        for t in topic_nodes
        if t.get("name")
    ]

    # Extract speakers
    speakers_raw = video_data.get("speakers", {})
    if isinstance(speakers_raw, dict):
        speaker_nodes = speakers_raw.get("nodes", [])
    elif isinstance(speakers_raw, list):
        speaker_nodes = speakers_raw
    else:
        speaker_nodes = []

    speakers = []
    for s in speaker_nodes:
        speakers.append(
            {
                "name": s.get("firstname", "") + " " + s.get("lastname", "")
                if s.get("firstname")
                else video_data.get("presenterDisplayName", ""),
                "slug": s.get("slug", ""),
                "thumb_url": s.get("photoUrl", ""),
                "bio": s.get("whoTheyAre", ""),
            }
        )

    # If no speaker nodes but we have a presenter name
    if not speakers and video_data.get("presenterDisplayName"):
        speakers.append(
            {
                "name": video_data["presenterDisplayName"],
                "slug": "",
                "thumb_url": "",
                "bio": "",
            }
        )

    return {
        "description": video_data.get("description", ""),
        "published_at": video_data.get("publishedAt", ""),
        "recorded_at": video_data.get("recordedOn", ""),
        "view_count": video_data.get("viewedCount"),
        "topics": topics,
        "speakers": speakers,
    }


def extract_stream_info(html, subtitle_languages=None):
    """
    Extract stream URL, subtitles, and video info from a TED talk page.
    Used at play time for setResolvedUrl.

    Args:
        html: Raw HTML string of the talk page.
        subtitle_languages: List of preferred ISO 639-1 language codes
                            (e.g. ['en', 'es']), or None to disable subtitles.

    Returns:
        dict with keys:
            stream (str or None): HLS or MP4 stream URL
            subtitles (str or None): Subtitle URL or local file path
            title (str)
            duration (int, seconds)
            description (str)
            date (str, DD.MM.YYYY format for Kodi)
            aired (str, YYYY-MM-DD)
            dateadded (str, YYYY-MM-DD HH:MM:SS)
            genre (str, event name)
            thumb (str, thumbnail URL)
            speaker_name (str)
    """
    data = _fetch_next_data(html)
    page_props = data.get("props", {}).get("pageProps", {})
    video_data = page_props.get("videoData", {})

    if not video_data:
        return {"stream": None, "subtitles": None}

    player_data_str = video_data.get("playerData", "{}")
    try:
        player_data = (
            json.loads(player_data_str)
            if isinstance(player_data_str, str)
            else player_data_str
        )
    except (json.JSONDecodeError, ValueError):
        player_data = {}

    # Stream URL
    stream = None
    hls = player_data.get("resources", {}).get("hls", {})
    stream = hls.get("stream")
    if stream:
        # Remove query string (default intro messes up subtitle timing)
        stream = str(stream).split("?")[0]
    else:
        # Try h264 fallback
        h264 = player_data.get("resources", {}).get("h264", [])
        if h264:
            stream = h264[0].get("file")

    if not stream:
        # Check for external YouTube video
        external = player_data.get("external", {})
        if external.get("service") == "YouTube":
            # Return YouTube URL — caller can resolve with youtube_dl if available
            stream = "https://www.youtube.com/watch?v=%s" % external.get("code", "")

    # Subtitles
    subtitles = None
    if subtitle_languages:
        available = player_data.get("languages", [])
        available_codes = [l.get("languageCode", "") for l in available]
        # Pick first preferred language that's available
        chosen = None
        for lang in subtitle_languages:
            if lang in available_codes:
                chosen = lang
                break
        if not chosen and "en" in available_codes:
            chosen = "en"

        if chosen:
            talk_id = player_data.get("id")
            if talk_id:
                subtitles = "%s/talks/subtitles/id/%s/lang/%s" % (
                    TED_URL,
                    talk_id,
                    chosen,
                )

        # Try to get webvtt subtitles from HLS metadata
        metadata_url = hls.get("metadata")
        if metadata_url and chosen:
            metadata_url = str(metadata_url).split("?")[0]
            # Note: caller needs to fetch this URL and pass the JSON
            # We store it for the caller to use
            subtitles = {
                "type": "metadata",
                "metadata_url": metadata_url,
                "language": chosen,
                "fallback": subtitles,
            }

    # Date formatting
    recorded_on = video_data.get("recordedOn") or ""
    published_at = video_data.get("publishedAt") or ""

    def format_date_kodi(iso_date):
        """Convert YYYY-MM-DD to DD.MM.YYYY for Kodi's date info label."""
        if not iso_date:
            return ""
        parts = iso_date.split("T")[0].split("-")
        if len(parts) == 3:
            return ".".join(reversed(parts))
        return iso_date

    def format_dateadded(iso_datetime):
        """Convert ISO 8601 to YYYY-MM-DD HH:MM:SS for Kodi."""
        if not iso_datetime:
            return ""
        return iso_datetime.replace("T", " ").replace("Z", "")[:19]

    # Artwork
    images = video_data.get("primaryImageSet", [])
    thumb = images[0].get("url", "") if images else ""

    # Speaker
    speaker_name = video_data.get("presenterDisplayName", "")

    return {
        "stream": stream,
        "subtitles": subtitles,
        "title": video_data.get("title", ""),
        "duration": video_data.get("duration", 0),
        "description": video_data.get("description", ""),
        "date": format_date_kodi(recorded_on),
        "aired": recorded_on.split("T")[0] if recorded_on else "",
        "dateadded": format_dateadded(published_at) if published_at else "",
        "genre": player_data.get("event", ""),
        "thumb": thumb,
        "speaker_name": speaker_name,
        "slug": video_data.get("slug", ""),
    }


def resolve_subtitles(fetch_fn, subtitle_info, cache_dir=None):
    """
    Resolve subtitles to a playable URL or local file.

    If subtitle_info is a string, it's already a URL (TED subtitle API).
    If it's a dict with type="metadata", fetch the metadata URL to find
    the webvtt subtitle URL. If that fails, fall back to the TED API URL
    which returns JSON that needs conversion to SRT.

    Args:
        fetch_fn: Callable that takes a URL and returns response text.
        subtitle_info: String URL or dict from extract_stream_info.
        cache_dir: Directory for cached SRT files. Uses temp dir if None.

    Returns:
        str: URL or file path to subtitles, or None.
    """
    if subtitle_info is None:
        return None

    if isinstance(subtitle_info, str):
        # It's a TED subtitle API URL — needs JSON-to-SRT conversion
        return _cache_ted_subtitles(fetch_fn, subtitle_info, cache_dir)

    if isinstance(subtitle_info, dict) and subtitle_info.get("type") == "metadata":
        # Try to get webvtt URL from HLS metadata
        try:
            metadata = json.loads(fetch_fn(subtitle_info["metadata_url"]))
            lang = subtitle_info.get("language", "en")
            for s in metadata.get("subtitles", []):
                if s.get("code", "") == lang and s.get("webvtt"):
                    return s["webvtt"]
        except Exception:
            pass

        # Fall back to TED API
        fallback = subtitle_info.get("fallback")
        if fallback:
            return _cache_ted_subtitles(fetch_fn, fallback, cache_dir)

    return None


def _cache_ted_subtitles(fetch_fn, url, cache_dir=None):
    """
    Fetch TED subtitle JSON and convert to SRT format, cached to a file.
    """
    if cache_dir is None:
        cache_dir = tempfile.gettempdir()

    cache_file = os.path.join(cache_dir, "TED_talk_subs.srt")

    def _format_time(ms):
        hours = int(ms / 3600000)
        minutes = int((ms / 60000) % 60)
        seconds = int((ms / 1000) % 60)
        millis = int(ms % 1000)
        return "%02d:%02d:%02d,%03d" % (hours, minutes, seconds, millis)

    try:
        raw = fetch_fn(url)
        data = json.loads(raw) if raw else None

        with open(cache_file, "w", encoding="utf-8") as fp:
            if data:
                for i, caption in enumerate(data.get("captions", [])):
                    start = caption["startTime"]
                    end = start + caption["duration"]
                    content = caption["content"]
                    fp.write(
                        "%d\n%s --> %s\n%s\n\n"
                        % (i + 1, _format_time(start), _format_time(end), content)
                    )
        return cache_file if data else None
    except Exception as e:
        logger.error("Failed to cache subtitles: %s", e)
        return None
