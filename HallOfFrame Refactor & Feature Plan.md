# HallOfFrame Refactor & Feature Plan

Oct 6, 2026 · @Bro

## How to use this document

This plan is written for an implementing model with limited context. Work one phase at a time, in order, and read only the sections that phase names. Phases 0–4 are refactors with zero behaviour change; phases 5–7 add the features. Three must-have features from spec §13.2 land in phases 1, 4 and 7; the gun-indexed frame store (spec §13.3) is phase 5.

Rules for every phase:

1. Before changing anything, run `python -m pytest -q -m "not slow"` and confirm it passes. Run it again after every step. A step is not finished while a test fails.
2. One phase = one pull request. Do not start the next phase in the same branch.
3. Each step lists the files it touches. If a step seems to need a file it does not list, stop and ask instead of improvising.
4. Never change the four files in the section *Things that must not change* except where a step names the exact function.
5. When a step says *move*, cut the code from the old file and paste it into the new one with no edits other than imports. Behaviour changes are always a separate step.
6. When a step says *delete*, grep the repository (`grep -rn <name> hallofframe tests`) first; the step is wrong if the grep finds a live caller it did not mention. Report that rather than deleting.
7. Keep docstrings that cite spec sections (for example `§6.5`). They are the reason the code is shaped the way it is.
8. Reference the spec at `hallofframe-finish-timer-spec.md` and the onboarding notes at `AGENTS.md`. Update `AGENTS.md` at the end of each phase so it describes the code as it now is.

Terms used below: *crossing* = one button press recorded in the `capture` table; *frame* = one JPEG from the camera stream; *window* = the frames saved around a crossing (±500 ms by default); *gun* or *t0* = the race-start timestamp; *roster* = the race list CSV shown in the Ready-screen dropdown.

## Current-state findings

The timing core is sound and well tested; the maintenance risk sits in the UI layer and the roster subsystem. The repository is about 12,200 lines: 3,840 core, 5,170 UI, 610 tools, 2,620 tests. The 115 headless tests pass in 17 s; 6 test files need PySide6.

| Area | Files | Lines | Finding | Action |
| --- | --- | --- | --- | --- |
| Timing core | `mjpeg.py`, `framebuffer.py`, `trigger.py`, hot path of `controller.py` | \~900 | Spec-faithful, tested. `FrameBuffer._lock`/`_buf`/`_snapshot` are read from 3 other modules. | Keep. Add accessor methods; do not touch internals. |
| Main window | `ui/main_window.py` | 1,030 | Owns 5 state flags plus `controller.race_id` (three sources for "which race is on screen"), roster orchestration (\~350 lines), four overlapping shortcut/keybar methods, calibration polling, export. Calls `controller.storage` 21 times; writes `ready._rows`/`_sel_row`. | Phases 2–3 cut it to under 350 lines. |
| Roster | `races.py`, `ui/roster_dialog.py`, glue in main window | \~1,300 | Add/Rename/Identify/Merge dialogs, Levenshtein near-miss suggestions, repoint logic. No *move/reorder* operation, which is the common field edit. `storage.py` imports `races.py` for key logic (layering inversion). | Phase 2 rebuilds around skip, move, add, rename. |
| Calibration file | 5 modules | — | `calibration.json` parsed independently in `controller.py` (twice), `calibration.py`, `ui/calibration_dialog.py`, `ui/main_window.py`, `ui/about_screen.py`. | Phase 1: one loader. |
| Controller API | `controller.py` | 557 | Six `signal_*` callables each wrapped in `try/except`; `Capture` is a hand-written class; `start_race` returns the *previous* `race_id` when it refuses to start, and its "prior race still open" check only warns and fires on every race after the first; real `threading.Timer` makes tests sleep and poll. | Phase 1. |
| Archive (F7) | `archive.py`, health band | 89 | `ArchiveWriter` is never constructed in `main.py`. The Recording screen shows "Archive: writing" but that label mirrors `controller.running`. | Phase 5 replaces it with the frame store. |
| Resume (N4) | `controller.resume_race` | — | Implemented, never called from the UI. | Phase 3 adds a startup prompt. |
| Rendering | `export.py`, `web.py` | 960 | \~600 lines of inline-style f-strings; `build_index` runs 2 queries per race. No `updated_at` anywhere in the schema. | Phases 1 and 6. |
| Crossing lists | `ui/crossing_list.py` | 356 | Two row classes and two list classes; order hardcoded `reverse=True`. `review_screen` pops `list._rows` directly. | Phase 4. |
| Dead code | `FrameBuffer.check_fps`, module function `live_lens_name` in `controller.py`, `ui/capture_list.py` (135 lines, class `CaptureList`), `ui/review_dialog.py` (130 lines, class `RaceReviewDialog`, superseded by `review_screen.py`) | \~270 | No production callers. `review_dialog.py` is referenced only by its own test. | Phase 1 deletes all four. |
| Tests | `tests/` | 2,620 | No `conftest.py`, no `pytest.ini`, no CI. `make_config()` duplicated in 6 files. Controller tests poll with `time.sleep`. UI tests assert Qt geometry at 1920 px and synthesise `QKeyEvent`s. No test of `main.py` wiring or of trigger dispatch. | Phase 0, then each phase. |

## Decisions already made

Two product decisions shape this plan. They are settled; do not reopen them during implementation.

**Roster editing stays, redesigned around the edits that happen on race day.** The operator receives the race list as a CSV on the morning of the regatta. The list has errors, and the two common corrections are *a race is skipped* and *two races swap places*. The roster therefore needs exactly these operations: skip/unskip, move up/down, add a race (late entry or omission), rename, and run an unlisted race. The current Merge/repoint dialog, the near-miss fuzzy matching and the "create row" that appears while filtering are removed. Display order is file order, always; adding a race inserts it after the selected row, never by sorting.

**Continuous archiving (F7) is replaced by the gun-indexed frame store from spec §13.3.** Frames are no longer saved as `captures/001_w-0167.jpg` (named by crossing and offset) and there is no separate continuous `archive/` folder. Instead every saved frame is named by its time since the gun, `frames/00412033.jpg` for a frame received 412.033 s after t0, and a crossing references frames by time range. Two crossings whose windows overlap share frames on disk instead of duplicating them, and a crossing added after the race can find frames that were saved for a neighbour. `archive.py` and the `[archive]` config block are deleted. Recovery of a missed press is possible only from saved windows and the live ring buffer; this narrows F7 and the spec's §2.1 row should be amended to say so.

**Three must-have features from spec §13.2 are in scope** and are assigned to phases: last-updated time on the web pages (phase 6), fastest-first crossing list (phase 4), insert/clone/remove any crossing in review (phase 7). The horn and optics items are closed in hardware.

**Nice-to-haves included:** fake camera feed and text-only web index with HTTP caching, because both fall out of refactor work already planned. **Deferred:** zoom/ROI, keypad lanes, play-sequence player, voice annotation. Each has a one-line note in its phase saying where it would attach.

## Target architecture

After the refactor the package has three layers and a strict rule: Qt code may call application logic and the core; application logic may call the core; nothing calls upward except through the signal bridge in `main.py`.

&#91;embedded content: target module map · three layers, five rebuilt modules\]

Arrows show who calls whom. The accented boxes are the modules this plan creates or rebuilds: `Session`, `Roster`, `Calibration`, `Render`, `FrameStore`, and the shared `CrossingList`/`images` widgets. Every other box exists today and keeps its public behaviour.

Resulting file layout (new or renamed files in bold):

| Path | Role | Phase |
| --- | --- | --- |
| `hallofframe/main.py` | Entry point, `build_core`, `build_trigger`, the Qt signal bridge | 1 (archive removed) |
| `hallofframe/controller.py` | `CaptureController`; hot path unchanged | 1, 5, 7 |
| `hallofframe/storage.py` | SQLite; gains `updated_at`, `frame` table, bundle queries | 1, 5 |
| **`hallofframe/framestore.py`** | Gun-indexed frame files, replaces `archive.py` and the per-crossing `captures/` writer | 5 |
| `hallofframe/calibration.py` | Measurement helpers plus the single `Calibration` loader | 1 |
| **`hallofframe/roster.py`** | Renamed from `races.py`; `Roster` class owning the CSV | 2 |
| **`hallofframe/session.py`** | `Session` state machine and the keymap table (Qt-free) | 3 |
| **`hallofframe/render/`** | `csv.py`, `clipboard.py`, `html.py` split out of `export.py` | 6 |
| `hallofframe/web.py` | HTTP server; uses `render/html.py` | 6 |
| **`hallofframe/voice.py`** | `AudioRecorder` and `Transcriber`, subscribed to controller events in `main.py` | 8 (optional) |
| `hallofframe/ui/main_window.py` | Under 350 lines: builds screens, forwards to `Session` | 2, 3 |
| **`hallofframe/ui/crossing_list.py`** | One `CrossingList` class, fastest-first | 4 |
| **`hallofframe/ui/images.py`** | `load_scaled()` used by every image view | 4 |
| `hallofframe/ui/roster_dialog.py` | Add and Rename dialogs only | 2 |
| **`hallofframe/tools/fake_camera.py`** | MJPEG server over a folder of JPEGs | 7 |
| **`tests/conftest.py`**, **`pytest.ini`**, **`.github/workflows/ci.yml`** | Shared fixtures, markers, CI | 0 |
| ~~`hallofframe/ui/capture_list.py`~~, ~~`hallofframe/ui/review_dialog.py`~~, ~~`tests/test_review_dialog.py`~~ | Deleted (no callers; superseded by `review_screen.py`) | 1 |
| ~~`hallofframe/archive.py`~~ | Deleted | 5 |

## Phase 0 — Test safety net

Goal: make the existing behaviour verifiable before any code moves. No production code changes in this phase except the two small seams in step 4.

**Files touched:** `tests/conftest.py` (new), `pytest.ini` (new), `.github/workflows/ci.yml` (new), `tests/goldens/` (new), every `tests/test_*.py`, `hallofframe/controller.py` (constructor only), `hallofframe/framebuffer.py` (constructor only).

- [ ] **Step 0.1 — `pytest.ini`.** Register markers `qt` (needs PySide6) and `slow` (over 2 s). Set `addopts = -q -p no:cacheprovider` and `testpaths = tests`. Document in `TESTING.md` that the default run is `pytest -m "not slow"`.
- [ ] **Step 0.2 — `tests/conftest.py`.** Create fixtures and delete the six local copies of `make_config` / `_make_config` / `_config` (in `test_arm_disarm.py`, `test_controller.py`, `test_export.py`, `test_focus.py`, `test_review_dialog.py`, `test_review_screen.py`). `test_late_regatta.py` imports `make_config` from `tools/late_regatta_soak.py`; leave that one, it is the tool's own. Fixtures:
  - `data_root` → a `tempfile.TemporaryDirectory` as `Path`.
  - `config(**overrides)` → a `Config` built from one canonical dict (copy the most complete one, from `test_arm_disarm.py`, including `trigger` and `ui`), with `overrides` merged section by section.
  - `storage` → `Storage(data_root)`, closed on teardown.
  - `buffer` → `FrameBuffer(assumed_fps=30)`; `seeded_buffer(t0=1000.0, fps=30, seconds=6.0)` → the `seed_buffer` helper from `test_controller.py`.
  - `controller` → `CaptureController(config, storage, buffer)`, `.stop()` on teardown.
  - `qapp` (session scope) → `QApplication` with `QT_QPA_PLATFORM=offscreen`; `pytest.skip` if PySide6 is missing. Every Qt test file gets `pytestmark = pytest.mark.qt` and uses this fixture instead of its own `_QT` try/except.
- [ ] **Step 0.3 — Golden files.** Add `tests/test_goldens.py`: seed a storage with 2 races and 4 crossings using fixed timestamps (no `time.time()`), then compare `export_csv`, `export_all_csv`, `export_all_html`, `clipboard_data`, `web.build_index` and `web.build_race_page` byte-for-byte against files in `tests/goldens/`. Strip the build stamp (`buildinfo.build_stamp()`) and version string before comparing. Add a `--update-goldens` option in `conftest.py` that rewrites the files. These tests are what make phase 6 safe.
- [ ] **Step 0.4 — Two injection seams.** In `CaptureController.__init__` add a keyword argument `scheduler=None`; when given, it is called as `scheduler(delay_s, callback)` and must return an object with `.cancel()`. Default behaviour (`threading.Timer`) is unchanged. In `FrameBuffer.__init__` add `clock=time.monotonic` and use `self._clock()` in `append` and `health`. Add `tests/fakes.py` with `FakeScheduler` (`.advance(seconds)` fires due callbacks in order) and `FakeClock` (`.now`, `.advance`).
- [ ] **Step 0.5 — Deterministic controller tests.** Rewrite `test_controller.py` to use `FakeScheduler`: replace every `_wait_for_frames` / `time.sleep` poll with `scheduler.advance(0.6)`. The writer thread is still real; wait for it with `controller._queue.join()` (add `task_done()` calls in `_writer_loop` — this is the only production change, and it is behaviour-neutral). Test count must not decrease.
- [ ] **Step 0.6 — Trigger dispatch test.** Extract the body of the `for event in device.read_loop()` loop in `trigger.py` into a method `_dispatch(self, code: int, value: int, timestamp: float) -> None` that performs the keydown filter, debounce check and handler call. No logic change. Add `tests/test_trigger.py` feeding synthetic events: key-up ignored, repeat ignored, second press inside `debounce_ms` is delivered with `suspect=True`, unknown keycode ignored.
- [ ] **Step 0.7 — CI.** `.github/workflows/ci.yml` on `ubuntu-latest`, Python 3.12 and 3.14, installing `PySide6-Essentials pillow requests pytest` and the apt packages `libegl1 libxkbcommon0 libxcb-cursor0`, with `QT_QPA_PLATFORM=offscreen`. Two jobs: `pytest -m "not slow"` and `pytest -m slow`. Do not install `evdev` in CI; tests that import it must skip.

**Done when:** CI is green on both Python versions; `grep -rn "def make_config\|def _make_config\|def _config" tests/` returns nothing; `grep -rn "time.sleep" tests/test_controller.py` returns nothing; `tests/goldens/` holds six files.

## Phase 1 — Core hygiene

Goal: remove duplicated knowledge and dead code in the core, and add the schema columns later phases need. The timing hot path (`record_crossing`, `_writer_loop`, `_handle_capture`, `_select_images`) is not edited in this phase except as step 1.3 states.

**Files touched:** `calibration.py`, `controller.py`, `framebuffer.py`, `storage.py`, `main.py`, `ui/main_window.py`, `ui/about_screen.py`, `ui/calibration_dialog.py`, `tests/`.

- [ ] **Step 1.1 — One calibration loader.** In `calibration.py` add:

  ```python
  @dataclass(frozen=True)
  class Calibration:
      latency_median_ms: float
      latency_iqr_ms: float
      resolution: str      # "1440x1080" or ""
      fps: float           # 0 when unknown
      mean_frame_bytes: int
      measured_at: str
  
      @classmethod
      def load(cls, data_root: Path) -> "Calibration | None": ...   # None if missing or unreadable
      def mismatch(self, live_resolution: str, live_fps: float) -> str | None: ...  # None = ok, else reason text
  ```

  `mismatch` holds the two comparisons now in the module-level functions `_load_latency` and `calibration_status` in `controller.py` (resolution must be equal when both known; fps must be within `max(1, cal_fps*0.05)` when both known). Then replace: `controller._load_latency` and `controller.calibration_status` (keep their names and return types, make them thin wrappers), `MainWindow._cal_latency_ms`, `about_screen.calibrated_latency`, and the JSON reads in `calibration_dialog.py`. Delete the module-level function `live_lens_name` in `controller.py`.
- [ ] **Step 1.2 — FrameBuffer accessors.** Add `FrameBuffer.recent(n: int) -> list[Frame]` (last n frames under the lock) and `FrameBuffer.live_format() -> tuple[str, int, float]` returning `(resolution, mean_frame_bytes, fps)` — move the body of the module-level function `_measure_live` in `controller.py` here. Replace the external uses of `buffer._lock` / `buffer._buf` / `buffer._snapshot()` in `controller._measure_live`, `calibration.capture_calibration_frames`, `calibration_dialog._sample` and `calibration_dialog.py:144` (`_measure_fps(self.buffer._snapshot())` becomes `_measure_fps(self.buffer.recent(30))`). Then delete `FrameBuffer._snapshot` and `FrameBuffer.check_fps`. After this step `grep -rn "_buf\b\|buffer._lock\|_snapshot" hallofframe` finds hits only inside `framebuffer.py`.
- [ ] **Step 1.3 — Controller API.** (a) Make `Capture` a `@dataclass(frozen=True)` with the same field names. (b) Replace the six `signal_*` attributes with one attribute `events: Callable[[str, dict], None] | None` and one private method `_emit(kind, **payload)` that does the `try/except`. Event kinds: `capture_added`, `capture_deleted`, `image_ready`, `race_started`, `race_ended`, `warning`. Update `main.py` (bridge) and `main_window._connect_controller` to subscribe through one function that switches on `kind`. (c) `start_race` returns `int` on success and raises on refusal; the hot path is untouched. It has three refusal paths today and each becomes a raise: `if self.running` → `RaceStateError("race already running")`; the `CalibrationError` path → re-raise instead of `return self.race_id`; the "prior race is open" branch → **fix the condition first**: it must test `storage.get_race(self.race_id)["ended_at"] is None` (today it tests only that the row exists, so it warns on every race after the first), and when the prior race is truly un-ended raise `RaceStateError("end race N first")`. Define `RaceStateError(Exception)` in `controller.py`. Callers: `main_window.on_evdev_start` catches `CalibrationError` and `RaceStateError` and shows the message as a toast.
- [ ] **Step 1.4 — Storage: `updated_at` and bundles.** (a) Add column `updated_at TEXT` to `race` and `capture` in `SCHEMA` and in `_migrate` (backfill with `created_at` for `race`, with the race's `created_at` for `capture`). (b) Add table `meta(key TEXT PRIMARY KEY, value TEXT)`. (c) Add private `_touch(race_id)` that sets `race.updated_at` and `meta['db_updated_at']` to `_utcnow()`; call it from every method that writes a race or a capture (`create_race`, `rename_races`, `identify_race`, `repoint_race`, `mark_race_ended`, `mark_race_reviewed`, `set_start_time`, `mark_race_reconstructed`, `insert_capture`, `update_capture`, `set_crossing_time`, `insert_frame`, `set_primary`). `update_capture` also sets `capture.updated_at`. (d) Add `last_updated(race_id=None) -> str | None` returning the ISO string. (e) Add `race_bundle(race_id) -> tuple[Row, list[Row]]` (race + non-deleted captures sorted by `elapsed_s`) and `all_bundles()` yielding them oldest-first; replace `export._all_race_blocks` and the per-race queries in `web.build_index` with these. (f) Move `race_keys`, `rename_races` and `repoint_race`'s key comparison out of `storage.py`: storage exposes `race_identity_rows()` returning `(id, race_no, heat_no, name)` rows, and the key logic lives in the roster module (phase 2 renames it; for now import from `races.py` in the caller, not in storage).
- [ ] **Step 1.5 — Remove the archive illusion and the two orphan UI modules.** In `main_window._update_health`, delete the `"Archive"` readout and its branch; `_health_labels` returns `["Stream", "Δ latency", "Disk"]` in every state. Do not delete `archive.py` yet (phase 5 does). Delete `hallofframe/ui/capture_list.py` (`CaptureList`, no callers) and `hallofframe/ui/review_dialog.py` together with `tests/test_review_dialog.py` (`RaceReviewDialog` is the pre-redesign review window; `ui/review_screen.py` replaced it). Confirm with `grep -rn "capture_list\|review_dialog\|CaptureList\|RaceReviewDialog" hallofframe tests` returning nothing before and after.
- [ ] **Step 1.6 — Tests.** `test_calibration.py` (new): `Calibration.load` on missing/corrupt/valid files, `mismatch` on each branch. `test_framebuffer.py`: `recent`, `live_format` (use a real tiny JPEG from PIL). `test_controller.py`: `start_race` raises on calibration mismatch, when already running, and when the previous race has no `ended_at`; a second race after a properly ended first one starts without a warning; events arrive through the single hook. `test_storage.py` (new): `updated_at` changes on every write method listed in 1.4(c); `last_updated()` with and without `race_id`; `race_bundle` ordering excludes deleted rows.

*Also in step 1.4, for phase 8:* add the nullable columns `race.audio_path TEXT`, `race.audio_t0_offset_s REAL`, `capture.bow_suggested TEXT`, `capture.bow_source TEXT` to `SCHEMA` and `_migrate` now, so the voice-annotation phase needs no second migration. Add `storage.set_race_audio(race_id, path, offset)`. These columns stay unused until phase 8.

**Done when:** `grep -rn "calibration.json" hallofframe` shows one hit (the loader); `grep -rn "signal_" hallofframe` returns nothing; `start_race` has no `return self.race_id`; `PRAGMA table_info(capture)` lists `updated_at`; goldens unchanged (the HTML does not show the new column yet).

## Phase 2 — Roster module

Goal: one Qt-free `Roster` class owns the CSV and the five race-day operations; the main window only forwards. Roughly 600 lines are deleted and a *move* operation is added.

**Files touched:** `races.py` → renamed `roster.py`, `ui/roster_dialog.py`, `ui/ready_screen.py`, `ui/main_window.py`, `ui/review_screen.py`, `storage.py` (the key import from 1.4f), `tests/test_races.py`, `tests/test_roster_write.py`, `tests/test_roster_dialog.py`, `tests/test_race_selector.py`.

The CSV file format does not change: header `race_no,heat_no,name,source,status`, a 3-column legacy file still loads, `status=skipped` still marks a skipped row. Only the code around it changes.

- [ ] **Step 2.1 — Rename.** `git mv hallofframe/races.py hallofframe/roster.py`; update imports. Keep every name not listed in step 2.2, in particular: `RaceInfo`, `format_display`, `race_key`, `_norm`, `_cell`, `_fold`, `load_races`, `read_rows`, `_atomic_write`, `mutate_roster`, `_find_row`, `_pad5`, `rename_race`, `skip_race`, `add_row`, `write_example`, `RosterLoad`, `RosterWriteError`, `HEADER`, `HEADER_5`. (`rename_race` and `add_row` are called from `ui/roster_dialog.py`; `skip_race` from `main_window.py`.)
- [ ] **Step 2.2 — Delete what race day does not need.** In `roster.py` delete `near_misses`, `_edit_distance`, `parse_key`, `remove_row`, `remove_row_exact`, `_sorted_insert_pos`, `_sort_key`, `_numkey`, `_Unsorted`, `_Collision`, `add_heat`; in `storage.py` delete `repoint_race`; in `ui/roster_dialog.py` delete the `IdentifyDialog` and `MergeDialog` classes. In `ui/ready_screen.py` delete the `create` row kind and every `near_misses`/`parse_key` use in `_rebuild_rows`; the filter still narrows the list by substring. In `main_window.py` delete `_has_duplicates`, `_recorded_count_for_key`, `_open_identify`, `_open_merge`, and the `identify_requested`/`merge_requested` signals in `review_screen.py`. Run the grep from rule 6 for each name before deleting. Duplicate CSV keys are still reported by `load_races` (first wins) and still shown as a banner; the operator fixes them in the file.
- [ ] **Step 2.3 — The `Roster` class.** In `roster.py`:

  ```python
  class Roster:
      def __init__(self, path: str | None): ...
      path: str | None
      result: RosterLoad          # last load
      races: list[RaceInfo]       # file order, duplicates dropped
      rows: list[list[str]] | None  # raw rows for optimistic-concurrency checks
      def load(self, path: str | None = None) -> RosterLoad
      def skipped_keys(self) -> set
      def skip(self, key, skip: bool) -> RosterLoad
      def move(self, key, delta: int) -> RosterLoad   # delta -1 = up, +1 = down; swaps two data rows
      def add(self, race_no, heat_no, name, after_key=None) -> tuple[RosterLoad, str]   # inserts after after_key, else appends; "ok" | "collision"
      def rename(self, key, new_name) -> RosterLoad
      def write_example(self) -> None
  ```

  `skip`, `rename` and `add` wrap the existing `skip_race`, `rename_race` and `add_row` with `expected=self.rows`, then call `self.load()`. `add_row` loses its sorted insert (deleted in 2.2) and takes an `after_key` parameter instead: insert after that row, else append. `move` is new: find the row index by key among data rows with `_find_row`, swap with the neighbour, refuse at the ends with `RosterWriteError`.
- [ ] **Step 2.4 — Main window forwards.** `MainWindow` holds `self.roster = Roster(path)`. Replace `_load_races`, `_apply_roster_result`, `_skipped_keys`, `_toggle_skip`, `_open_add_race`, `_open_rename`, `_reload_roster`, `_write_example_roster` with calls into `self.roster` followed by one method `_render_roster()` that pushes `roster.races`, `storage.race_keys()` and `roster.skipped_keys()` into `ReadyScreen.set_races` and rebuilds the chip and banners. The banner text in `_render_roster_banner` is kept verbatim. Add two keybar actions in READY: `Shift+↑` / `Shift+↓` = `roster.move(selected_key, ∓1)`, then `ready.select_key(key)`.
- [ ] **Step 2.5 — ReadyScreen encapsulation.** Add `ReadyScreen.select_key(key) -> bool`; replace the `self.ready._rows` / `self.ready._sel_row` writes in `main_window._select_race_by_key` with it. Delete `_select_race_by_key`.
- [ ] **Step 2.6 — Review-side identify.** The one remaining use of the identify flow (an unlisted race gets its number after the fact) becomes a plain *Edit race* action in the review screen that calls `storage.identify_race` and, when the operator ticks "also add to roster", `roster.add(...)`. Reuse `RenameDialog` with the number fields enabled; do not write a new dialog class.
- [ ] **Step 2.7 — Tests.** `test_roster.py` (merge `test_races.py` and `test_roster_write.py`): every `Roster` method against a temp CSV, including `move` at both ends, `move` on a 3-column legacy file (pads to 5 columns), `skip` round-trip, `add` collision and `add` with `after_key`, concurrent-edit refusal (`rows` stale → `RosterWriteError`). Delete tests for removed functions. `test_race_selector.py`: drop the create-row and near-miss cases, add `select_key`.

**Done when:** `wc -l hallofframe/roster.py hallofframe/ui/roster_dialog.py` is under 600 combined (today 984); `main_window.py` has no `csv`, `RaceInfo(` or `read_rows` references; `grep -rn "_rows\|_sel_row" hallofframe/ui/main_window.py` returns nothing.

## Phase 3 — Session state machine and keymap

Goal: one object holds the operator's position in the race lifecycle, every transition is a named method, and one data table describes which keys do what in each state. `main_window.py` ends under 350 lines. No visible behaviour changes.

**Files touched:** `session.py` (new), `ui/state.py` (reduced to the enum), `ui/main_window.py`, `ui/widgets.py` (`KeyBar` reads the table), `main.py` (grab hook), `tests/test_state.py` → `tests/test_session.py`, `tests/test_arm_disarm.py`, `tests/test_focus.py`.

- [ ] **Step 3.1 — `Session`.** Create `hallofframe/session.py`:

  ```python
  class Phase(Enum): IDLE, ARMED, RECORDING, RACE_OVER, REVIEW
  
  @dataclass
  class Session:
      phase: Phase = Phase.IDLE
      focused_race_id: int | None = None   # the race shown in RACE_OVER / REVIEW
      return_to: Phase = Phase.IDLE        # where Esc from REVIEW goes (IDLE or RACE_OVER)
  
      # transitions — each returns None or raises SessionError with a message for the toast
      def arm(self) -> None                       # IDLE or RACE_OVER → ARMED
      def disarm(self) -> None                    # ARMED → IDLE
      def race_started(self, race_id) -> None     # ARMED → RECORDING
      def race_ended(self, race_id) -> None       # RECORDING → RACE_OVER, focused = race_id
      def load_race(self, race_id) -> None        # IDLE → RACE_OVER, focused = race_id
      def open_review(self, race_id) -> None      # RACE_OVER or IDLE → REVIEW, return_to set
      def close_review(self) -> None              # REVIEW → return_to
      def dismiss(self) -> None                   # RACE_OVER → IDLE
  
  def derive_state(session, stream_alive: bool, cal_ok: bool) -> AppState
  ```

  `derive_state` keeps today's precedence exactly: RECORDING > ARMED > REVIEW > RACE\_OVER > STREAM\_DOWN > RECALIBRATE > READY. The five window flags `_armed`, `_race_over`, `_reviewing`, `_review_race_id`, `_race_over_race_id` and the helper `_current_race_id` are deleted and every read becomes `self.session.phase` / `self.session.focused_race_id`.
- [ ] **Step 3.2 — Wire transitions.** Map each existing window method to one transition: `_arm_start` → `arm`, `on_evdev_end` while armed → `disarm`, `on_evdev_start` → `race_started`, `on_race_ended` → `race_ended`, `_load_selected_race` → `load_race`, `_open_review` → `open_review`, `_close_review` → `close_review`, `_esc` and `_on_race_selected` in RACE\_OVER → `dismiss`. After each transition call `_recompute_state()` once. `SessionError` messages replace the ad-hoc toast strings ("Can't arm in this state." etc.); keep the current wording in the exceptions.
- [ ] **Step 3.3 — Keymap table.** In `session.py`:

  ```python
  @dataclass(frozen=True)
  class Key:
      key: str          # Qt sequence, e.g. "Ctrl+S"
      label: str        # keybar text
      action: str       # name of a MainWindow method
      hot: bool = False
      typable: bool = False   # disabled while a QLineEdit has focus
  
  KEYMAP: dict[AppState, list[Key]] = { ... }
  ```

  Fill it from today's `_install_shortcuts` and `_apply_keybar` so the two agree. `MainWindow` creates one `QShortcut` per distinct `key` at construction, and on every state change (a) enables exactly the shortcuts whose `Key` appears under the current state, (b) rebuilds the `KeyBar` from the same list, (c) applies the `typable` rule from `_sync_shortcuts`. Delete `_install_shortcuts`, `_apply_keybar`, `_sync_shortcuts`, `_typable_shortcuts`, `_race_shortcuts`. Keybar notes (`set_note`) move to a second dict `KEYBAR_NOTE: dict[AppState, str]`.
- [ ] **Step 3.4 — Encapsulate what is left.** `ReviewList.remove(seq)` replaces the `self.list._rows.pop` in `review_screen._delete`; `CrossingLog.count()` replaces `len(self.log._rows)` in `race_screen.py`. The trigger-grab hook in `main.py` subscribes to `win.state_changed` (a new Qt signal emitted at the end of `_apply_state`) instead of being assigned to `win.on_state_changed`.
- [ ] **Step 3.5 — Resume after restart (N4).** On startup, if `storage.open_race()` (new: the newest race with `ended_at IS NULL`) exists, `main.py` shows a non-modal banner "Race N was not ended — Resume / Discard". Resume calls `controller.resume_race(id)` then `session.race_started(id)`; Discard calls `storage.mark_race_ended(id, None)`. This is the only new behaviour in the phase and it is additive.
- [ ] **Step 3.6 — Tests.** `test_session.py`: every transition from every phase (a 5×8 table; illegal ones raise), `derive_state` precedence (port the nine cases from `test_state.py`), `KEYMAP` sanity (every `action` names a real `MainWindow` method — import the class, `hasattr`; no duplicate `key` within a state). `test_arm_disarm.py` and `test_focus.py` keep passing with the flags removed; where they poked `win._armed`, read `win.session.phase` instead.

**Done when:** `wc -l hallofframe/ui/main_window.py` is under 350; `grep -n "_armed\|_race_over\|_reviewing" hallofframe/ui/main_window.py` returns nothing; every `QShortcut(` in the file is inside one loop over `KEYMAP`.

## Phase 4 — Crossing list and image loader

Goal: one list widget with a single fixed order, and one function that turns an image into a scaled pixmap. This phase delivers must-have **fastest-first ordering** (spec §13.2) and prepares the zoom/ROI nice-to-have.

**Files touched:** `ui/crossing_list.py`, `ui/images.py` (new), `ui/race_screen.py`, `ui/review_screen.py`, `ui/preview_widget.py`, `ui/misc_screens.py`, `hallofframe-finish-timer-spec.md` (§7.3), `README.md`, `AGENTS.md`, `tests/test_review_screen.py`, `tests/test_crossing_list.py` (new), `tests/test_images.py` (new). No config change.

- [ ] **Step 4.1 — `ui/images.py`.** One function:

  ```python
  def load_scaled(source: str | bytes, size: QSize, *, fast: bool = True,
                  roi: tuple[float, float, float, float] | None = None) -> QPixmap | None
  ```

  `source` is a path or JPEG bytes. Uses `QImageReader.setScaledSize` before `read()` (spec §7.2) and `Qt.FastTransformation` unless `fast=False`. `roi` is a normalised `(x, y, w, h)` crop applied before scaling; pass `None` everywhere in this phase. Replace `crossing_list._load_pixmap`, the decode in `preview_widget.py`, `race_screen.LastCapturePanel.set_photo` and `review_screen._show_frame` / `_Photo` with calls to it. Behaviour is identical for `roi=None`.
- [ ] **Step 4.2 — One `CrossingList`.** Replace `CrossingLog`, `ReviewList`, `_RowBase`, `_ReviewRow` with:

  ```python
  class CrossingList(QWidget):
      bow_edited = Signal(int, str); time_edited = Signal(int, str); delete_requested = Signal(int)
      def __init__(self, *, editable: bool, parent=None)
      def add(self, data: dict); def update_thumb(self, seq, path); def remove(self, seq); def clear(self)
      def count(self) -> int; def set_selected(self, seq | None); def focus_bow(self, seq); def refresh_time(self, seq, elapsed_s)
  ```

  A single `_Row` class shows the bow and time fields only when `editable`. `_rebuild` sorts by `elapsed_s` ascending — fastest first, on every screen, with no parameter to change it. The header caption reads "fastest first". There is deliberately **no** `order` argument and **no** config key: spec §13.2 asks that the configurable ordering of §7.3 be removed so it "can't reintroduce the confusion".
- [ ] **Step 4.3 — Spec and docs.** Amend spec §7.3: replace "Newest at top, or newest at bottom with auto-scroll — pick one and make it configurable" with "Fastest at top, slowest at bottom, on every screen; not configurable (decided 2026-10-06, §13.2)". Keep the auto-scroll rule that follows it (a new row while recording must not steal focus from a bow field). Update `README.md` and `AGENTS.md`. No config change.
- [ ] **Step 4.4 — Tests.** `test_crossing_list.py` (`qt` marker): rows render fastest-first regardless of insertion order; `remove` and `count` agree; `editable=False` shows no `QLineEdit`. `test_review_screen.py`: keep the four regression cases from its docstring; replace geometry assertions with "minimum width ≤ 1920" only. `test_images.py`: `load_scaled` on a 64×36 PIL JPEG returns the requested size; `roi` crops to the expected pixel colour (two-colour test image).

**Done when:** `grep -rn "QImageReader\|QPixmap(" hallofframe/ui` shows hits only in `images.py`; `crossing_list.py` defines exactly one public class; a fresh database shows the first finisher at the top of the Race and Review screens.

*Deferred hook:* zoom/ROI (spec §13.3) becomes a `[ui] roi` value read once in `MainWindow` and passed to every `load_scaled` call; the preview also needs a drag handle in `preview_widget.py`. Nothing else moves.

## Phase 5 — Gun-indexed frame store

Goal: every saved frame is named by its time since the gun and stored once per race; a crossing refers to frames by time range instead of owning copies. This replaces both the per-crossing `captures/NNN_w±mmmm.jpg` files and the never-wired `archive.py`. Timing behaviour (when selection runs, which frame becomes primary, the `image_flag` rules) does not change.

**Files touched:** `framestore.py` (new), `controller.py` (`_select_images`, `start_race`, `resume_race`), `storage.py`, `main.py`, `config.py`, `hallofframe.example.toml`, `archive.py` (deleted), `tools/late_regatta_soak.py`, `tests/`, and the spec (§2.1 F7, §6.6, §6.7 file tree).

**Design, stated once so every step agrees:**

| Term | Definition |
| --- | --- |
| `t_ms` | `round((frame.t_recv - race.t0_monotonic) * 1000)`. Integer milliseconds since the gun. Frames with `t_ms < 0` are never saved. |
| File | `<data_root>/races/<id>_<name>/frames/{t_ms:08d}.jpg`. One file per `t_ms` per race. |
| `frame` table | `id, race_id, t_ms INTEGER, t_recv REAL, path TEXT, UNIQUE(race_id, t_ms)`. Replaces `capture_frame`. |
| `capture.target_ms` | `round((t_press - delta_used - t0_monotonic) * 1000)`: the selection target in gun-ms. New column. |
| `capture.primary_frame_id` | `REFERENCES frame(id)`, nullable. New column. `capture.primary_image` is kept as a denormalised copy of that frame's `path` so export, web and the UI keep reading it. |
| `race.window_before_ms`, `race.window_after_ms` | Copied from config at `start_race`, so a later config change does not alter which frames belong to a finished race. New columns. |
| Frames of a crossing | `SELECT * FROM frame WHERE race_id=? AND t_ms BETWEEN target_ms - window_before_ms AND target_ms + window_after_ms ORDER BY t_ms`. A frame can belong to several crossings. |

- [ ] **Step 5.1 — Schema and migration.** In `storage.py` add the table and columns above to `SCHEMA` and `_migrate`. Migration of an existing database, in one transaction: (1) create `frame`; (2) for each `capture_frame` row, compute `t_ms` from its `t_recv` and the race's `t0_monotonic`, `INSERT OR IGNORE` into `frame` keeping the row's existing `path` (old files stay where they are); (3) set each capture's `target_ms` from `t_press`, `delta_used` and `t0_monotonic`, and `primary_frame_id` to the `frame` row that came from its `is_primary=1` row; (4) set `race.window_before_ms/after_ms` to the current config values for every existing race; (5) `DROP TABLE capture_frame`. Replace `insert_frame`, `frames_for_capture`, `set_primary` with versions over `frame`; `set_primary` also updates `primary_image`. Add `frame_exists(race_id, t_ms)` and `insert_frames(rows)` (one transaction).
- [ ] **Step 5.2 — `framestore.py`.**

  ```python
  class FrameStore:
      def __init__(self, storage: Storage, race_id: int, race_dir: Path, t0: float): ...
      def t_ms(self, frame: Frame) -> int
      def save(self, frames: list[Frame]) -> list[sqlite3.Row]
          """Write each frame whose t_ms >= 0 and is not yet in the frame table; insert rows in one
          transaction; return the frame rows for ALL given frames (new and pre-existing), ordered by t_ms."""
  def nearest(rows, target_ms) -> sqlite3.Row | None   # pure helper, smallest |t_ms - target_ms|
  ```

  `save` writes files with `Path.write_bytes` to a `.tmp` name then `os.replace`, so a crash leaves no half-written JPEG. It never blocks on anything but disk; it is called only from the deferred-selection timer thread, exactly where `_select_images` writes files today.
- [ ] **Step 5.3 — Controller.** `start_race` creates `self.store = FrameStore(...)` after `create_race` and passes `window_before_ms/after_ms` to `create_race`; `resume_race` recreates it from the row. `_select_images(capture_id, sequence, target, race_dir)` becomes: `frames = buffer.window(target, before_s, after_s)`; `rows = store.save(frames)`; `primary = nearest(rows, target_ms)`; the existing flag recomputation; `storage.set_primary(capture_id, primary['id'])` when `primary` is not None; emit `image_ready` as today. Remove the per-frame `fpath.write_bytes` / `insert_frame` loop and the `captures/` directory. `set_primary(capture_id, frame_id)` (operator promotion) is unchanged in signature. Add `frames_for_capture(capture_id)` passthrough for the review screen.
- [ ] **Step 5.4 — Delete the archive.** Delete `archive.py`; remove `[archive]` from `config.DEFAULTS`, `hallofframe.example.toml`, every test config dict and `tools/late_regatta_soak.make_config`; remove the `archive/` mention from `README.md` and `AGENTS.md`. `grep -rn archive hallofframe tests` must return nothing afterwards.
- [ ] **Step 5.5 — Soak tool.** `tools/late_regatta_soak.py` seeds `capture_frame` rows and `captures/` files; change it to seed `frame` rows and `frames/` files through `FrameStore.save` so the seeded layout is the one the app writes.
- [ ] **Step 5.6 — Spec amendment.** In `hallofframe-finish-timer-spec.md`: §2.1 F7 → "Frames around every crossing are saved once per race, named by time since the gun, so a crossing added or moved after the race can be matched to frames already on disk"; §6.6 → replaced by a short description of `framestore.py` and a note that continuous archiving was removed on 2026-10-06; §6.7 → new file tree and the `frame` table. Mark the §13.3 item as implemented.
- [ ] **Step 5.7 — Tests.** `test_framestore.py`: `t_ms` rounding; `save` skips negative `t_ms`; `save` on an overlapping second window writes no new files and returns the shared rows; `nearest` ties pick the earlier frame. `test_storage.py`: migration from a database created with the *old* schema (keep a copy of the old `SCHEMA` string in the test) ends with the right `frame` rows, `primary_frame_id`, and no `capture_frame` table. `test_controller.py`: two crossings 200 ms apart with a ±500 ms window produce one `frames/` directory whose file count equals the number of distinct frames in the union, and both captures list their frames. Goldens: `primary_image` paths change from `captures/001_w+0000.jpg` to `frames/00005000.jpg`; regenerate with `--update-goldens` and check the diff is only paths.

**Done when:** a fresh race writes only `frames/*.jpg`; `sqlite3 event.db ".tables"` has `frame` and not `capture_frame`; `archive.py` is gone; the review screen scrubber still shows the window frames.

*Deferred hook:* a `[frames] keep_all = false` flag that saves every frame during a race through the same `FrameStore.save` would restore continuous recording with no new naming scheme; the disk-space thresholds would come back with it.

## Phase 6 — Rendering and web must-haves

Goal: split `export.py` into three small renderers that share one HTML skeleton, then add the **last-updated** must-have (spec §13.2) and the text-only index with HTTP caching (spec §13.3 "Minimize web data usage"). The golden tests from phase 0 prove the split changed nothing before the features are added.

**Files touched:** `export.py` → `render/csv.py`, `render/clipboard.py`, `render/html.py`, `render/__init__.py`; `web.py`; `ui/main_window.py` (imports); `tests/test_export.py`, `tests/test_web.py`, `tests/goldens/`.

- [ ] **Step 6.1 — Split, no behaviour change.** Move `format_elapsed`, `parse_elapsed`, `utc_iso`, `local_hms`, `flag_word` to `render/__init__.py`. Move `export_csv`, `export_all_csv`, `_data_rows`, `_COLUMNS`, `_ALL_COLUMNS` to `render/csv.py`. Move `clipboard_data` to `render/clipboard.py`. Move everything else to `render/html.py`. Keep `export.py` as a one-release shim that re-exports every public name with a `DeprecationWarning`. Goldens must pass unchanged.
- [ ] **Step 6.2 — One skeleton.** In `render/html.py` add `page(title: str, body: str, *, width_px: int, footer: str) -> str` holding the `<!DOCTYPE>`, `<head>`, the `<style>` block and the `<script>` tags. Move every inline `style="..."` attribute that repeats (there are about 40) into named classes in one `CSS` string; the colour dict `_C` and the font strings stay. `build_all_html`, `web.build_index` and `web.build_race_page` call `page(...)`. Regenerate goldens with `--update-goldens`; the diff must be markup-only (view it in a browser and confirm the pages look the same). Delete `_about_footer` from `web.py` by moving it to `render/html.py`.
- [ ] **Step 6.3 — Last updated (must-have).** Using `storage.last_updated()` from phase 1: the index header shows "Results updated HH:MM:SS" (local time, from `meta.db_updated_at`); each index row and each race page header shows that race's `updated_at` the same way. Per-race values are plain text, so they add well under 1 KB to the index. Add both to the exported `export_<stamp>.html` as well. Golden update; `test_web.py` asserts the strings appear and change after a bow-number edit.
- [ ] **Step 6.4 — Text-only index and conditional requests.** (a) The index page already carries no images; add a one-line note under its header saying photos are on each race page. (b) In `WebHandler`: compute `Last-Modified` from the relevant `updated_at` (DB-wide for `/`, per race for `/race/<id>` and `/excel/<id>`, file mtime for `/img/`), send it with `ETag: "<updated_at>"`, and answer `304 Not Modified` with no body when `If-None-Match` or `If-Modified-Since` matches. (c) Send `Cache-Control: public, max-age=31536000, immutable` on `/img/` (a frame file never changes once written) and `Cache-Control: no-cache` on HTML so the browser revalidates with the cheap conditional request. (d) Add `loading="lazy"` to every `<img>` on the race page so only visible thumbnails download. `test_web.py`: a second request with the returned `ETag` gets 304; an edit changes the `ETag`.
- [ ] **Step 6.5 — Tests.** `test_export.py` imports from `render.*`; add a test that `export.py` still exposes the old names with a warning. `test_web.py` gains the four cases above.

**Done when:** `export.py` is under 30 lines; `grep -c 'style="' hallofframe/render/html.py hallofframe/web.py` totals under 15; `curl -sI http://127.0.0.1:8080/` shows `ETag` and `Last-Modified`; the index shows the update time.

*Deferred hook:* the play-sequence player (spec §13.3) is a route `/race/<id>/frames.json` returning `[{t_ms, path}]` from `frames_for_capture` for each crossing, and a `<script>` that steps an `<img>` through them; it attaches to `render/html.py` and `web.py` only.

## Phase 7 — Review editing and fake camera

Goal: deliver the **insert / clone / remove any crossing** must-have (spec §13.2) on top of the frame store, and add the fake camera feed (spec §13.3) that lets the whole pipeline run without a phone.

**Files touched:** `controller.py`, `storage.py`, `session.py` (keymap), `ui/review_screen.py`, `ui/crossing_list.py` (deleted-row style), `tools/fake_camera.py` (new), `tests/`.

**Semantics, stated once:**

| Operation | Result |
| --- | --- |
| Remove | Soft delete: `capture.deleted = 1`. The row stays in the list, struck through, with an Undo action. Sequence numbers are never reused (spec §6.7). |
| Restore | `capture.deleted = 0`. |
| Clone | A new `capture` row with `sequence = MAX(sequence)+1` for the race, copying `t_press`, `t_press_wall`, `elapsed_s`, `delta_used`, `target_ms`, `primary_frame_id`, `primary_image`, `image_flag`; `bow_number` and `notes` empty; `debounce_suspect = 0`. Frames are shared through `target_ms`, so the clone sees the same window with no new files. |
| Edit time | Existing `set_crossing_time`; additionally recompute `target_ms`, and if `frames_for_capture` is then empty set `image_flag = 'missing'` and clear the primary so the UI shows "re-pick image". If the live buffer still holds frames for the new target (race still running), run `_select_images` for it. |

- [ ] **Step 7.1 — Storage and controller.** `storage.restore_capture(id)`, `storage.clone_capture(id) -> int`. `controller.remove(capture_id)` (rename of `soft_delete`, emits `capture_deleted`), `controller.restore(capture_id)` (emits `capture_added` with the row), `controller.clone(capture_id) -> Capture` (emits `capture_added`). `update_crossing_time` gains the `target_ms` recomputation above. `undo_last` stays as is for `Ctrl+Z` during recording.
- [ ] **Step 7.2 — Review screen.** Keys in `KEYMAP[AppState.REVIEW]`: `Del` = remove, `Shift+Del` or `U` = restore the selected deleted row, `Ins` or `Shift+D` = clone selected (the clone appears next to its parent because both sort by `elapsed_s`), existing `Shift+←/→` and `Tab` unchanged. `CrossingList` shows deleted rows with `editable=True` struck through and dimmed instead of hiding them (`load_captures` passes `include_deleted=True`); the race screen and race-over screen keep hiding them. A crossing with `image_flag='missing'` after a time edit shows a "no frames at this time" label in the photo pane.
- [ ] **Step 7.3 — Export.** Nothing changes: `race_bundle` already excludes deleted rows and clones are ordinary rows. Verify with the goldens.
- [ ] **Step 7.4 — Fake camera.** `tools/fake_camera.py`: `python -m hallofframe.tools.fake_camera --folder DIR --fps 30 --port 8081 [--loop]` serves `multipart/x-mixed-replace` with a `Content-Length` per part from the JPEGs in `DIR` in filename order, pacing with `time.monotonic()`. Optional `--counter` draws the current `int(time.monotonic()*1000)` onto each frame with PIL so the calibration flow can be exercised too. Point `[stream] url` at it and the app runs unchanged; `transport.check_device()` must be skippable — add `[transport] enabled = true` to config and skip `UsbTransport` entirely when false.
- [ ] **Step 7.5 — End-to-end test.** `tests/test_e2e.py` (`slow`): start `fake_camera` in a thread on an ephemeral port with 90 generated JPEGs; `build_core(config)`; `reader.start()`; wait for `buffer.health()` alive; `controller.start_race(now)`; two `record_crossing` calls 200 ms apart; `scheduler.advance`; assert two captures with primaries, shared `frames/` files, `web.build_race_page` contains both; clone the second and assert three rows in the page. This is the one test that proves the layers still fit.
- [ ] **Step 7.6 — Unit tests.** `test_controller.py`: clone copies the listed fields and gets `MAX+1`; remove then restore round-trips; time edit to a target with no frames flags `missing`. `test_review_screen.py` (`qt`): `Del`/`U`/`Ins` reach the controller; deleted rows render struck through.

*Also in step 7.4:* when adding `[transport] enabled`, add the `[voice]` block from phase 8 step 8.1 with `enabled = false` at the same time, so a laptop set up for the fake camera can later take a microphone without a config-format change.

**Done when:** in review, the operator can delete, restore, and duplicate any row with the keyboard; the fake camera drives a full race on a laptop with no phone attached; `pytest -m slow` includes the end-to-end test and passes in CI.

*Deferred hook:* keypad lanes (spec §13.3) are a `[trigger] lane_keycodes = {"2": "1", "3": "2"}` map read in `build_trigger`; the handler passes the lane to `record_crossing(..., bow_number=lane)` and `insert_capture` stores it. Nothing else moves.

## Phase 8 — Voice annotation (optional)

Goal: record one audio track per race, let the operator hear the seconds around a crossing while reviewing it, and optionally pre-fill bow numbers from a local speech model. Spec §13.3 defines it in two stages; stage 2 is strictly additive to stage 1. This phase depends on phases 1, 3, 4 and 5 being merged.

**Files touched:** `voice.py` (new), `main.py`, `storage.py`, `config.py`, `hallofframe.example.toml`, `session.py` (keymap), `ui/review_screen.py`, `ui/crossing_list.py`, `INSTALL.md`, `tests/`.

**Design, stated once:**

| Term | Definition |
| --- | --- |
| Track | `<data_root>/races/<id>_<name>/audio.wav`, 16 kHz mono PCM, one per race. |
| `race.audio_path`, `race.audio_t0_offset_s` | Path of the track, and (monotonic time of the first recorded sample) minus `t0_monotonic`. Negative when recording started before the gun. Columns added in phase 1 step 1.4. |
| Offset of a crossing in the track | `capture.elapsed_s + race.audio_t0_offset_s` seconds. |
| Playback window | From offset minus 3 s to offset plus 5 s. Both numbers are config values. |
| `capture.bow_suggested`, `capture.bow_source` | Stage 2 output. `bow_source` is `voice` when the shown bow came from transcription and nobody edited it, `operator` otherwise. Columns added in phase 1 step 1.4. |
| Transcription window | Spoken numbers inside offset minus 1 s to offset plus 4 s. One number gives a suggestion; zero or several give blank. |

Recording and playback both go through `ffmpeg` / `ffplay` subprocesses, which `INSTALL.md` already requires. Do not add `PySide6-Addons` (QtMultimedia); the spec installs `PySide6-Essentials` only.

- [ ] **Step 8.1 — Config.** Add `[voice]` to `config.DEFAULTS` and the example file: `enabled = false`, `input = "default"` (PulseAudio/PipeWire source name), `play_before_s = 3.0`, `play_after_s = 5.0`, `transcribe = false`, `model = "base"`, `transcribe_before_s = 1.0`, `transcribe_after_s = 4.0`.
- [ ] **Step 8.2 — `voice.py`: `AudioRecorder`.** A class with `start(race_dir, t0) -> None` and `stop() -> tuple[str, float] | None` (path, offset). `start` launches `ffmpeg -f pulse -i <input> -ac 1 -ar 16000 -y audio.wav` with `subprocess.Popen` and records `time.monotonic()` just before `Popen` returns as the start estimate (a few hundred ms of error is fine for an 8 s playback window). `stop` writes `q` to stdin, waits up to 5 s, then kills. Any failure logs a `voice` warning and returns `None`; a race without audio is normal. `main.py` subscribes `recorder.start` to the controller's `race_started` event and `recorder.stop` to `race_ended`, then calls `storage.set_race_audio(race_id, path, offset)`. Nothing in `controller.py` changes.
- [ ] **Step 8.3 — Playback in review.** Add `Key("P", "Play audio", "_play_audio")` to `KEYMAP[AppState.REVIEW]`. `ReviewScreen._play_audio` launches `ffplay -nodisp -autoexit -ss <offset - before> -t <before + after> <path>` for the selected crossing; a second press while playing kills the process. Show an "audio" marker in the race-over summary when the race has a track. If `audio_path` is null, toast "No audio for this race".
- [ ] **Step 8.4 — Stage 2: `voice.py`: `Transcriber`.** `transcribe(race_id) -> int` returns the number of suggestions written. `main.py` starts it in a `threading.Thread` on `race_ended` only when `[voice] transcribe = true`. It checks `controller.running` before each crossing and stops if a new race has started, so it never competes with ingest on the two-core laptop. `faster-whisper` is an optional import; if missing, log once and return 0. For each non-deleted crossing: cut the transcription window with `ffmpeg -ss -t`, run the model with a digits-biased prompt, parse integers, apply the one-number rule, write `bow_suggested` and, if `bow_number` is empty, `bow_number` with `bow_source='voice'`, all through `storage.update_capture` so `updated_at` and the web page refresh. Notify the UI through the existing `events` hook with a new kind `bow_suggested`.
- [ ] **Step 8.5 — Suggested-value styling.** `CrossingList` rows with `bow_source='voice'` show the bow field in the amber accent with tooltip "from voice — press Enter to confirm". Editing or confirming the field sets `bow_source='operator'`. The CSV and HTML exports gain a `bow_source` column.
- [ ] **Step 8.6 — Web.** No audio is served by default (spec §13.3 data-usage item). Nothing to do unless the owner asks.
- [ ] **Step 8.7 — Tests.** `test_voice.py`: `AudioRecorder` with `ffmpeg` replaced by a fake executable on `PATH` that writes a 1 s WAV — `stop` returns the path and a plausible offset; a missing binary returns `None` and the race still ends. `Transcriber` with a stub model returning fixed text: "fourteen" gives 14, "7 or 8" gives blank, silence gives blank; it does not run while `controller.running`. `test_review_screen.py` (`qt`): `P` launches the player with the expected `-ss` for a known offset (patch `subprocess.Popen`).

**Done when:** with `[voice] enabled = true` and a microphone, a race produces `audio.wav`; `P` in review plays the seconds around the selected crossing; with `transcribe = true` the bow fields fill in amber after the race ends and the next race's fps does not drop.

*Open questions for the owner:* headset or laptop microphone (the plan assumes a headset in `[voice] input`); whether the model download (about 150 MB for `base`) is acceptable in `INSTALL.md`'s offline pre-staging; whether a confirmed voice suggestion should keep `bow_source='voice'` for the jury's information. *Defaults:* headset; yes; no (confirmation makes it `operator`).

## Things that must not change

These are the parts of the system the spec spent most of its words protecting. A refactor that touches them has stopped being a refactor.

| What | Where | Why |
| --- | --- | --- |
| `t_press` and `t0` come from evdev kernel timestamps, never from `time.monotonic()` in a Qt slot | `trigger.py`, `main.py` bridge | Spec §5.3: the two timestamps must travel the same path or the reaction-time cancellation fails. The `Space`/`Enter` Qt shortcuts are a documented degraded fallback, not the normal path. |
| `record_crossing` only computes two numbers and enqueues | `controller.record_crossing` | Spec §6.5: nothing on the trigger path touches disk or SQLite. |
| Image selection is deferred by `window_after_ms + margin` | `controller._handle_capture` | Spec §6.5: the frames after the target do not exist yet at press time. |
| A time is never discarded: stream down → `image_flag='missing'`; suspect double press → recorded and flagged | `controller._handle_capture`, `trigger._dispatch` | Spec §6.5 edge-case table, §6.4 debounce. |
| The MJPEG parse loop, including the 4 MB cap and the marker-walk EOI path | `mjpeg.py` | Spec §6.2 and Appendix C list the silent failures each line prevents. |
| `FrameBuffer.window()` is bounded by seconds, not frame count | `framebuffer.py` | Spec §6.3. |
| `PRAGMA journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`; soft delete; `MAX(sequence)+1` including deleted rows | `storage.py` | Spec §6.7 (N4, F5). |
| The web server stays a separate process with its own read-only connection | `web.py` | Spec §8 and `AGENTS.md`: viewer load must not reach the timing thread. |
| `config.toml` is never written by the application | everywhere | Spec §6.7, §8. |
| No modal dialog while `Session.phase` is ARMED or RECORDING | `ui/` | Spec §7.5. The roster dialogs are already blocked in those phases; keep that check in `Session`. |

If a phase step appears to require breaking one of these, the step is wrong. Stop and report it.

## Definition of done and open questions

The plan is complete when all three must-haves are usable from the keyboard, the fake camera drives a full race in CI, and `main_window.py` is under 350 lines with the `Session`/`Roster`/`Render`/`FrameStore` modules each tested without Qt.

| Phase | Delivers | Checked by |
| --- | --- | --- |
| 0 | Fixtures, goldens, CI, deterministic controller tests | CI green on 3.12 and 3.14 |
| 1 | One calibration loader, controller events, `updated_at`, bundle queries, placeholder voice columns | `grep` checks in the phase; goldens unchanged |
| 2 | `Roster` with skip, move, add, rename; 600 lines removed | `roster.py` + dialog under 600 lines |
| 3 | `Session`, `KEYMAP`, resume-after-restart banner | `main_window.py` under 350 lines |
| 4 | One `CrossingList`, `images.load_scaled`, **fastest-first order** | first finisher at top on a fresh DB |
| 5 | `FrameStore`, `frame` table, archive deleted, spec amended | fresh race writes only `frames/*.jpg` |
| 6 | `render/` split, **last-updated on web**, ETag/304, lazy images | `curl -sI` shows caching headers |
| 7 | **Remove/restore/clone any crossing**, fake camera, end-to-end test | `pytest -m slow` passes in CI |
| 8 (optional) | Audio track per race, play-around-crossing in review, voice bow suggestions | `audio.wav` written; `P` plays; fps steady during transcription |

Each phase is one pull request. Phases 0–4 may be reviewed by diffing behaviour only (goldens and existing tests). Phases 5–7 add behaviour and need a manual run on the laptop with the fake camera before merging.

**Open questions for the project owner** — each has a default that the implementer should use if no answer arrives:

- [ ] Phase 2: should the `Shift+↑/↓` move keys also work on the RACE\_OVER screen, or only in READY? *Default: READY only.*
- [ ] Phase 3: on startup with an un-ended race, should Resume be offered when the race is older than a configurable number of hours? *Default: always offer; the operator decides.*
- [ ] Phase 4: fastest-first is now fixed on all three screens per spec §13.2. During recording this means a new crossing appears wherever its time sorts, not at the top. Is that acceptable, or should the live Race screen keep a separate "last press" highlight (the `LastCapturePanel` already shows it)? *Default: rely on `LastCapturePanel`; no list-order exception.*
- [ ] Phase 5: when migrating an existing database, should the old `captures/*.jpg` files be moved into `frames/` or left in place? *Default: left in place; the `frame.path` column points at them.*
- [ ] Phase 6: should per-race "updated" also appear on the exported standalone HTML, or only on the live server? *Default: both.*
- [ ] Phase 7: should a clone inherit the parent's bow number? *Default: no; a clone exists to be edited.*

## Phase 9 — §13.3 nice-to-haves (ROI, web thumbnails, player)

Built 2026-10-09 as three packages on two branches (`feat/roi-zoom`, then
`feat/web-thumbs-player`), merged A → B+C. The exclusive file-ownership table
kept the branches disjoint; nothing under `hallofframe/{trigger,mjpeg,
framebuffer,controller,framestore}.py` is in the diff. The index and the
offline export stayed byte-identical (goldens enforce it).

- **Package A — Zoom / area of interest (app only).** `Storage.get_setting`/
  `set_setting` read/write `ui.`-prefixed keys in `meta` without `_touch`, so a
  cosmetic setting never churns the web ETag. `images.load_view` applies a
  process-wide ROI via a DCT-domain `setClipRect` + `setScaledSize` crop (with a
  full-decode fallback); `PreviewWidget` gains draw-zoom mode and a full-frame
  image-normalised finish line; `Z`/`Shift+Z` are READY-only keymap entries.
  *Done when:* `grep -n "load_scaled(" hallofframe/ui/*.py` lists only
  `images.py` and `calibration_dialog.py`; `meta` shows `ui.roi` and
  `ui.finish_line_x` after a setup session with `db_updated_at` unchanged;
  `Z` does nothing in ARMED/RECORDING/REVIEW.
- **Package B — Minimize web data usage.** `[web]` gains `thumb_width`/
  `thumb_quality`/`cache_dir`; the web process builds and caches thumbnails
  itself under `<data_root>/web-cache/thumbs/<width>/` (`ThumbCache`, no SQLite
  writes) and serves them from `/thumb/` with the width folded into the ETag.
  Race pages send no images by default: cards carry `data-thumb`/`data-full`
  and a `Show photos` button (state in `localStorage`); the static page has zero
  `<img>`.
  *Done when:* `curl -s .../race/<id> | grep -c '<img'` prints `0`; `/thumb/`
  answers `image/jpeg` ≤ 480 px with `Cache-Control: … immutable`; the index and
  `export_<stamp>.html` are unchanged.
- **Package C — Play crossing images as a sequence.** The B viewer overlay gains
  a `Play`/`Pause` button, interval `<select>` and `Loop`, plus a header `Play`
  that opens the overlay at card 1; it steps one primary per crossing in finish
  order (`data-pos` 1..n, fastest first) using only `/thumb/` requests and waits
  for each image's `load` before scheduling the next. No server-side video, no
  new route.
  *Done when:* the player steps through primaries in finish order using only
  `/thumb/` requests.
