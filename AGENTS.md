# AGENTS.md — HallOfFrame Finish-Line Timer

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

The authoritative requirements are in **`hallofframe-finish-timer-spec.md`**
(v1.2). Several "obvious" design alternatives (OpenCV `VideoCapture`, RTSP,
`time.time()`) break the timing accuracy — **do not swap them without reading
the spec's reasoning first** (see the "How to use this document" note and §3).

## Companion documents (read before coding)

| File | Purpose |
|---|---|
| `hallofframe-finish-timer-spec.md` | The build spec. §5 timing model, §6 components, §7 UI, §8 config, §12 order. Read §3, §5, §6 before touching trigger/timing code. |
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
  export.py          CSV + whole-database HTML export; format_elapsed(); flag_word().
  web.py             SEPARATE-PROCESS HTTP results server (own read-only SQLite
                     connection; never touches the app's locked Storage). Live
                     race pages with per-crossing frames + per-race "Copy as
                     Excel" (.xls). Run: python -m hallofframe.web --config PATH.
  config.py          config.toml load + defaults (never writes the file).
  log.py             Structured JSONL logging.
  calibration.py     Latency calibration helpers.
  ui/                PySide6 widgets: main_window, capture_list, preview_widget,
                     calibration_dialog.
  tools/ingest_soak.py  Soak-test utility for the ingest path.
  tools/late_regatta_soak.py  "Almost the whole day is over" soak: seeds a
                     near-complete DB, plays the final race, verifies integrity/
                     exports/latency/memory. Shared with tests/test_late_regatta.py.
tests/               pytest suites (controller, export, framebuffer, mjpeg).
  conftest.py        Shared fixtures (data_root, config, storage, buffer,
                     seeded_buffer, controller, session-scoped qapp) and the
                     --update-goldens option. `config` is a factory taking
                     per-section overrides.
  fakes.py           FakeScheduler / FakeClock for deterministic tests.
  goldens/           Byte-exact export/web snapshots (tests/test_goldens.py).
  test_trigger.py    Synthetic evdev dispatch tests (no device needed).
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

- **`<data_root>`** — a single directory set by `[paths] data_root` in
  `config.toml`; it defaults to `~/regatta-data` but the **name is arbitrary**
  (e.g. `$HOME/regatta-data`).
  `config.toml` lives in that directory. Treat it as the single source of truth.
  - `config.toml` — hand-edited. Trigger keys: `crossing_keycodes` (SPACE=57),
    `start_keycodes` (ENTER=28), `end_keycodes` (F12=88); `grab_device`;
    `end_device_path` — optional second evdev device that handles ONLY the end
    keycodes and is NEVER grabbed (Qt keeps receiving typing, e.g. future boat
    numbers) while the timing `device_path` is grabbed during a race;
  `[timing] image_mode` = `"auto"` (default) | `"off"` (timing-only — no camera,
  no calibration, no image selection; races start with the stream down). A dead
  stream auto-degrades to timing-only in ANY mode (`start_race` skips calibration
  when the buffer is empty, `Δ = 0`, captures `image_flag = "missing"`); the
  degraded state is persisted as `race.image_off`. `image_mode = "off"` is only
  needed to force timing-only when the stream is *up*. There is no mid-race GUI
  toggle.
  - `{event_name}.db` (`[paths] event_name`) — SQLite (`race`, `capture`,
    `capture_frame`). The event name is set in `config.toml` and every piece of
    generated data carries it.
  - `[web]` — optional live results HTTP server (host, port, enabled). Runs as
    a **separate process** (`python -m hallofframe.web --config PATH`); it opens
    its **own** SQLite read connection (WAL-safe against the app's writer) and
    serves only pre-rendered HTML + files already on disk, so viewer load never
    perturbs the evdev timing thread. Never point it at the app's `Storage`.
  - `races/<Race-YYYYmmdd-HHMM>/` — capture images + per-race `archive/`.
  - `logs/{event_name}-app.jsonl` — structured log; useful for reproducing
    issues.
  - `calibration.json` — latency result produced by Calibrate.
  - `{event_name}_races.csv` (`[races] csv_path`, default
    `~/regatta-data/{event_name}_races.csv`) — the
    race roster, one race per row with three columns: `race_no`, `heat_no`,
    `name`. The Ready screen shows a single combined string (`race_no-Hheat -
    name`) in the dropdown but stores/exports the three fields separately. It
    passes the selected race's fields to `start_race`. A one-column (legacy)
    CSV or a missing file degrades gracefully (the UI falls back to a timestamp
    name; `write_example` writes a starter file).
- **Race selector (Ready screen).** Names already stored in `{event_name}.db`
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

# tests — default run is the fast suite (slow tests are deselected)
./venv/bin/python -m pytest -m "not slow"
./venv/bin/python -m pytest -m slow        # the few >2 s tests
```

Test infrastructure (Phase 0): `pytest.ini` registers the `qt` (needs PySide6)
and `slow` (>2 s) markers and sets `testpaths = tests`. A single session-scoped
`qapp` fixture owns the `QApplication` (offscreen) so Qt tests never create or
destroy their own. `tests/goldens/` holds byte-exact snapshots; regenerate with
`pytest tests/test_goldens.py --update-goldens`. CI is
`.github/workflows/ci.yml` (Python 3.12 and 3.14, no `evdev`).

Race lifecycle (keyboard is grabbed during a race, see `grab_device`):
`Ctrl+S` arm → ENTER starts (`t0`) → SPACE records crossings → **F12 (or End
Race button) ends the race**, releasing the keyboard → `Ctrl+Q`/Quit exits.
If the **same** keycode is listed in both `crossing_keycodes` and
`start_keycodes`, the trigger is single-key: the first press while armed IS
`t0`, every press after records a crossing (§5.3). The routed handler is
`on_evdev_crossing` (its dispatch is in `ui/main_window.py`).

## Current-state notes

- App already supports: arm, start, multiple crossings, deferred image
  selection, calibration, export, archive, and (recently added) **End Race +
  Quit**. If a request mentions an end/quit problem, that is implemented —
  check the current `end_race()`/UI wiring before assuming it's missing.
- Version in `hallofframe/__init__.py` (`__version__`).
- **Finish horn is hardware-driven — §13.2 must-have closed (2026-10-06).** The
  deployed crossing button is a **double-pole switch**: pole 1 is the USB HID
  keyboard the app times, pole 2 switches the horn directly. One press fires
  both, so no software horn path is needed and the §13.2 must-have "Drive the
  finish horn from the app" is resolved in hardware. The app never touches the
  horn; note it also sounds on the `t0` press (single-key flow) and on
  accidental presses. See spec §13.2.
- **Retired: relay-driven finish horn (software).** `hallofframe/horn.py`, its
  `tools/test_horn.py` CLI, and the `[horn]` config block were removed (they
  shipped in commit `8f0c9a4`, preserved at tag `horn-relay`, reverted by
  `d734372`). No tests or other modules depended on it; it is superseded by the
  double-pole hardware above. Reintegration steps and the LCUS-1 protocol are
  recorded in the spec §13.2 implementation note.
