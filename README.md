# Regatta Finish-Line Timer

A single-operator **finish-line timer** for rowing regattas. A phone camera
streams MJPEG over a USB tunnel; the operator presses keys on the laptop to
record boat crossing times.

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
- CSV export and per-race continuous archive.
- Structured JSONL logging and full-screen launch under `systemd-inhibit`.

## Requirements

See [INSTALL.md](INSTALL.md) for the authoritative environment setup.

- Linux with a USB-tunneled MJPEG camera and a grabbed evdev input device.
- Python 3 with a virtualenv (see `environment-lock.txt`).
- PySide6 for the UI.

## Quick start

```bash
# create the venv and install deps (see INSTALL.md §2–§3)
./regatta.sh                 # launch full-screen under systemd-inhibit
# or
REGATTA_NO_INHIBIT=1 ./regatta.sh   # launch without the inhibitor
```

Race lifecycle (the keyboard is grabbed during a race):

| Action          | Key            |
|-----------------|----------------|
| Arm             | `Ctrl+S`       |
| Start race (`t0`) | `ENTER`     |
| Record crossing | `SPACE`        |
| End race        | `F12`          |
| Quit            | `Ctrl+Q`       |

## Configuration

Hand-edit `~/regatta-data/config.toml` (the app never writes it):

- `crossing_keycodes` (default `SPACE`=57)
- `start_keycodes` (default `ENTER`=28)
- `end_keycodes` (default `F12`=88)
- `grab_device` — the evdev device to grab.

Data lives in `~/regatta-data/`:

- `regatta.db` — SQLite (`race`, `capture`, `capture_frame`)
- `races/<Race-YYYYmmdd-HHMM>/` — capture images + per-race `archive/`
- `logs/regatta-app.jsonl` — structured log
- `calibration.json` — latency result from Calibrate
- `races.xlsx` — the race-name roster (one name per row in column A). On the
  Ready screen the dropdown gray out races already recorded in `regatta.db`
  (still selectable to overwrite) and defaults to the next not-yet-recorded one.

## Tests

```bash
./venv/bin/python -m pytest -q
```

## Documentation

| File | Purpose |
|---|---|
| [regatta-finish-timer-spec.md](regatta-finish-timer-spec.md) | The build spec (timing model, components, UI, config). |
| [INSTALL.md](INSTALL.md) | Environment setup, `systemd-inhibit`, launch. |
| [TESTING.md](TESTING.md) | Per-stage test procedures. |
| [system-environment.md](system-environment.md) | Hardware/OS audit. |
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
  export.py        CSV export.
  config.py        config.toml load + defaults.
  log.py           Structured JSONL logging.
  calibration.py   Latency calibration helpers.
  ui/              PySide6 widgets.
  tools/           Soak-test utility.
```

## License

Copyright © 2026 Isael Diaz. All rights reserved.
