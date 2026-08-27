#!/usr/bin/env bash
# Launcher for the HallOfFrame results web server.
#
# Serves recorded races over HTTP from a SEPARATE process with its own
# read-only SQLite connection, so viewer load never perturbs the evdev timing
# thread (README "Live results", INSTALL.md §7). Runs alongside the app and
# reads the same database.
#
# Usage:
#   ./hallofframe-web.sh                 start with the default config
#   ./hallofframe-web.sh /path/to/config.toml   start with a specific config

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

venv_py="${HALL_OF_FRAME_VENV:-$here/venv/bin/python}"
if [ ! -x "$venv_py" ]; then
    echo "error: no virtualenv python at $venv_py (create it per INSTALL.md)" >&2
    exit 1
fi

args=()
if [ "$#" -gt 0 ]; then
    args+=(--config "$1")
fi

exec "$venv_py" -m hallofframe.web "${args[@]}"