#!/usr/bin/env bash
# Launcher for the fake-camera bench rig: starts the virtual MJPEG camera on a
# folder of recorded frames, waits for it, then runs the app against a virtual
# config. One command, no phone, no USB.
#
# Usage:
#   ./hallofframe-fake.sh                 # defaults below
#   ./hallofframe-fake.sh --counter       # overlay a live ms counter (calibration)
#
# Env overrides:
#   HALL_OF_FRAME_CONFIG  virtual config passed to the app
#                         (default: ~/regatta-virtual/config.toml)
#   HALL_OF_FRAME_FEED    folder of JPEGs to serve
#                         (default: ~/regatta-virtual/feed)
#   HALL_OF_FRAME_SOURCE_DB
#                         event DB to build the feed from if it is missing
#                         (read-only; default: unset — the feed must already
#                         exist). This is NOT the app's database; the app's DB
#                         comes from HALL_OF_FRAME_CONFIG's data_root/event_name.
#   HALL_OF_FRAME_RACE    race id/race_no to build from (default: 28)
#   HALL_OF_FRAME_FPS     camera fps (default: 30) — must match [stream].assumed_fps
#   HALL_OF_FRAME_PORT    camera port (default: 8081) — must match [stream].url
#   HALL_OF_FRAME_VENV    python interpreter (default: <repo>/venv/bin/python)
#
# The recorded frames live OUTSIDE the repo; see TESTING.md §3 "Virtual feed".

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv_py="${HALL_OF_FRAME_VENV:-$here/venv/bin/python}"
if [ ! -x "$venv_py" ]; then
    echo "error: no virtualenv python at $venv_py (create it per INSTALL.md)" >&2
    exit 1
fi

config="${HALL_OF_FRAME_CONFIG:-$HOME/regatta-virtual/config.toml}"
feed="${HALL_OF_FRAME_FEED:-$HOME/regatta-virtual/feed}"
source_db="${HALL_OF_FRAME_SOURCE_DB:-}"
race="${HALL_OF_FRAME_RACE:-28}"
fps="${HALL_OF_FRAME_FPS:-30}"
port="${HALL_OF_FRAME_PORT:-8081}"

counter=()
if [ "${1:-}" = "--counter" ]; then
    counter+=(--counter)
fi

if [ ! -f "$config" ]; then
    echo "error: virtual config not found: $config" >&2
    echo "see TESTING.md §3 'Virtual feed' for how to create one." >&2
    exit 1
fi

# Build the feed on first use if the folder is missing or empty. The source DB
# is read-only and is NOT the app's database.
if [ ! -d "$feed" ] || [ -z "$(ls -A "$feed" 2>/dev/null)" ]; then
    if [ -n "$source_db" ] && [ -f "$source_db" ]; then
        echo "building feed from $source_db (race $race) -> $feed"
        "$venv_py" -m hallofframe.tools.build_feed \
            --db "$source_db" --race "$race" --crossings 6 --max-frames 200 \
            --out "$feed"
    else
        echo "error: feed '$feed' is empty; nothing to serve." >&2
        echo "build it first with a source DB (HALL_OF_FRAME_SOURCE_DB):" >&2
        echo "  $venv_py -m hallofframe.tools.build_feed --help" >&2
        exit 1
    fi
fi

echo "fake camera: $feed @ ${fps}fps on http://127.0.0.1:$port/video"
"$venv_py" -m hallofframe.tools.fake_camera \
    --folder "$feed" --fps "$fps" --port "$port" --loop \
    ${counter[@]+"${counter[@]}"} &
cam_pid=$!

cleanup() {
    kill "$cam_pid" 2>/dev/null || true
    wait "$cam_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for the camera to accept a connection (max ~5 s).
for _ in $(seq 1 50); do
    if "$venv_py" -c \
        'import socket,sys; s=socket.socket(); s.settimeout(0.2);
sys.exit(0 if s.connect_ex(("127.0.0.1",int(sys.argv[1])))==0 else 1)' \
        "$port" 2>/dev/null; then
        break
    fi
    sleep 0.1
done

echo "app: $config"
"$venv_py" -m hallofframe "$config"
