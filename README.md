# plugin.video.ted.talks

A Kodi addon for browsing and watching [TED Talks](https://www.ted.com/). Features a local SQLite catalog of all ~7500 TED talks with full-text search, topic and speaker browsing, playlists, series, and a background sync service that keeps the catalog up to date.

Requires Kodi 21 (Omega) or later.

## Features

- **Newest Talks** -- browse the latest TED talks
- **Search** -- full-text search across titles, descriptions, and speaker names (FTS5)
- **Topics** -- drill-down topic filtering with intersection support (e.g. "Science + Brain")
- **Speakers** -- browse talks by speaker
- **Series** -- TED original series with season/episode organization
- **Playlists** -- 700+ curated TED playlists, filterable by topic
- **Favorites** -- bookmark talks after watching
- **Watched tracking** -- resume playback from where you left off
- **Background sync** -- catalog updates automatically every 12 hours
- **YouTube fallback** -- talks hosted on YouTube are resolved via youtube_dl

## Installation

### From zip file

1. Download the latest `.zip` from the releases page
2. In Kodi: **Settings > Add-ons > Install from zip file** > select the zip
3. Kodi will auto-install the `script.module.requests` dependency
4. The addon ships with a pre-built catalog database -- browsing works immediately

### Manual installation

Copy the `plugin.video.ted.talks` directory to your Kodi addons folder:

- Linux: `~/.kodi/addons/`
- macOS: `~/Library/Application Support/Kodi/addons/`
- Windows: `%APPDATA%\Kodi\addons\`

Then enable it in Kodi: **Settings > Add-ons > My add-ons > Video add-ons > TED Talks > Enable**.

## How it works

### Architecture

The addon uses a local SQLite database as the primary data source for all browsing. The database is populated from two sources:

1. **TED Search API** (Algolia) -- provides the talk catalog (title, slug, speaker, duration, thumbnail)
2. **TED talk pages** (`__NEXT_DATA__` JSON) -- provides enrichment data (description, published date, topics, view count, stream URLs)

Video playback always resolves stream URLs fresh from the talk page at play time, since HLS/MP4 URLs are ephemeral.

### Background service

A Kodi service (`service.py`) runs at startup and:

- **Cold start** (no database): fetches the full catalog (~7500 talks), topics, series, then enriches all talks by fetching individual talk pages
- **Every 12 hours**: incremental sync of newest talks, topic refresh, and enrichment of any unenriched talks

### Database schema

- `talks` -- all TED talks with metadata, enrichment data, favorites, and watched state
- `speakers` -- speaker names and bios
- `topics` -- topic labels and slugs
- `series` -- both TED series and curated playlists (distinguished by `type` column)
- `talks_fts` -- FTS5 virtual table for full-text search
- Junction tables: `talk_speakers`, `talk_topics`, `talk_series`, `series_topics`

## Settings

**Enable subtitles**: Show subtitles during playback. Defaults to the current Kodi language.

**Custom language code**: Set a custom ISO 639-1 language code. Supports comma-separated lists (e.g. `pt-br,pt,en`) -- the first available language is used.

## Development

The recommended development setup uses [kodi-podman](https://github.com/briancappello/kodi-podman), which runs Kodi in a container with the addon bind-mounted for live code editing:

```bash
git clone https://github.com/briancappello/kodi-podman.git
cd kodi-podman
git clone <this-repo> plugin.video.ted.talks
make install-addon ID=plugin.video.ted.talks
```

### Building the catalog

The `sync_catalog.py` script builds the SQLite database on the host using `asyncio` + `httpx` for fast parallel fetching:

```bash
pip install httpx
python3 sync_catalog.py ted_catalog.db
```

This fetches and enriches all ~7500 talks in about 3 minutes. The resulting database can be bundled with the addon for distribution.

Options:

| Flag | Description |
|------|-------------|
| `--enrich-limit N` | Enrich only N talks (useful for testing) |
| `--concurrency N` | Number of parallel HTTP requests (default: 30) |

### Running tests

```bash
cd plugin.video.ted.talks
python3 -m unittest resources.lib.model.db_test -v
python3 -m unittest resources.lib.model.ted_api_test -v
python3 -m unittest resources.lib.model.talk_page_test -v
```

## Credits

Originally created by rwparris2. Maintained by moreginger, kevwag, kodaksmith, and demitrios. v6.0 rewrite by briancappello.

## License

See [LICENSE.txt](LICENSE.txt).
