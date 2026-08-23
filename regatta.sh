#!/usr/bin/env bash
# Launcher for the Regatta Finish-Line Timer.
#
# Starts the app full-screen under systemd-inhibit so a lid-close or idle
# timeout cannot suspend the session mid-heat (INSTALL.md §7). Run it from a
# desktop session, not over SSH.
#
# Usage:
#   ./regatta.sh            launch under systemd-inhibit (preferred)
#   REGATTA_NO_INHIBIT=1 ./regatta.sh   launch without the inhibitor

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

venv_py="${REGATTA_VENV:-$here/venv/bin/python}"
if [ ! -x "$venv_py" ]; then
    echo "error: no virtualenv python at $venv_py (create it per INSTALL.md)" >&2
    exit 1
fi

if [ "${REGATTA_NO_INHIBIT:-0}" = "1" ]; then
    exec "$venv_py" -m regatta_timer
fi

exec systemd-inhibit \
    --what=handle-lid-switch:sleep:idle \
    --who="regatta-timer" --why="finish-line timing in progress" \
    "$venv_py" -m regatta_timer
