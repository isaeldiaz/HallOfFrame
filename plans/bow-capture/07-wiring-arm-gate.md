# Sheet 07 — wiring: MainWindow + main.py, arm gate

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/ui/main_window.py`, `hallofframe/main.py`, new `tests/test_main_window_scratch.py`.
**Read first:** `main.py` (`_TriggerBridge`, `_on_controller_event`), `ui/main_window.py` (`__init__`, `_on_controller_event`, `_apply_state`, `_arm_start`, `on_capture`), `ui/scratchpad_widget.py`, `ui/race_screen.py` (`set_scratchpad`, `focus_scratchpad`, `set_bows`), `trigger.py` (`is_internal_keyboard`), `tests/test_review_screen.py` lines 42–95 (MainWindow test fixture).

### Spec
`main_window.py`:
1. `__init__`, after `self.recording = RaceScreen()`:
   ```python
   self.scratchpad = None
   if bool(config.section("scratchpad")["enabled"]):
       from ..ui.scratchpad_widget import ScratchpadWidget
       self.scratchpad = ScratchpadWidget()
       self.recording.set_scratchpad(self.scratchpad)
       self.scratchpad.committed.connect(self._scratch_commit)
       self.scratchpad.undo_requested.connect(self.controller.undo_scratch)
   ```
2. `_scratch_commit(self, text)`: `if not self.controller.add_scratch(text): self.scratchpad.flash()`.
3. `on_scratch_changed(self, payload: dict)`: `self.scratchpad.set_counts(payload["typed"], payload["crossings"])`; `self.recording.set_bows(payload["bows"])`. No-op when `self.scratchpad is None`.
4. `_on_controller_event`: `elif kind == "scratch_changed": self.on_scratch_changed(payload)`.
5. `_apply_state`: inside the `if state == AppState.RECORDING:` branch, after `clock_timer.start(50)`: `self.recording.focus_scratchpad()`.
6. `_arm_start`: before `self.session.arm()`:
   ```python
   if self.scratchpad is not None:
       from ..trigger import is_internal_keyboard
       if is_internal_keyboard(self.config.section("trigger")["device_path"]):
           self._show_toast("Scratchpad needs the external trigger button: "
                            "[trigger] device_path is the laptop keyboard (§13.3).")
           return
   ```
7. Race over (spec §13.3.2: operator 2 reads the mismatch *after* the heat, but `_apply_state(RACE_OVER)` swaps the race screen out): keep the last payload on the widget (`self._scratch_last = payload` in `on_scratch_changed`), and in `on_race_ended`, after the summary: `if self.scratchpad is not None and self.scratchpad.mismatch(): typed, crossings = self._scratch_last["typed"], self._scratch_last["crossings"]; self._show_toast(f"Scratchpad: {typed} typed / {crossings} crossings — fix bow numbers in review", timeout_ms=0)`.

`main.py`:
- `_TriggerBridge`: add `scratch_changed = Signal(object)`.
- connect `bridge.scratch_changed.connect(win.on_scratch_changed)`.
- `_on_controller_event`: `elif kind == "scratch_changed": bridge.scratch_changed.emit(payload)`.

### Tests — `tests/test_main_window_scratch.py` (qt)
Fixture: copy `arm_env` from `tests/test_arm_disarm.py` (lines ~31–43: NO pre-seeded race — `test_review_screen.py`'s fixture seeds an un-ended race and sets `controller.race_id`, which makes `start_race` raise `RaceStateError`). Config: `config(scratchpad={"enabled": True}, timing={"viewing_mode": "screen", "image_mode": "off"}, trigger={"device_path": "", "grab_device": False})`; `buffer.health = lambda: (True, 30.0, 0.1)`; `win.show(); qapp.processEvents()`; teardown `win.status_timer.stop(); controller.stop(); win.close()`.
Controller events must reach Qt on the GUI thread, so the fixture replaces the hook and the test pumps it: after building `win`: `self.events = []; controller.events = lambda k, p: self.events.append((k, p))`; helper
```python
def pump(self):
    self.controller._queue.join()
    for k, p in list(self.events): self.win._on_controller_event(k, p)
    self.events.clear(); self.app.processEvents()
```
Helper `start()`: `win._arm_start(); win.on_evdev_start(1000.0); pump()`.

| Test | Steps | Expected |
|---|---|---|
| `test_widget_absent_when_disabled` | config enabled False | `win.scratchpad is None`; `win.recording.findChildren(ScratchpadWidget) == []` |
| `test_focus_on_recording` | `start()` | `win._last_state is AppState.RECORDING`; `qapp.focusWidget() is win.scratchpad.field` |
| `test_commit_flows_to_db_and_counters` | `start()`; `controller.record_crossing(1001.0)`; `pump()`; `win.scratchpad.committed.emit("14")`; `pump()` | capture seq 1 bow `"14"`; `typed_lbl.text() == "1"`, `cross_lbl.text() == "1"`; race list row shows `"14"` |
| `test_new_capture_keeps_focus` | `start()`; `controller.record_crossing(1001.0)`; `pump()` | `qapp.focusWidget() is win.scratchpad.field` |
| `test_arm_refused_on_internal_keyboard` | `monkeypatch.setattr("hallofframe.trigger.is_internal_keyboard", lambda p: True)`; `_arm_start()` | `session.phase is Phase.IDLE`; toast text contains `"external trigger"` |
| `test_arm_allowed_when_disabled_even_on_internal_keyboard` | enabled False + same monkeypatch | phase ARMED |
| `test_return_claimed_by_field_in_recording` | `start()`; send `QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Return, Qt.NoModifier)` to the field | `event.isAccepted()`; no new capture; `controller.running` |
| `test_mismatch_toast_at_race_end` | `start()`; 2 crossings; `committed.emit("14")`; `pump()`; `controller.end_race()`; `pump()` | toast text contains `"1 typed / 2 crossings"` |
| `test_no_toast_when_counts_match` | `start()`; 1 crossing; `committed.emit("14")`; `pump()`; `end_race()`; `pump()` | no scratchpad toast |

**Commit:** `feat(app): wire the scratchpad, refuse arming on the internal keyboard (§13.3 step 07)`
