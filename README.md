<h1>
  <img src="hallofframe/assets/logo.svg" width="40" height="40" align="left" alt="" />
  HallOfFrame
</h1>

**HallOfFrame Finish-Line Timer** — a single-operator finish-line timer for rowing
regattas. A phone camera streams MJPEG over a USB tunnel; the operator presses
keys on the laptop to record boat crossing times.

- **Millisecond-level timing** driven by **evdev kernel timestamps** — not Qt
  key events or `time.time()`.
- Each crossing is attached to the saved photo nearest its recorded time for
  jury review.
- Races persist to SQLite and continuous footage is archived for recovery.
- The trigger path never touches disk — nothing timing-critical blocks.

## Features

- Arm / start / record crossings / end race / quit, all keyboard-driven
  (`Ctrl+S`, `ENTER`, `SPACE`, `F12`).
- Deferred image selection so after-window frames exist before a photo is
  chosen.
- Latency calibration (water mode vs. screen mode).
- CSV export, a whole-database HTML results page (`D`, photos included), and
  per-race continuous archive.
- `F1` About / diagnostics screen: version, environment, paths, key reference,
  and a copyable support bundle for bug reports.
- Structured JSONL logging and full-screen launch under `systemd-inhibit`.

## Requirements

See [INSTALL.md](INSTALL.md) for the authoritative environment setup.

- Linux with a USB-tunneled MJPEG camera and a grabbed evdev input device.
- Python 3 with a virtualenv (see `environment-lock.txt`).
- PySide6 for the UI.

## Quick start

```bash
# create the venv and install deps (see INSTALL.md §2–§3)
./hallofframe.sh                 # launch full-screen under systemd-inhibit
# or
HALL_OF_FRAME_NO_INHIBIT=1 ./hallofframe.sh   # launch without the inhibitor
```

Race lifecycle (the keyboard is grabbed during a race):

| Action          | Key            |
|-----------------|----------------|
| Arm             | `Ctrl+S`       |
| Start race (`t0`) | `ENTER`     |
| Record crossing | `SPACE`        |
| End race        | `F12`          |
| About / diagnostics | `F1`       |
| Quit            | `Ctrl+Q`       |

## Configuration

The app never writes its config — copy the templates and hand-edit:

```bash
mkdir -p ~/regatta-data
cp hallofframe.example.toml ~/regatta-data/config.toml   # full example, every section
cp races.example.csv ~/regatta-data/races.csv        # the race roster for the dropdown
$EDITOR ~/regatta-data/config.toml
```

The values in `hallofframe.example.toml` are the application's built-in defaults, so a
straight copy runs unchanged. Highlights:

- `crossing_keycodes` / `start_keycodes` / `end_keycodes` — evdev keycodes
  (`SPACE`=57, `ENTER`=28, `F12`=88) that record crossings / start / end a race.
- `grab_device` — the evdev device to grab for timing.
- `[paths] event_name` — the competition/event name; generated data (DB, logs,
  roster CSV) carries it.
- `[timing] viewing_mode` — `"water"` or `"screen"` (selects the latency formula).
- `[races] csv_path` — the roster file shown in the Ready-screen dropdown.

Data lives in `~/regatta-data/`:

- `{event_name}.db` — SQLite (`race`, `capture`, `capture_frame`)
- `races/<Race-YYYYmmdd-HHMM>/` — capture images + per-race `archive/`
- `logs/{event_name}-app.jsonl` — structured log
- `calibration.json` — latency result from Calibrate
- `{event_name}_races.csv` — the race roster (one race per row: `race_no`,
  `heat_no`, `name`). On the Ready screen the dropdown gray out races already
  recorded in `{event_name}.db` (still selectable to overwrite) and defaults to
  the next not-yet-recorded one.

## Brand

The mark is a camera frame with viewfinder corners and the red finish line
through its centre — `hallofframe/assets/logo.svg`, drawn from the UI tokens in
`hallofframe/ui/styles.py` (`TEXT_PRIMARY` #f2f6f8 on `PANEL_BORDER` #2c3942,
finish line `RED_TEXT` #ff5a42). It reads down to 32 px. The wordmark is IBM
Plex Mono SemiBold, set as `HallOf` + `Frame` with the second word in the finish
-line red. The About screen (`F1`) is the only place the lockup appears in-app.

## Tests

```bash
./venv/bin/python -m pytest -q
```

## Documentation

| File | Purpose |
|---|---|
| [hallofframe-finish-timer-spec.md](hallofframe-finish-timer-spec.md) | The build spec (timing model, components, UI, config). |
| [INSTALL.md](INSTALL.md) | Environment setup, `systemd-inhibit`, launch. |
| [TESTING.md](TESTING.md) | Per-stage test procedures. |
| [system-environment.md](system-environment.md) | Hardware/OS audit. |
| [hallofframe.example.toml](hallofframe.example.toml) | Template — copy to `~/regatta-data/config.toml`. |
| [races.example.csv](races.example.csv) | Template — race roster for the dropdown. |
| [AGENTS.md](AGENTS.md) | Onboarding notes for AI assistants. |
| `reports/` | Test/verification reports. |

## Package layout

```
hallofframe/
  main.py          Entry point: wires transport, reader, buffer, storage,
                   controller, trigger, Qt UI, and the worker→Qt bridge.
  controller.py    Capture orchestration and deferred image selection.
  trigger.py       evdev key-listener (kernel timestamps, debounce, grab).
  transport.py     iproxy USB tunnel lifecycle (host→device, TCP-only).
  mjpeg.py         MJPEG parse loop; emits timestamped Frame objects.
  framebuffer.py   Timestamped ring buffer.
  storage.py       SQLite persistence (WAL, foreign_keys ON).
  archive.py       Per-race footage writer with disk-space handling.
  export.py        CSV export; whole-database HTML results page (`D`).
  config.py        config.toml load + defaults.
  log.py           Structured JSONL logging.
  calibration.py   Latency calibration helpers.
  assets/          Application logo.
  ui/              PySide6 widgets.
  tools/           Soak-test utility.
```

## License

Copyright © 2026 Isael Diaz. All rights reserved.
