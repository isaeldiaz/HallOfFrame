# Sheet 23 — calibration dialog tabs (Latency | Reaction) + evdev press hook

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/ui/calibration_dialog.py`, `hallofframe/ui/main_window.py` (`__init__` one attribute, `on_evdev_start`, `on_evdev_crossing`, `_calibrate`, one new `_clear_press_hook`), `tests/test_main_window_calibrate.py` (create).
**Read first:** `hallofframe/ui/calibration_dialog.py` (all), `hallofframe/ui/reaction_page.py` (`ReactionPage` signals and constructor), `hallofframe/ui/main_window.py` lines 82–105 (`__init__` head), 615–660 (`on_evdev_start`, `on_evdev_crossing`), 853–863 (`_calibrate`), `tests/test_main_window_scratch.py` (how a `MainWindow` is built in tests), `tests/test_review_audio.py` (fixture).

### Goal
Make the calibration dialog a tabbed window — tab 1 is the existing latency flow untouched, tab 2 is the `ReactionPage` — and route kernel-timestamped presses to it while it is open. The dialog is only opened from READY/STREAM_DOWN/RECALIBRATE (`_calibrate` already refuses ARMED/RECORDING), where the trigger device is **not grabbed**: every press reaches both evdev (via the bridge → `on_evdev_crossing`/`on_evdev_start`) and Qt. The page ignores the Qt copy when evdev is active (sheet 22); this sheet delivers the evdev copy.

### Spec

`calibration_dialog.py`:
- Rename the existing dialog body into `class LatencyPage(QWidget)` with the same constructor `(buffer, data_root, config, parent=None)` and the same methods verbatim (`_capture`, `_capture_impl`, `_begin_capture`, `_sample`, `_finish_capture`, `_finish`). Where it called `self.hide()` / `self.show()` / `self.accept()` on the dialog, it now emits `run_started` / `run_finished` / `finished_ok` signals (`Signal()` each); `QMessageBox` parents stay `self`.
- New `CalibrationDialog(QDialog)`:
  ```python
  def __init__(self, buffer, data_root, config, parent=None, *, evdev: bool = False):
  ```
  `QTabWidget` with `self.latency = LatencyPage(...)` ("Latency") and `self.reaction = ReactionPage(data_root, config.section("voice"), evdev=evdev)` ("Reaction"). Connect each page's `run_started` → `self.hide`, `run_finished` → `self.show`; `latency.finished_ok` → `self.accept`; `reaction.notify` → `self.notify` (new `Signal(str)` on the dialog). Window title "Calibrate". Same dark stylesheet line as before.
  ```python
  def on_press(self, t: float) -> None:   # evdev press while open → reaction page
      self.reaction.on_press(t)
  def closeEvent(self, event):            # abort a running reaction trial
      self.reaction.abort(); super().closeEvent(event)
  ```
  `keyPressEvent`: `Qt.Key_Escape` → `self.reaction.abort()` then default.

`main_window.py`:
- `__init__`: `self.press_hook = None` after `self.end_evdev_active = False`. comment: `set while the calibration dialog is open: evdev presses go to it instead of start/crossing`.
- `on_evdev_start` and `on_evdev_crossing`: first statement
  ```python
  if self.press_hook is not None and self.session.phase not in (Phase.ARMED, Phase.RECORDING):
      self.press_hook(t_press)
      return
  ```
- `_calibrate`: `dlg = CalibrationDialog(self.buffer, self.config.data_root, self.config, self, evdev=self.evdev_active)`; `dlg.notify.connect(self._show_toast)`; `self.press_hook = dlg.on_press`; `dlg.finished.connect(self._clear_press_hook)`; `dlg.destroyed.connect(self._clear_press_hook)`; keep `WA_DeleteOnClose` + `show()`.
- `def _clear_press_hook(self, *_): self.press_hook = None`.

### Tests

`tests/test_main_window_calibrate.py` (create; `qt` marker; build the window exactly as `tests/test_main_window_scratch.py` does, with `trigger={"device_path": ""}`; `from unittest import mock`):
| Test | Steps | Expected |
|---|---|---|
| `test_calibrate_opens_tabs` | `win._calibrate()`; `dlg = win.findChild(CalibrationDialog)` | `dlg is not None`; tab texts `== ["Latency", "Reaction"]`; `dlg.reaction.evdev is False` (Qt fallback window) |
| `test_evdev_press_routed_to_dialog` | `win.evdev_active = True`; `win._calibrate()`; `dlg.reaction.on_press = mock.Mock()`; `win.controller.record_crossing = mock.Mock()`; `win.on_evdev_crossing(5.0, 57)`; `win.on_evdev_start(6.0)` | `on_press` called with `5.0` then `6.0`; `record_crossing` not called; `win.session.phase` unchanged |
| `test_hook_cleared_on_close` | `win._calibrate()`; `dlg.close()`; `qapp.processEvents()` | `win.press_hook is None`; `win.on_evdev_crossing(5.0, 57)` reaches `record_crossing` (mock called once) |
| `test_hook_bypassed_while_armed` | `win._calibrate()` (hook set); `win.session.arm()` (or the window's arm path); `win.on_evdev_start = ...` — simpler: set `win.press_hook = mock.Mock()`; `win.session.arm()`; `win.on_evdev_crossing(5.0, 57)` | `press_hook` not called (the press went to the start path: `controller.start_race` mock called, or `session.phase` advanced) |
| `test_calibrate_refused_during_race` | `win._last_state = AppState.RECORDING`; `win._calibrate()` | no `CalibrationDialog` child; `win.press_hook is None` |
| `test_dialog_evdev_flag` | `win.evdev_active = True`; `win._calibrate()` | `dlg.reaction.evdev is True` |

**Commit:** `feat(calibration): tabbed Calibrate dialog (Latency | Reaction) with evdev press routing (step 23)`
