#!/usr/bin/env bash
# Launcher for HallOfFrame, the finish-line timer.
#
# Starts the app full-screen under systemd-inhibit so a lid-close or idle
# timeout cannot suspend the session mid-heat (INSTALL.md §7). Run it from a
# desktop session, not over SSH.
#
# Usage:
#   ./hallofframe.sh            launch under systemd-inhibit (preferred)
#   HALL_OF_FRAME_NO_INHIBIT=1 ./hallofframe.sh   launch without the inhibitor

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

venv_py="${HALL_OF_FRAME_VENV:-$here/venv/bin/python}"
if [ ! -x "$venv_py" ]; then
    echo "error: no virtualenv python at $venv_py (create it per INSTALL.md)" >&2
    exit 1
fi

if [ "${HALL_OF_FRAME_NO_INHIBIT:-0}" = "1" ]; then
    exec "$venv_py" -m hallofframe
fi

exec systemd-inhibit \
    --what=handle-lid-switch:sleep:idle \
    --who="hallofframe-timer" --why="finish-line timing in progress" \
    "$venv_py" -m hallofframe
