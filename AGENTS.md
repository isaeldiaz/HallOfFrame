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
- Persist races to SQLite and save the frames around each crossing once per
  race, named by time since the gun, for jury review.
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
                     Emits through ONE `events(kind, payload)` hook (no
                     `signal_*` attributes); `start_race` returns int or raises
                     `RaceStateError`/`CalibrationError`.
  trigger.py         evdev key-listener (kernel timestamps, debounce, device grab).
  transport.py       iproxy USB tunnel lifecycle (host→device, TCP-only).
  mjpeg.py           MJPEG parse loop; emits timestamped Frame objects.
  framebuffer.py     Timestamped ring buffer; window(target, before, after).
  storage.py         SQLite persistence (WAL, foreign_keys ON), schema + migrations.
                     `updated_at` on race/capture, `meta` table, `race_bundle`/
                     `all_bundles`, `race_identity_rows`, the `frame` table.
  framestore.py      Gun-indexed frame files: `frames/{t_ms:08d}.jpg`, one per
                     t_ms per race; `FrameStore.save`/`nearest`. Replaces the old
                     per-crossing `captures/` writer and `archive.py` (removed).
  render/            `csv.py`, `clipboard.py`, `html.py` split out of `export.py`;
                     one `page()` HTML skeleton + shared `CSS`. `export.py` is a
                     deprecation shim that re-exports the old names.
  web.py             SEPARATE-PROCESS HTTP results server (own read-only SQLite
                     connection; never touches the app's locked Storage). Live
                     race pages with per-crossing frames + per-race "Copy as
                     Excel" (.xls); "Results updated" times, ETag/304 and lazy
                     images. Run: python -m hallofframe.web --config PATH.
  config.py          config.toml load + defaults (never writes the file).
  log.py             Structured JSONL logging.
  calibration.py     Latency calibration helpers plus the single `Calibration`
                     loader (the only place that reads the calibration file).
  session.py         Qt-free `Session` state machine (`Phase`, transitions,
                     `SessionError`), `derive_state()`, and the `KEYMAP`/
                     `KEYBAR_NOTE` key tables.
  ui/                PySide6 widgets: main_window, ready_screen, roster_view,
                     race_screen, review_screen, crossing_list, preview_widget,
                     images, calibration_dialog. `ui/state.py` holds only the
                     `AppState` enum. `ui/crossing_list.py` is one `CrossingList`
                     (fastest-first); `ui/images.load_scaled` is the only image
                     decoder.
  tools/ingest_soak.py  Soak-test utility for the ingest path.
  tools/fake_camera.py  MJPEG server over a folder of JPEGs (`--folder --fps
                     --port [--loop] [--counter]`); lets a full race run with no
                     phone. `[transport] enabled = false` skips the USB tunnel.
  tools/build_feed.py  Copies a gun-ordered subset of recorded regatta frames
                     (from the event DB `frame` table) into a flat, self-contained
                     folder for fake_camera. The full-resolution frames live
                     OUTSIDE the repo; nothing is vendored (JPEGs don't zip).
                     See TESTING.md §3 "Virtual feed".
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
- **Frames are gun-indexed and saved once per race** (`framestore.py`):
  `frames/{t_ms:08d}.jpg` where `t_ms = round((t_recv - t0) * 1000)`; a crossing
  refers to frames by `target_ms ± the race's saved window`, so overlapping
  crossings share files. Continuous archiving was removed (phase 5).
- **No disk on the trigger path.** Commits happen on the persistence writer
  thread via a `queue.Queue`.
- **Worker→UI is thread-safe via a Qt signal bridge** (`_TriggerBridge` in
  `main.py`). The controller has a single `events(kind, payload)` hook; `main.py`
  and `ui/main_window.py` each subscribe one function that switches on `kind`.
  Never call Qt widgets directly from a worker thread — it can deadlock the GUI.
- **No modal dialogs during a race** (spec §7.5); errors surface as a banner.
- **UI position is one `Session` state machine** (`session.py`): `MainWindow`
  holds a `Session`, every lifecycle action is a named transition
  (`arm`/`race_started`/`race_ended`/`open_review`/...), and illegal ones raise
  `SessionError` (shown as a toast). `derive_state(session, stream_alive,
  cal_ok)` maps it to `AppState`. Which keys do what in each state is the
  Qt-free `KEYMAP` table; `MainWindow` builds one `QShortcut` per distinct key
  and enables the subset for the current state. `MainWindow.state_changed` is
  the hook `main.py` uses to sync the trigger grab.
- **Crossing lists are fastest-first** (ascending `elapsed_s`) on the Race,
  Race-over and Review screens, with no `order` argument and no config key
  (spec §7.3/§13.2). A new row while recording must not steal focus from a bow
  field. Every image view decodes through `ui/images.load_scaled`.
- **Calibration (`delta`)** is validated at race start against the live stream
  (§8) by the single `Calibration` loader (`calibration.py`): water mode requires
  a calibration file matching live resolution/fps; screen mode needs none. A dead
  stream (empty buffer) auto-degrades the race to timing-only (skip calibration,
  `Δ = 0`), and `image_mode = "off"` forces that when the stream is up.
- **`start_race` refuses by raising** `RaceStateError` (already running, or a
  prior race has no `ended_at`) or `CalibrationError` (mismatch); it never
  returns a stale `race_id`.

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
    `frame`). The event name is set in `config.toml` and every piece of
    generated data carries it.
  - `[web]` — optional live results HTTP server (host, port, enabled). Runs as
    a **separate process** (`python -m hallofframe.web --config PATH`); it opens
    its **own** SQLite read connection (WAL-safe against the app's writer) and
    serves only pre-rendered HTML + files already on disk, so viewer load never
    perturbs the evdev timing thread. Never point it at the app's `Storage`.
  - `races/<id>_<name>/frames/{t_ms:08d}.jpg` — gun-indexed frames, one file
    per `t_ms` per race; a crossing references frames by time range.
  - `logs/{event_name}-app.jsonl` — structured log; useful for reproducing
    issues.
  - `calibration.json` — latency result produced by Calibrate.
  - `{event_name}_races.csv` (`[races] csv_path`, default
    `~/regatta-data/{event_name}_races.csv`) — the
    race roster, one race per row with three columns: `race_no`, `heat_no`,
    `name`. The Ready screen shows a single combined string (`race_no-Hheat -
    name`) in the dropdown but stores/exports the three fields separately. It
    passes the selected race's fields to `start_race`. **Loading never raises and
    never stops a race (keep-good-rows):** a missing file degrades to a
    timestamp name (`write_example` writes a starter file), while a bad export —
    an `.xlsx` picked by mistake, a directory, a >1 MB field — is reported in
    the Ready-screen banner (`file_error`) and the operator can still race
    without a roster. Windows-1252 and UTF-16 exports are decoded and load
    (cp1252 with a warning). A malformed row is reported (`errors`) and skipped,
    but the good rows still load; a one-column legacy CSV still loads.
    Semicolon-delimited files are detected and kept semicolon-delimited on
    write-back, and a non-standard header (`Race,Heat,Name`) is recognised with
    a warning. The Qt-free `Roster` class in `hallofframe/roster.py` owns the
    file and the race-day edits: skip/unskip, `move` up/down, `add` after the
    selected row, `rename`; display order is always file order (no sorting). The
    old Merge/repoint and near-miss suggestion flows were removed (phase 2).
- **Race selector (Ready screen).** Keys already stored in `{event_name}.db`
  (`roster.recorded_keys(storage)`, distinct across all `race` rows) are
  **grayed out** in the dropdown but stay selectable, so a completed race can be
  overwritten. `Shift+↑`/`Shift+↓` move the selected roster row. The default
  selection skips recorded names to the **next not-yet-recorded race**; once
  every race is recorded it wraps to the first. The gray set refreshes whenever
  the app returns to READY and when a race ends, so a just-finished race turns
  gray immediately.
- The app never writes `config.toml`.

## Run / test

```bash
# run (installed deps in venv; typically under systemd-inhibit, see INSTALL.md §7)
~/regatta/venv/bin/python -m hallofframe   # or ./venv/bin/python -m hallofframe

# fake-camera bench rig (no phone): camera + app in one command (TESTING.md §3)
./hallofframe-fake.sh

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
  selection, calibration, export, the gun-indexed frame store, and (recently
  added) **End Race + Quit**. If a request mentions an end/quit problem, that is
  implemented — check the current `end_race()`/UI wiring before assuming it's
  missing.
- **Resume after restart (N4).** On startup, if `storage.open_race()` finds a
  race with no `ended_at`, `main.py` shows a non-modal Resume/Discard banner.
- **Live results freshness (phase 6).** The web index and each race page show a
  "Results updated HH:MM:SS" line derived from `storage.last_updated()`
  (`meta.db_updated_at` / per-race `updated_at`). HTML carries an `ETag` and
  answers `304` on a matching `If-None-Match`; `/img/` is immutable-cached and
  race-page images are `loading="lazy"`.
- **Review editing (phase 7).** In REVIEW, `Del` soft-deletes the selected
  crossing (row stays, struck through). `U` undoes deletions newest-first
  (multi-level, one press each); `Shift+Del` restores the selected row. `Ins`/
  `Shift+D` clones it (`sequence = MAX+1`, same `target_ms` so frames are
  shared). A time edit that lands where no frames exist flags `missing` and
  clears the primary. Export excludes deleted rows; clones are ordinary rows.
- **Fake camera (phase 7).** `python -m hallofframe.tools.fake_camera --folder
  DIR --fps 30 --port 8081` serves the folder as MJPEG; set `[stream] url` at it
  and `[transport] enabled = false`. `tests/test_e2e.py` (`slow`) drives a full
  race through it. A `[voice]` block (`enabled = false`) is already in the
  config format for phase 8.
- **Refactor note (phase 3).** The plan's target of `ui/main_window.py` under
  350 lines was not reached (it is ~766). Roster rendering was extracted to
  `ui/roster_view.py`; the remaining bulk is the evdev/session orchestration and
  health/export logic. Closing the gap needs a presenter extraction that no plan
  step specifies; accepted as a documented deviation (2026-10-06).
- **Timing-source guards (2026-10-08).** The Qt start/crossing fallback is
  disabled whenever an evdev trigger is present (`MainWindow.evdev_active`), so a
  click or shortcut can never supply a Qt-loop `time.monotonic()` in place of
  the kernel timestamp. `TriggerListener` verifies at startup that the device's
  event timestamps share `CLOCK_MONOTONIC` (spec §6.4) and refuses (Qt fallback)
  on mismatch. A radio-relayed start applies `radio_delay_ms` to `t0` (§5.3.1).
- **Web reads read-only (2026-10-08).** `Storage(..., read_only=True)` opens its
  own `mode=ro` SQLite connection (no schema, no migrations) and `/img/` serves
  only images under `races/`; race/excel pages enforce the reviewed gate.
- **N4 per-capture flag.** A resume that reconstructs `t0` (boot_id mismatch)
  sets `capture.t0_reconstructed` on every crossing recorded after it; CSV and
  HTML render those elapsed times with a `~` prefix.
- **Elapsed format is `M:SS.cc`** (`render.format_elapsed`), per spec §6.8 —
  centiseconds, the resolution the operator needs. Stored times keep full
  precision; only the displayed/exported string is rounded to 2 decimals.
- **Crossing rows show their time-ordered position, not the DB `sequence`**
  (`crossing_list.py` numbers each row by fastest-first rank, tie-broken by
  sequence; `render/html.py` and the flat CSVs do the same). The clone copies
  its parent's time, so it lands beside it with the next position.
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
