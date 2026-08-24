# AGENTS.md — Regatta Finish-Line Timer

Onboarding doc for AI coding assistants (analogous to `CLAUDE.md`). Read this
first, then read the spec before touching timing/trigger code.

## Project goal

A single-operator **finish-line timer** for rowing regattas. A phone camera
streams MJPEG over a USB tunnel; the operator presses keys/buttons on the
laptop to record boat crossing times. The system must:

- Time each crossing with **millisecond-level accuracy**, driven by **evdev
  kernel timestamps** (not Qt key events).
- Attach the saved photo nearest each recorded time for jury review.
- Persist races to SQLite and archive continuous footage for recovery.
- Never block the trigger path: nothing timing-critical touches disk.

The authoritative requirements are in **`regatta-finish-timer-spec.md`**
(v1.2). Several "obvious" design alternatives (OpenCV `VideoCapture`, RTSP,
`time.time()`) break the timing accuracy — **do not swap them without reading
the spec's reasoning first** (see the "How to use this document" note and §3).

## Companion documents (read before coding)

| File | Purpose |
|---|---|
| `regatta-finish-timer-spec.md` | The build spec. §5 timing model, §6 components, §7 UI, §8 config, §12 order. Read §3, §5, §6 before touching trigger/timing code. |
| `INSTALL.md` | Authoritative environment setup (deps, systemd-inhibit, launch). |
| `TESTING.md` | Per-stage test procedures + file-based failure-report protocol. |
| `system-environment.md` | Hardware/OS audit the design was revised against. |
| `reports/` | Test/verification reports. |

## Repo layout

```
hallofframe/
  main.py            Entry point: wires transport, reader, buffer, storage,
                     controller, trigger, Qt UI, and the worker→Qt signal bridge.
  controller.py      Capture orchestration: start_race / end_race / record_crossing,
                     deferred image selection, calibration validation (delta).
  trigger.py         evdev key-listener (kernel timestamps, debounce, device grab).
  transport.py       iproxy USB tunnel lifecycle (host→device, TCP-only).
  mjpeg.py           MJPEG parse loop; emits timestamped Frame objects.
  framebuffer.py     Timestamped ring buffer; window(target, before, after).
  storage.py         SQLite persistence (WAL, foreign_keys ON), schema + migrations.
  archive.py         Continuous per-race footage writer with disk-space handling.
  export.py          CSV export; format_elapsed().
  config.py          config.toml load + defaults (never writes the file).
  log.py             Structured JSONL logging.
  calibration.py     Latency calibration helpers.
  ui/                PySide6 widgets: main_window, capture_list, preview_widget,
                     calibration_dialog.
  tools/ingest_soak.py  Soak-test utility for the ingest path.
tests/               pytest suites (controller, export, framebuffer, mjpeg).
```

## Key architectural rules

- **Timestamps are monotonic kernel times from evdev** (`t_press`); `t0` and
  every crossing come from the same clock domain. Qt path is never used for a
  timestamp (spec §5.3).
- **Image selection is deferred**: on a press, only the capture row is queued;
  a `threading.Timer` selects frames ~`window_after_ms + margin` later so the
  after-window frames exist in the buffer (spec §6.5).
- **No disk on the trigger path.** Commits happen on the persistence writer
  thread via a `queue.Queue`.
- **Worker→UI is thread-safe via a Qt signal bridge** (`_TriggerBridge` in
  `main.py`). Never call Qt widgets directly from a worker thread — it can
  deadlock the GUI.
- **No modal dialogs during a race** (spec §7.5); errors surface as a banner.
- **Calibration (`delta`)** is validated at race start against the live stream
  (§8): water mode requires `calibration.json` matching live resolution/fps;
  screen mode needs none. A dead stream (empty buffer) auto-degrades the race to
  timing-only (skip calibration, `Δ = 0`), and `image_mode = "off"` forces that
  when the stream is up.

## Data & config (the single source of truth)

- **`~/regatta-data/`** (`data_root`) — **do not change the default**.
  - `config.toml` — hand-edited. Trigger keys: `crossing_keycodes` (SPACE=57),
    `start_keycodes` (ENTER=28), `end_keycodes` (F12=88); `grab_device`;
  `[timing] image_mode` = `"auto"` (default) | `"off"` (timing-only — no camera,
  no calibration, no image selection; races start with the stream down). A dead
  stream auto-degrades to timing-only in ANY mode (`start_race` skips calibration
  when the buffer is empty, `Δ = 0`, captures `image_flag = "missing"`); the
  degraded state is persisted as `race.image_off`. `image_mode = "off"` is only
  needed to force timing-only when the stream is *up*. There is no mid-race GUI
  toggle.
  - `regatta.db` — SQLite (`race`, `capture`, `capture_frame`).
  - `races/<Race-YYYYmmdd-HHMM>/` — capture images + per-race `archive/`.
  - `logs/regatta-app.jsonl` — structured log; useful for reproducing issues.
  - `calibration.json` — latency result produced by Calibrate.
  - `races.xlsx` (`[races] excel_path`, default `~/regatta-data/races.xlsx`) —
    the race-name roster, one name per row in column A. The Ready screen shows
    these in a dropdown and passes the selected name to `start_race`.
- **Race selector (Ready screen).** Names already stored in `regatta.db`
  (`Storage.race_names()`, distinct names across all `race` rows) are **grayed
  out** in the dropdown but stay selectable, so a completed race can be
  overwritten. The default selection skips recorded names to the **next
  not-yet-recorded race**; once every race is recorded it wraps to the first.
  The gray set refreshes whenever the app returns to READY and when a race
  ends, so a just-finished race turns gray immediately.
- The app never writes `config.toml`.

## Run / test

```bash
# run (installed deps in venv; typically under systemd-inhibit, see INSTALL.md §7)
~/regatta/venv/bin/python -m hallofframe   # or ./venv/bin/python -m hallofframe

# tests
./venv/bin/python -m pytest -q
```

Race lifecycle (keyboard is grabbed during a race, see `grab_device`):
`Ctrl+S` arm → ENTER starts (`t0`) → SPACE records crossings → **F12 (or End
Race button) ends the race**, releasing the keyboard → `Ctrl+Q`/Quit exits.

## Current-state notes

- App already supports: arm, start, multiple crossings, deferred image
  selection, calibration, export, archive, and (recently added) **End Race +
  Quit**. If a request mentions an end/quit problem, that is implemented —
  check the current `end_race()`/UI wiring before assuming it's missing.
- Version in `hallofframe/__init__.py` (`__version__`).
