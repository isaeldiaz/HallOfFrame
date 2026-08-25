"""Main window (REDESIGN-PLAN §1, §3–§7; spec §7).

One full-screen surface that changes with application state. A single ``AppState``
enum (``ui/state.py``) drives everything: the state band on top, the centre pane
(ready / armed / recording / race-over / review), and the key bar at the bottom.
The red banner is retired as a general-purpose channel — warnings go to a
bottom-right toast, the band is persistent, and capture confirmation is the
last-capture panel flash.

Timing is untouched: ``t_press`` still comes from evdev kernel timestamps
(``trigger.py``); this window only renders and orchestrates. No modal dialogs
during a race (§7.5).
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QLineEdit, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..controller import CaptureController, calibration_status
from ..export import clipboard_data
from ..framebuffer import FrameBuffer
from ..races import load_race_names, write_example
from ..results import append_race
from ..ui import styles
from ..ui.calibration_dialog import CalibrationDialog
from ..ui.misc_screens import ArmedScreen, RaceOverScreen
from ..ui.race_screen import RaceScreen
from ..ui.ready_screen import ReadyScreen
from ..ui.review_screen import ReviewScreen
from ..ui.state import AppState, derive_state
from ..ui.widgets import KeyBar, StateBand, Toast


class _ExcelMimeData(QMimeData):
    """Clipboard payload carrying both TSV and an Excel-compatible HTML table."""

    def __init__(self, tsv: str, markup: str):
        super().__init__()
        self._tsv = tsv
        self._markup = markup
        self.setData("text/html", markup.encode("utf-8"))

    def formats(self) -> list[str]:
        return ["text/html", "text/plain"]

    def hasText(self) -> bool:
        return True

    def text(self) -> str:
        return self._tsv


def keycode_names(codes) -> str:
    """Human-readable names for a list of Linux keycodes (e.g. [88] -> 'F12')."""
    import evdev.ecodes as ec
    names = []
    for c in codes:
        name = ec.KEY.get(int(c), "")
        names.append(name.removeprefix("KEY_") if name else str(c))
    return ", ".join(names) if names else str(codes)


class MainWindow(QMainWindow):
    def __init__(self, config, controller: CaptureController,
                 buffer: FrameBuffer, trigger=None):
        super().__init__()
        self.config = config
        self.controller = controller
        self.buffer = buffer
        self.trigger = trigger

        self.setWindowTitle("HallOfFrame — Finish-Line Timer")
        self.resize(1280, 720)

        root = QWidget()
        root.setObjectName("Root")
        self._root_lay = QVBoxLayout(root)
        self._root_lay.setContentsMargins(0, 0, 0, 0)
        self._root_lay.setSpacing(0)

        # --- state band (top) ---
        self.band = StateBand()
        self._root_lay.addWidget(self.band)

        # --- centre pane (one page per state) ---
        self.center = QStackedWidget()
        self.ready = ReadyScreen(buffer)
        self.ready.finish_line_changed.connect(self._finish_line_changed)
        self.armed = ArmedScreen(on_start=lambda: self.on_evdev_start(time.monotonic()))
        self.recording = RaceScreen()
        self.race_over = RaceOverScreen()
        self.center.addWidget(self.ready)
        self.center.addWidget(self.armed)
        self.center.addWidget(self.recording)
        self.center.addWidget(self.race_over)
        self._root_lay.addWidget(self.center, 1)

        # --- key bar (bottom) ---
        self.keybar = KeyBar()
        self.keybar.setStyleSheet(f"background:{styles.PANEL};"
                                  f" border-top:1px solid {styles.PANEL_BORDER};")
        self._root_lay.addWidget(self.keybar)

        self.setCentralWidget(root)

        # Toast floats over the centre pane (bottom-right), as a child of root
        # so move() is parent-relative and it overlays the stacked pages.
        self.toast = Toast(root)
        self._toast_initialized = False

        # --- timers ---
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(500)

        # --- state fields ---
        self._armed = False
        self._race_over = False
        self._reviewing = False
        self._review_screen: ReviewScreen | None = None
        self._cal_ok = True
        self._cal_detail = ""
        self._cal_check_at = 0.0
        self._last_capture: int | None = None
        self._last_state = None
        self._advance_race_default = False
        self._race_names: list[str] = []
        self._saved_race_ids: set[int] = set()
        # Set by main.py: the worker→UI bridge used to marshal the results-save
        # outcome back onto the GUI thread (§6.4). None when running headless.
        self._results_bridge = None
        # Set by main.py to keep the trigger device grab tied to state (§9/Opt A).
        self.on_state_changed = None

        self._populate_race_selector()
        self.ready.set_races(self._race_names,
                             recorded=self.controller.storage.race_names())
        self.ready.race_selected.connect(self._on_race_selected)
        self.ready.set_finish_line(float(self.config.section("ui")["finish_line_x"]))
        self._set_trigger_label()

        self._connect_controller()
        self._install_shortcuts()
        self._apply_state(derive_state(self.controller, self.buffer,
                                       self._cal_ok, self._armed,
                                       self._reviewing, self._race_over))
        self._update_status()

    # ------------------------------------------------------------------ wiring
    def _connect_controller(self) -> None:
        # capture_added and image_ready are routed through the bridge in main.py;
        # these defaults keep the window usable outside Qt (tests).
        self.controller.signal_capture_added = self.on_capture
        self.controller.signal_image_ready = self.on_image_ready
        self.controller.signal_race_ended = self.on_race_ended
        self.controller.signal_warning = self._show_toast

    def _set_trigger_label(self) -> None:
        trig = self.config.section("trigger")
        start = keycode_names(trig["start_keycodes"])
        end = keycode_names(trig["end_keycodes"])
        device = trig["device_path"] or "Qt fallback"
        self.armed.set_trigger_label(
            f"({device}). {end} or Esc to disarm.")

    def _install_shortcuts(self) -> None:
        # ApplicationShortcut context: these never lose to a focused button (§4).
        def sc(key, fn):
            return QShortcut(QKeySequence(key), self, activated=fn,
                             context=Qt.ApplicationShortcut)
        sc("Ctrl+S", self._arm_start)
        sc("Ctrl+Z", self.controller.undo_last)
        sc("Ctrl+Q", self._quit)
        sc("Esc", self._esc)
        sc("F12", lambda: self.on_evdev_end(time.monotonic(), 88))
        letters = [sc("C", self._calibrate), sc("E", self._export),
                   sc("R", self._open_review), sc("N", self._next_race),
                   sc("Ctrl+E", self._save_race_and_copy)]
        race = [sc("Return", lambda: self.on_evdev_start(time.monotonic())),
                sc("Enter", lambda: self.on_evdev_start(time.monotonic())),
                sc("Space",
                   lambda: self.controller.record_crossing(time.monotonic()))]
        # Beating a focused button (§4) also means beating a focused text field:
        # every shortcut whose key can be typed has to stand down while a bow
        # number or race name is being entered, or the character never arrives.
        self._typable_shortcuts = letters + race
        # The race controls are unreachable from REVIEW anyway (_arm_start
        # refuses it) and Enter/Space belong to the review screen there — a real
        # trigger press still arrives through the evdev bridge, not a shortcut.
        self._race_shortcuts = race
        app = QApplication.instance()
        if app is not None:
            # Bound method, not a lambda: Qt drops the connection when this
            # window is destroyed instead of calling into a dead object.
            app.focusChanged.connect(self._focus_changed)

    def _focus_changed(self, _old=None, _new=None) -> None:
        self._sync_shortcuts()

    def _sync_shortcuts(self) -> None:
        """Silence the typable shortcuts while typing, and in REVIEW."""
        typing = isinstance(QApplication.focusWidget(), QLineEdit)
        for shortcut in self._typable_shortcuts:
            shortcut.setEnabled(not typing)
        for shortcut in self._race_shortcuts:
            shortcut.setEnabled(not typing and self._last_state
                                is not AppState.REVIEW)

    # ---------------------------------------------------------------- state band
    def _health_labels(self) -> list[str]:
        if self._last_state == AppState.RECORDING:
            return ["Stream", "Archive", "Disk"]
        return ["Stream", "Δ latency", "Disk"]

    def _update_health(self) -> None:
        _, fps, _ = self.buffer.health()
        fps = self.trigger.fps if (self.trigger and fps <= 0) else fps
        free = shutil.disk_usage(self.config.data_root).free / 1e9
        self.band.set_health(self._health_labels())
        self.band.set_health_value("Stream", f"{fps:.1f} fps",
                                   styles.GREEN_TEXT if fps > 0 else styles.RED_TEXT)
        self.band.set_health_value("Disk", f"{int(free)} GB")
        if self._last_state == AppState.RECORDING:
            self.band.set_health_value(
                "Archive", "writing" if self.controller.running else "stopped",
                styles.GREEN_TEXT if self.controller.running else styles.TEXT_DIM)
        else:
            lag = self._cal_latency_ms()
            self.band.set_health_value(
                "Δ latency", f"{lag:.0f} ms" if lag else "—")

    def _cal_latency_ms(self) -> float | None:
        import json as _json
        p = self.config.data_root / "calibration.json"
        if not p.exists():
            return None
        try:
            return float(_json.loads(p.read_text()).get("latency_median_ms", 0.0))
        except Exception:
            return None

    # ------------------------------------------------------------------- status
    def _update_status(self) -> None:
        self._update_health()
        self._recompute_state()

    def _recompute_state(self) -> None:
        # Calibration is only gateable in water mode; screen mode cancels
        # latency entirely and needs no calibration.json (§5.4, §8).
        if self.config.section("timing")["viewing_mode"] == "screen":
            self._cal_ok, self._cal_detail = True, ""
        # Re-check calibration ~every 3 s (decodes a frame for resolution).
        elif time.monotonic() - self._cal_check_at >= 3.0:
            self._cal_check_at = time.monotonic()
            self._cal_ok, self._cal_detail = calibration_status(self.config, self.buffer)
        state = derive_state(self.controller, self.buffer, self._cal_ok,
                             self._armed, self._reviewing, self._race_over)
        if state != self._last_state:
            self._apply_state(state)
        else:
            # keep the band race name / fix fresh even if state unchanged
            self._refresh_band(state)

    def _race_name(self) -> str:
        if self.controller.race_id is not None:
            row = self.controller.storage.get_race(self.controller.race_id)
            if row and row["name"]:
                return row["name"]
        return self.ready.current_race_name()

    def _refresh_band(self, state: AppState) -> None:
        fix = ""
        if state == AppState.STREAM_DOWN:
            fix = "No frames arriving — check the camera app and the USB cable"
        elif state == AppState.RECALIBRATE:
            fix = f"{self._cal_detail} — press C to calibrate"
        self.band.set_state(state, self._race_name(), fix)

    # ---------------------------------------------------------------- apply state
    def _refresh_race_selector(self) -> None:
        """Re-read recorded races so completed ones turn gray immediately,
        without disturbing the operator's current selection (overwrite stays
        available)."""
        self.ready.refresh_recorded(self.controller.storage.race_names())

    def _apply_state(self, state: AppState) -> None:
        self._last_state = state
        pages = {
            AppState.READY: self.ready, AppState.ARMED: self.armed,
            AppState.RECORDING: self.recording,
            AppState.RACE_OVER: self.race_over, AppState.REVIEW: self._review_screen,
        }
        page = pages.get(state)
        if state in (AppState.STREAM_DOWN, AppState.RECALIBRATE):
            page = self.ready
        if page is not None and self.center.currentWidget() is not page:
            self.center.setCurrentWidget(page)

        # clock timer only during recording
        if state == AppState.RECORDING:
            self.recording.begin(self.controller.t0)
            self.clock_timer.start(50)
        else:
            self.recording.end()
            self.clock_timer.stop()

        self._refresh_band(state)
        self._apply_keybar(state)
        self._sync_shortcuts()
        if state == AppState.READY:
            if self._advance_race_default:
                # A race just finished: jump the default to the next race in the
                # roster that is not yet recorded (skipping the one we just ran).
                self.ready.select_first_unrecorded()
                self._advance_race_default = False
            self._refresh_race_selector()
            self.ready.set_checks(self._pre_race_checks())
            self.ready.set_lag(self._measure_lag())
        if self.on_state_changed is not None:
            self.on_state_changed(state)

    def _apply_keybar(self, state: AppState) -> None:
        kb = self.keybar
        kb.clear()
        if state == AppState.RECORDING:
            kb.add("SPACE", "Record crossing", True,
                   lambda: self.controller.record_crossing(time.monotonic()))
            kb.add("F12", "End race", callback=self._end_race)
            kb.add("Ctrl+Z", "Undo last", callback=self.controller.undo_last)
            kb.set_note(self._grab_note())
        elif state == AppState.ARMED:
            trig = self.config.section("trigger")
            end = keycode_names(trig["end_keycodes"])
            kb.add(end, "Disarm", callback=lambda: self.on_evdev_end(time.monotonic(), 88))
            kb.add("Esc", "Cancel", callback=self._esc)
            kb.set_note(f"Keyboard grabbed while armed — {end} or Esc disarms "
                        "and releases it, then quit normally.")
        elif state == AppState.REVIEW:
            kb.add("↑/↓", "Select crossing", True)
            kb.add("Tab", "Next bow field")
            kb.add("Del", "Soft-delete")
            kb.add("E", "Copy as Excel", callback=self._export)
            kb.add("Ctrl+E", self._results_button_label(), callback=self._save_race_and_copy)
            kb.add("Esc", "Back to Ready", callback=self._close_review)
        elif state == AppState.RACE_OVER:
            kb.add("R", "Review crossings", True, callback=self._open_review)
            kb.add("E", "Copy as Excel", callback=self._export)
            kb.add("Ctrl+E", self._results_button_label(), callback=self._save_race_and_copy)
            kb.add("N", "Next race", callback=self._next_race)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
        elif state == AppState.RECALIBRATE:
            kb.add("C", "Calibrate", True, callback=self._calibrate)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
        else:  # READY / STREAM_DOWN
            kb.add("Ctrl+S", "Arm", True, callback=self._arm_start)
            kb.add("C", "Calibrate", callback=self._calibrate)
            kb.add("R", "Review last race", callback=self._open_review)
            kb.add("E", "Copy as Excel", callback=self._export)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
            if state == AppState.STREAM_DOWN:
                kb.set_note("Stream down — race starts timing-only (no photos)")

    def _grab_note(self) -> str:
        trig = self.config.section("trigger")
        dev = trig["device_path"] or "Qt fallback"
        grabbed = "keyboard grabbed" if trig["grab_device"] else "grab disabled"
        return f"{grabbed} · {dev}"

    def _pre_race_checks(self) -> list[dict]:
        alive, fps, _ = self.buffer.health()
        trig = self.config.section("trigger")
        disk = shutil.disk_usage(self.config.data_root).free / 1e9
        checks = [
            {"mark": "✓", "label": "MJPEG stream", "detail": f"{fps:.1f} fps",
             "accent": styles.GREEN if alive else styles.RED},
            {"mark": "✓", "label": "Calibration Δ",
             "detail": self._cal_detail or "ok", "accent": styles.GREEN},
            {"mark": "✓", "label": "Trigger device",
             "detail": (trig["device_path"] or "Qt fallback") or "—",
             "accent": styles.GREEN},
            {"mark": "✓", "label": "Disk headroom", "detail": f"{int(disk)} GB free",
             "accent": styles.GREEN},
        ]
        lag = self._measure_lag()
        if lag is not None and lag >= 0.3:
            checks.append({"mark": "!", "label": "Preview lag",
                           "detail": f"+{lag:.1f} s · framing only",
                           "accent": styles.AMBER})
        return checks

    def _measure_lag(self) -> float | None:
        # In screen mode the calibration measured glass-to-screen lag directly.
        if self.config.section("timing")["viewing_mode"] != "screen":
            return None
        return self._cal_latency_ms() / 1000.0 if self._cal_latency_ms() else None

    def _tick_clock(self) -> None:
        if self.controller.running and self.controller.t0 is not None:
            self.recording.set_clock(time.monotonic() - self.controller.t0)

    # -------------------------------------------------------------------- events
    def _arm_start(self) -> None:
        if self._armed:
            return
        if self._last_state in (AppState.ARMED, AppState.RECORDING,
                                AppState.REVIEW):
            self._show_toast("Can't arm in this state.")
            return
        # With no stream, start_race auto-degrades to timing-only (§6.5), so
        # arming is allowed in STREAM_DOWN. RECALIBRATE (stream up, stale Δ) is
        # still blocked: start_race's calibration gate will refuse it anyway.
        if self._last_state in (AppState.RECALIBRATE,):
            self._show_toast("Calibration no longer matches the stream — "
                             "press C to re-calibrate.")
            return
        trig = self.config.section("trigger")
        start_keys = keycode_names(trig["start_keycodes"])
        device = trig["device_path"] or "keyboard"
        self._armed = True
        self._race_over = False
        self._recompute_state()
        self._show_toast(f"Armed. Press {start_keys} on the trigger device "
                         f"({device}) to start.", timeout_ms=0)

    def on_evdev_start(self, t_press: float) -> None:
        if not self._armed:
            return
        self._armed = False
        self.controller.start_race(t_press, name=self._race_name())
        if self.controller.running:
            self._race_over = False
            self._last_capture = None
            self.recording.clear_captures()
        self._recompute_state()

    def on_evdev_crossing(self, t_press: float, code: int, suspect: bool = False) -> None:
        self.controller.record_crossing(t_press, debounce_suspect=suspect)

    def on_evdev_end(self, t_press: float, code: int = 0) -> None:
        if self._armed:
            # Escape hatch while armed: the trigger keyboard is grabbed, so the
            # Qt shortcuts (Esc / Ctrl+Q) are unreachable. The END key disarms
            # and releases the grab back to READY, where normal quit works.
            # Timing is unaffected — this is the pre-race ARMED state, and the
            # RECORDING end path below is untouched.
            self._armed = False
            self._race_over = False
            self._recompute_state()
            self._show_toast("Disarmed. Keyboard released.")
            return
        if code == 1:
            # KEY_ESC while recording: ignore so accidental Esc does not end a race.
            return
        self._end_race()

    def _end_race(self) -> None:
        self.controller.end_race()

    def on_race_ended(self, race_id: int) -> None:
        self._armed = False
        self._race_over = True
        self._reviewing = False
        caps = self.controller.storage.captures_for_race(race_id)
        self.race_over.set_summary(list(caps))
        self._advance_race_default = True
        self._refresh_race_selector()
        self._recompute_state()

    def on_capture(self, cap) -> None:
        primary = getattr(cap, "primary_image", None)
        self.recording.add_capture({
            "sequence": cap.sequence,
            "elapsed_s": cap.elapsed_s,
            "image_path": str(self.config.data_root / primary) if primary else None,
            "image_flag": cap.image_flag,
            "suspect": cap.debounce_suspect,
        })
        self._last_capture = cap.sequence

    def on_image_ready(self, sequence: int, path: str) -> None:
        self.recording.update_image(sequence, str(self.config.data_root / path))

    # ---------------------------------------------------------------- review
    def _open_review(self) -> None:
        race_id = self.controller.race_id
        if race_id is None:
            return
        if self._review_screen is None:
            self._review_screen = ReviewScreen(self.controller, self.config.data_root,
                                               race_id=race_id)
            self.center.addWidget(self._review_screen)
        elif self._review_screen.race_id != race_id:
            self._review_screen.race_id = race_id
        self._review_screen.load_captures()
        self._reviewing = True
        self._recompute_state()
        self._review_screen.setFocus()

    def _close_review(self) -> None:
        self._reviewing = False
        self._recompute_state()

    # ---------------------------------------------------------------- actions
    def _on_race_selected(self, _index: int) -> None:
        if self._last_state == AppState.RACE_OVER:
            self._race_over = False
            self._recompute_state()

    def _next_race(self) -> None:
        if self._last_state == AppState.READY:
            self.ready.next_race()
        elif self._last_state == AppState.RACE_OVER:
            self.ready.next_race()
            self._race_over = False
            self._recompute_state()
        else:
            self._show_toast("Next race only in Ready / Race-over.")

    def _calibrate(self) -> None:
        dlg = CalibrationDialog(self.buffer, self.config.data_root, self.config, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _export(self) -> None:
        if self.controller.race_id is None:
            self._show_toast("No race to export yet.")
            return
        tsv, markup = clipboard_data(self.controller.storage, self.controller.race_id)
        cb = QApplication.clipboard()
        cb.setMimeData(_ExcelMimeData(tsv, markup))
        self._show_toast("Copied to clipboard — paste into Excel.")

    # ------------------------------------------------------------ results save
    def _results_target_race_id(self) -> int | None:
        """The ended race whose data is on screen (REVIEW holds its own id)."""
        if self._reviewing and self._review_screen is not None:
            return self._review_screen.race_id
        return self.controller.race_id

    def _results_button_label(self) -> str:
        rid = self._results_target_race_id()
        if rid is not None and rid in self._saved_race_ids:
            return "Copy only"
        return "Save race and copy"

    def _save_race_and_copy(self) -> None:
        rid = self._results_target_race_id()
        if rid is None:
            self._show_toast("No race to save.")
            return
        race = self.controller.storage.get_race(rid)
        if race is None or race["ended_at"] is None or self.controller.running:
            self._show_toast("Save is only available after a race ends.")
            return
        # Clipboard copy is synchronous and never fails; the workbook write is
        # disk/network I/O on a possibly-slow mount, so run it on a short-lived
        # thread and marshal the outcome back via the bridge signal (§6.4).
        tsv, markup = clipboard_data(self.controller.storage, rid)
        QApplication.clipboard().setMimeData(_ExcelMimeData(tsv, markup))
        if rid in self._saved_race_ids:
            self._show_toast("Copied to clipboard — already saved.")
            self._apply_keybar(self._last_state)
            return
        out = os.path.expanduser(self.config.section("results")["xlsx_path"])

        def _work():
            try:
                result = append_race(self.controller.storage, rid, out)
            except Exception:
                outcome = "error"
            else:
                outcome = "saved" if result is not None else "error"
            bridge = self._results_bridge
            if bridge is not None:
                bridge.results_done.emit(rid, outcome)

        threading.Thread(target=_work, daemon=True, name="results-save").start()

    def on_results_done(self, race_id: int, outcome: str) -> None:
        """GUI-thread handler for the results-save worker (spec §6.4)."""
        if outcome == "saved":
            self._saved_race_ids.add(race_id)
            self._show_toast("Saved to results.xlsx and copied to clipboard.")
        else:
            self._show_toast("Could not save to results.xlsx — see log.")
        self._apply_keybar(self._last_state)

    def _quit(self) -> None:
        self.close()

    def _esc(self) -> None:
        if self._reviewing:
            self._close_review()
        elif self._armed:
            self._armed = False
            self._recompute_state()
        elif self._race_over:
            self._race_over = False
            self._recompute_state()
        else:
            self._quit()

    def _finish_line_changed(self, x: float) -> None:
        pass  # value is displayed in ReadyScreen; persistence not required

    # -------------------------------------------------------------------- toast
    def _show_toast(self, msg: str, timeout_ms: int = 6000) -> None:
        self._toast_initialized = True
        self.toast.show_message(msg, timeout_ms)
        self.toast.reposition(self.ready.width(), self.ready.height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._toast_initialized:
            self.toast.reposition(self.ready.width(), self.ready.height())

    # ------------------------------------------------------------------ selector
    def _populate_race_selector(self) -> None:
        import os
        races_cfg = self.config.section("races")
        excel_path = os.path.expanduser(races_cfg["excel_path"])
        self._race_names = load_race_names(excel_path)
        if not self._race_names:
            try:
                write_example(excel_path)
                self._race_names = load_race_names(excel_path)
            except Exception:
                self._race_names = []
