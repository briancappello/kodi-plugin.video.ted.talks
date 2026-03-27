#!/bin/bash
# dist-hook.sh — called by `make dist` after copying addon files.
# $1 = path to the staged addon directory (inside dist/)
# $2 = kodi data directory
#
# Bundles the pre-built TED catalog database into the addon zip.

set -euo pipefail

DIST_ADDON="$1"
DATA_DIR="$2"
DB_SOURCE="$DATA_DIR/.kodi/userdata/addon_data/plugin.video.ted.talks/ted_catalog.db"

if [ -f "$DB_SOURCE" ]; then
    mkdir -p "$DIST_ADDON/resources/data"
    podman unshare cp "$DB_SOURCE" "$DIST_ADDON/resources/data/ted_catalog.db"
    echo "  Bundled catalog DB ($(du -h "$DIST_ADDON/resources/data/ted_catalog.db" | cut -f1))"
else
    echo "  WARNING: No catalog DB found. Run 'make sync-ted' first for a bundled DB."
fi
