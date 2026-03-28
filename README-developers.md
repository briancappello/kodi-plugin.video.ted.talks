# Developer Guide

## Development environment

The recommended setup uses [kodi-podman](https://github.com/briancappello/kodi-podman) to run Kodi in a container with the addon bind-mounted for live editing:

```bash
git clone https://github.com/briancappello/kodi-podman.git
cd kodi-podman
git clone <this-repo> plugin.video.ted.talks
make install-addon ID=plugin.video.ted.talks
```

Python changes take effect on the next addon invocation (no restart needed). Changes to `addon.xml` or `service.py` require `make restart`.

## Project structure

```
plugin.video.ted.talks/
├── addon.xml                    # Addon metadata and dependencies
├── default.py                   # Plugin entry point (URL routing)
├── service.py                   # Background sync service
├── sync_catalog.py              # Host-side async catalog builder
├── resources/
│   ├── lib/
│   │   ├── ted_talks.py         # Plugin UI controller (all actions)
│   │   ├── settings.py          # Kodi settings wrapper
│   │   └── model/
│   │       ├── db.py            # SQLite database layer
│   │       ├── ted_api.py       # TED Algolia search API client
│   │       ├── talk_page.py     # __NEXT_DATA__ extraction
│   │       ├── fetcher.py       # HTTP client with retry
│   │       └── arguments.py     # URL parameter parsing
│   ├── data/
│   │   └── ted_catalog.db       # Bundled catalog (in dist zip only)
│   ├── language/                # Localized strings
│   └── settings.xml             # Kodi settings definition
├── .distignore                  # Files excluded from dist zip
└── dist-hook.sh                 # Bundles the catalog DB into dist zip
```

## Running tests

```bash
# Unit tests (offline, fast)
python3 -m unittest resources.lib.model.db_test -v
python3 -m unittest resources.lib.model.talk_page_test -v

# Tests that hit the live TED API/website (slower)
python3 -m unittest resources.lib.model.ted_api_test -v

# All tests
python3 -m unittest discover -s resources -p "*_test.py" -v
```

## Building the catalog database

The `sync_catalog.py` script runs on the host (not inside Kodi) and uses `asyncio` + `httpx` for fast parallel fetching:

```bash
pip install httpx
python3 sync_catalog.py ted_catalog.db
```

This takes about 3 minutes and:

1. Fetches the full talk catalog from the TED search API (~7500 talks)
2. Syncs topics from ted.com/topics (384 topics with proper labels)
3. Enriches all talks by fetching individual talk pages for descriptions, topics, view counts
4. Re-syncs topic labels to fix casing from enrichment
5. Syncs TED series (14 series with seasons/episodes)
6. Syncs playlists via GraphQL (715 playlists with topic associations)

Or via the kodi-podman Makefile:

```bash
make sync-ted
```

## Building a release

From the kodi-podman directory:

```bash
make sync-ted    # Build/refresh the catalog database
make dist        # Creates plugin.video.ted.talks-X.Y.Z.zip
```

The zip includes all addon code, assets, and the bundled catalog database. The `.distignore` file controls what's excluded (dev files, tests, git history, etc.).

## Releasing

1. Run tests
2. Test in Kodi via kodi-podman
3. Bump version in `addon.xml`
4. Update `<news>` in `addon.xml`
5. Run `make sync-ted && make dist`
6. Test the zip install on a clean Kodi instance
7. Tag and push

## Architecture notes

### Data flow

- **Plugin invocations** (`default.py` → `ted_talks.py`): stateless, short-lived. Opens the SQLite DB, queries it, builds a directory listing, exits.
- **Background service** (`service.py`): long-lived. Syncs the catalog, enriches talks, monitors playback for favorites/watched tracking.
- **Host sync** (`sync_catalog.py`): one-shot. Builds a complete database using async HTTP for speed.

### Concurrency safety

The plugin and service run in separate threads with separate SQLite connections. WAL journal mode allows concurrent reads + one writer. The service's enrichment worker threads only do HTTP fetching -- all DB writes happen on the main service thread.

### SSL certificates

The Kodi `script.module.requests` addon bundles an outdated `certifi` CA bundle. The addon uses the system CA bundle (`/etc/ssl/certs/ca-certificates.crt`) when available, falling back to certifi.

### YouTube-hosted talks

Some TED talks are hosted on YouTube instead of TED's own CDN. The addon resolves these via `youtube_dl` (from `script.module.youtube.dl` if available), then `yt-dlp`, then falls back to delegating to the `plugin.video.youtube` Kodi addon.
