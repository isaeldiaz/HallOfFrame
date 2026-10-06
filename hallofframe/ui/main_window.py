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

import shutil
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QLineEdit, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..calibration import Calibration
from ..controller import (CalibrationError, CaptureController, RaceStateError,
                          calibration_status)
from ..export import clipboard_data, export_all_html, local_hms
from ..framebuffer import FrameBuffer
from ..roster import (Roster, RosterLoad, RosterWriteError, format_display,
                      race_key, recorded_keys)
from ..ui import styles
from ..ui.calibration_dialog import CalibrationDialog
from ..ui.misc_screens import ArmedScreen, RaceOverScreen
from ..ui.race_screen import RaceScreen
from ..ui.ready_screen import ReadyScreen, choose_roster_file, roster_path
from ..ui.review_screen import ReviewScreen
from ..ui.about_screen import AboutOverlay
from ..ui.state import AppState, derive_state
from ..ui.widgets import Banner, BannerHost, KeyBar, StateBand, Toast


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
                 buffer: FrameBuffer, trigger=None, logger=None):
        super().__init__()
        self.config = config
        self.controller = controller
        self.buffer = buffer
        self.trigger = trigger
        self._logger = logger

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

        # --- roster-load banners (F10), full-width above the content ---
        self.banner_host = BannerHost()
        self._root_lay.addWidget(self.banner_host)

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
        self.about = AboutOverlay(self.config, root)

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
        self._review_race_id: int | None = None
        self._race_over_race_id: int | None = None
        self._review_screen: ReviewScreen | None = None
        self._cal_ok = True
        self._cal_detail = ""
        self._cal_check_at = 0.0
        self._last_capture: int | None = None
        self._last_state = None
        self._advance_race_default = False
        self.roster = Roster(None)
        # Set by main.py to keep the trigger device grab tied to state (§9/Opt A).
        self.on_state_changed = None

        self._load_races()
        self.ready.race_selected.connect(self._on_race_selected)
        self.ready.add_race_clicked.connect(self._open_add_race)
        self.ready.skip_clicked.connect(self._toggle_skip)
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
        self.controller.events = self._on_controller_event

    def _on_controller_event(self, kind: str, payload: dict) -> None:
        """Single subscription to ``CaptureController.events`` (step 1.3)."""
        if kind == "capture_added":
            self.on_capture(payload["capture"])
        elif kind == "capture_deleted":
            self.on_capture_deleted(payload["sequence"])
        elif kind == "image_ready":
            self.on_image_ready(payload["sequence"], payload["path"])
        elif kind == "race_ended":
            self.on_race_ended(payload["race_id"])
        elif kind == "warning":
            self._show_toast(payload["message"])

    def _set_trigger_label(self) -> None:
        trig = self.config.section("trigger")
        start = keycode_names(trig["start_keycodes"])
        end = keycode_names(trig["end_keycodes"])
        device = trig["device_path"] or "Qt fallback"
        if self._single_key_mode():
            label = (f"First press starts the race; each press after records a "
                     f"crossing ({device}). {end} or Esc to disarm.")
        else:
            label = f"({device}). {end} or Esc to disarm."
        self.armed.set_trigger_label(label)

    def _single_key_mode(self) -> bool:
        """True when the same evdev keycode drives both start and crossing
        (single-key armed first-press = t0 flow, spec §5.3)."""
        trig = self.config.section("trigger")
        cross = {int(c) for c in trig["crossing_keycodes"]}
        start = {int(c) for c in trig["start_keycodes"]}
        return bool(cross & start)

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
        sc("F1", self._toggle_about)
        letters = [sc("C", self._calibrate), sc("E", self._on_e),
                   sc("L", self._load_selected_race),
                   sc("D", self._export_html), sc("R", self._on_r),
                   sc("N", self._next_race), sc("/", self._focus_filter),
                   sc("End", self._end_unlisted)]
        sc("Shift+Up", lambda: self._move_selected_race(-1))
        sc("Shift+Down", lambda: self._move_selected_race(1))
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
        return ["Stream", "Δ latency", "Disk"]

    def _update_health(self) -> None:
        _, fps, _ = self.buffer.health()
        fps = self.trigger.fps if (self.trigger and fps <= 0) else fps
        free = shutil.disk_usage(self.config.data_root).free / 1e9
        self.band.set_health(self._health_labels())
        self.band.set_health_value("Stream", f"{fps:.1f} fps",
                                   styles.GREEN_TEXT if fps > 0 else styles.RED_TEXT)
        self.band.set_health_value("Disk", f"{int(free)} GB")
        lag = self._cal_latency_ms()
        self.band.set_health_value(
            "Δ latency", f"{lag:.0f} ms" if lag else "—")

    def _cal_latency_ms(self) -> float | None:
        cal = Calibration.load(self.config.data_root)
        return cal.latency_median_ms if cal is not None else None

    # ------------------------------------------------------------------- status
    def _update_status(self) -> None:
        self._update_health()
        self._recompute_state()

    def _recompute_state(self) -> None:
        # Calibration is only gateable in water mode; screen mode cancels
        # latency entirely and needs no calibration file (§5.4, §8).
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

    # States where the stored race is the one on screen (still meaningful to show
    # its name). In READY/ARMED the band should reflect the NEXT race from the
    # dropdown, not the one that just finished (controller.race_id is not cleared
    # when a race ends).
    _ACTIVE_RACE_STATES = (AppState.RECORDING, AppState.RACE_OVER, AppState.REVIEW)

    def _race_name(self, state: AppState) -> str:
        if state in self._ACTIVE_RACE_STATES and self.controller.race_id is not None:
            row = self.controller.storage.get_race(self.controller.race_id)
            if row:
                return format_display(row["race_no"], row["heat_no"], row["name"])
        return self.ready.current_race_name()

    def _refresh_band(self, state: AppState) -> None:
        fix = ""
        if state == AppState.STREAM_DOWN:
            fix = "No frames arriving — check the camera app and the USB cable"
        elif state == AppState.RECALIBRATE:
            fix = f"{self._cal_detail} — press C to calibrate"
        self.band.set_state(state, self._race_name(state), fix)

    # ---------------------------------------------------------------- apply state
    def _refresh_race_selector(self) -> None:
        """Re-read recorded races so completed ones turn gray immediately,
        without disturbing the operator's current selection (overwrite stays
        available)."""
        self.ready.refresh_recorded(recorded_keys(self.controller.storage))

    def _apply_state(self, state: AppState) -> None:
        self._last_state = state
        # A state transition means the previous warning's context is gone:
        # drop any sticky toast (e.g. the persistent "Armed…" hint, calibration
        # mismatch) so it can't linger past when it stopped being relevant.
        self.toast.dismiss()
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
            kb.set_note(f"Trigger device grabbed while armed — {end} or Esc disarms "
                        "and releases it, then quit normally.")
        elif state == AppState.REVIEW:
            kb.add("↑/↓", "Select crossing", True)
            kb.add("Tab", "Next bow field")
            kb.add("Del", "Soft-delete")
            kb.add("E", "Edit race", callback=self._edit_race)
            kb.add("Esc", "Back to Ready", callback=self._close_review)
        elif state == AppState.RACE_OVER:
            kb.add("R", "Review crossings", True, callback=self._open_review)
            kb.add("E", "Copy as Excel", callback=self._export)
            kb.add("N", "Next race", callback=self._next_race)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
        elif state == AppState.RECALIBRATE:
            kb.add("C", "Calibrate", True, callback=self._calibrate)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
        else:  # READY / STREAM_DOWN
            kb.add("Ctrl+S", "Arm", True, callback=self._arm_start)
            kb.add("C", "Calibrate", callback=self._calibrate)
            kb.add("L", "Load race", callback=self._load_selected_race)
            kb.add("Shift+↑", "Move up", callback=lambda: self._move_selected_race(-1))
            kb.add("Shift+↓", "Move down", callback=lambda: self._move_selected_race(1))
            kb.add("D", "Save DB HTML", callback=self._export_html)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
            if state == AppState.STREAM_DOWN:
                kb.set_note("Stream down — race starts timing-only (no photos)")

    def _grab_note(self) -> str:
        trig = self.config.section("trigger")
        dev = trig["device_path"] or "Qt fallback"
        grabbed = "trigger device grabbed" if trig["grab_device"] else "grab disabled"
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
        if self._advance_race_default:
            # A race just finished: advance the default to the next unrecorded
            # race even when arming straight from RACE_OVER (without passing
            # READY, which is where _apply_state would otherwise do this).
            self.ready.select_first_unrecorded()
            self._advance_race_default = False
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
        if self._single_key_mode():
            self._show_toast(f"Armed. First press on {start_keys} starts the race; "
                             f"each press after records a crossing ({device}).",
                             timeout_ms=0)
        else:
            self._show_toast(f"Armed. Press {start_keys} on the trigger device "
                             f"({device}) to start.", timeout_ms=0)

    def on_evdev_start(self, t_press: float) -> None:
        if not self._armed:
            return
        self._armed = False
        # WP7: an unlisted race records under a provisional (timestamp) key with
        # null race_no/heat_no, identified once afterwards in review.
        race, is_unlisted = self.ready.current_selection()
        try:
            self.controller.start_race(
                t_press, name=race.name,
                race_no=None if is_unlisted else race.race_no,
                heat_no=None if is_unlisted else race.heat_no)
        except (CalibrationError, RaceStateError) as exc:
            # start_race refuses (calibration mismatch, already running, or a
            # prior race never ended). Surface it without a modal (§7.5).
            self._show_toast(str(exc))
            self._recompute_state()
            return
        if self.controller.running:
            self._race_over = False
            self._last_capture = None
            self.recording.clear_captures()
        self._recompute_state()

    def on_evdev_crossing(self, t_press: float, code: int, suspect: bool = False) -> None:
        # Single-key flow (§5.3): when the trigger key is armed, the first press
        # IS t0 (start). On the main thread via the bridge, so reading _armed
        # here is safe. Once a race is running, every press is a crossing.
        if self._armed:
            self.on_evdev_start(t_press)
            return
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
        self._race_over_race_id = race_id
        self._reviewing = False
        caps = self.controller.storage.captures_for_race(race_id)
        self.race_over.set_summary(list(caps), self._start_text(race_id))
        self._advance_race_default = True
        self.ready.reset_provisional()
        self._refresh_race_selector()
        self._recompute_state()

    def _start_text(self, race_id: int) -> str:
        row = self.controller.storage.get_race(race_id)
        t0_wall = row["t0_wall"] if row else None
        return local_hms(t0_wall) if t0_wall is not None else "—"

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

    def on_capture_deleted(self, sequence: int) -> None:
        self.recording.remove_capture(sequence)

    # ---------------------------------------------------------------- review
    def _open_review(self, race_id: int | None = None) -> None:
        if race_id is None:
            race_id = self._current_race_id()
        if race_id is None:
            return
        self._review_race_id = race_id
        if self._review_screen is None:
            self._review_screen = ReviewScreen(self.controller, self.config.data_root,
                                               race_id=race_id)
            self.center.addWidget(self._review_screen)
            self._review_screen.edit_race_requested.connect(self._edit_race)
        elif self._review_screen.race_id != race_id:
            self._review_screen.race_id = race_id
        self._review_screen.load_captures()
        self._reviewing = True
        self._recompute_state()
        self._review_screen.setFocus()

    def _close_review(self) -> None:
        if self._review_race_id is not None:
            self.controller.storage.mark_race_reviewed(self._review_race_id)
        self._reviewing = False
        if self._race_over and self._race_over_race_id is not None:
            race_id = self._race_over_race_id
            caps = self.controller.storage.captures_for_race(race_id)
            self.race_over.set_summary(list(caps), self._start_text(race_id))
        self._recompute_state()

    def _current_race_id(self) -> int | None:
        """The race the operator is currently looking at: the reviewed race, the
        race shown on the RACE_OVER window, or else the last race run."""
        if self._reviewing:
            return self._review_race_id
        if self._race_over:
            return self._race_over_race_id
        return self.controller.race_id

    # ---------------------------------------------------------------- actions
    def _on_race_selected(self, row) -> None:
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

    def _on_r(self) -> None:
        """R: from RACE_OVER open REVIEW (no-op elsewhere — race summary was
        folded into Load race)."""
        if self._last_state == AppState.RACE_OVER:
            self._open_review()

    def _load_selected_race(self) -> None:
        """L: reload the race selected in the next-race dropdown into the
        RACE_OVER window, so its summary and crossings can be reviewed (R) or
        copied (E) without re-arming. Only recorded races can be loaded."""
        if self._last_state not in (AppState.READY, AppState.STREAM_DOWN,
                                    AppState.RECALIBRATE):
            self._show_toast("Load race only in Ready.")
            return
        race, is_unlisted = self.ready.current_selection()
        if race is None or is_unlisted:
            self._show_toast("Select a recorded race to load.")
            return
        target = None
        for row in self.controller.storage.all_races():
            if race_key(row["race_no"], row["heat_no"], row["name"]) == race.key:
                target = row
        if target is None:
            self._show_toast("This race isn't recorded yet — arm and run it first.")
            return
        self._armed = False
        self._reviewing = False
        self._race_over = True
        self._race_over_race_id = target["id"]
        caps = self.controller.storage.captures_for_race(target["id"])
        self.race_over.set_summary(list(caps), self._start_text(target["id"]))
        self._recompute_state()

    def _on_e(self) -> None:
        """E: edit the selected/under-review race; export in Race-over."""
        if self._last_state == AppState.RACE_OVER:
            self._export()
        elif self._last_state == AppState.REVIEW:
            self._edit_race()
        else:
            self._open_rename()

    # ---------------------------------------------------------------- roster editing
    def _open_add_race(self, race_no: str = "", heat_no: str = "") -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't edit the roster while armed or recording.")
            return
        if not self.roster.path:
            self._show_toast("No roster loaded — Load roster… first.")
            return
        from ..ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(self.roster.path, race_no, heat_no,
                            expected=self.roster.rows, logger=self._logger,
                            parent=self, after_key=self.ready.selected_key())
        dlg.result_applied.connect(lambda _r: self._reload_roster())
        dlg.exec()

    def _open_rename(self) -> None:
        """E in READY: correct the selected roster row's name (F6)."""
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't rename while armed or recording.")
            return
        if not self.roster.path:
            self._show_toast("No roster loaded — Load roster… first.")
            return
        if self.ready.selected_is_unlisted():
            self._show_toast("Nothing to rename on an unlisted race.")
            return
        race, _ = self.ready.current_selection()
        if race is not None:
            self._rename_dialog(race.race_no, race.heat_no, race.name)

    def _edit_race(self) -> None:
        """Review-side *Edit race* (step 2.6): an unlisted race gets its number
        after the fact via ``storage.identify_race``; a listed race is renamed.
        The dialog also offers to append the row to the roster."""
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't edit the roster while armed or recording.")
            return
        race_id = self._review_race_id or self.controller.race_id
        if race_id is None:
            return
        row = self.controller.storage.get_race(race_id)
        if row is None:
            return
        if not (row["race_no"] or ""):
            self._rename_dialog(row["race_no"] or "", row["heat_no"] or "",
                                row["name"] or "", editable=True,
                                race_id=race_id)
        else:
            self._rename_dialog(row["race_no"], row["heat_no"], row["name"])
        self._reload_roster()
        self._recompute_state()

    def _rename_dialog(self, race_no, heat_no, name, *, editable=False,
                       race_id=None) -> None:
        from ..ui.roster_dialog import RenameDialog
        dlg = RenameDialog(self.roster.path, race_no, heat_no, name,
                           recorded_keys(self.controller.storage),
                           self.controller.storage,
                           expected=self.roster.rows, logger=self._logger,
                           parent=self, editable_numbers=editable,
                           race_id=race_id, roster=self.roster)
        dlg.result_applied.connect(lambda _r: self._reload_roster())
        dlg.exec()

    def _move_selected_race(self, delta: int) -> None:
        """Shift+↑/↓ in READY: move the selected row one place in file order
        (plan step 2.4). The file is never re-sorted; the moved row stays
        selected."""
        if self._last_state not in (AppState.READY, AppState.STREAM_DOWN,
                                    AppState.RECALIBRATE):
            return
        key = self.ready.selected_key()
        if key is None:
            return
        try:
            self.roster.move(key, delta)
        except RosterWriteError as exc:
            self._show_toast(f"Could not move race: {exc}")
            return
        self._render_roster()
        self.ready.select_key(key)

    def _toggle_skip(self) -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't skip while armed or recording.")
            return
        if self.ready.selected_is_unlisted():
            return
        race, _ = self.ready.current_selection()
        if race is None or not self.roster.path:
            return
        skipping = race.key not in self.roster.skipped_keys()
        try:
            self.roster.skip(race.key, skip=skipping)
        except Exception as exc:
            self._show_toast(f"Could not update roster: {exc}")
            return
        if self._logger is not None:
            self._logger.info("roster", "skip" if skipping else "unskip",
                              key=str(race.key), name=race.name,
                              file=self.roster.path)
        self._render_roster()

    def _toggle_about(self) -> None:
        # The trigger keyboard is grabbed while armed or recording: nothing may
        # cover the clock or the last-capture panel mid-race (§7.5).
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("About is unavailable during a race — F12 ends it.")
            return
        if self.about.toggle():
            self.about.setGeometry(self.centralWidget().rect())

    def _calibrate(self) -> None:
        dlg = CalibrationDialog(self.buffer, self.config.data_root, self.config, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _export(self) -> None:
        race_id = self._current_race_id()
        if race_id is None:
            self._show_toast("No race to export yet.")
            return
        tsv, markup = clipboard_data(self.controller.storage, race_id)
        cb = QApplication.clipboard()
        cb.setMimeData(_ExcelMimeData(tsv, markup))
        self._show_toast("Copied to clipboard — paste into Excel.")

    def _export_html(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M")
        out = self.config.data_root / f"export_{stamp}.html"
        export_all_html(self.controller.storage, out)
        self._show_toast(f"Saved full database to {out}")

    def _quit(self) -> None:
        self.close()

    def _focus_filter(self) -> None:
        if self._last_state in (AppState.READY, AppState.STREAM_DOWN,
                                AppState.RECALIBRATE):
            self.ready.begin_filter()

    def _end_unlisted(self) -> None:
        if self._last_state in (AppState.READY, AppState.STREAM_DOWN,
                                AppState.RECALIBRATE):
            self.ready.end_select_unlisted()

    def _esc(self) -> None:
        if self.about.isVisible():
            self.about.close_about()
            return
        if self.ready._filter_active:
            self.ready.clear_filter()
            return
        if self._reviewing:
            self._close_review()
        elif self._armed:
            self._armed = False
            self._recompute_state()
        elif self._race_over:
            self._race_over = False
            self._recompute_state()
        # No fallback: Esc never quits the application. Only Ctrl+Q does.

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
        if self.about.isVisible():
            self.about.setGeometry(self.centralWidget().rect())

    # ------------------------------------------------------------------ selector
    def _load_races(self, path: str | None = None) -> None:
        """Load the roster (startup or a manual Load roster…) and render it.

        A configured-but-missing path never auto-writes an example roster; it is
        offered as an action instead (BEHAVIOUR §4)."""
        if path is None:
            path = roster_path(self.config)
        self.roster.load(path)
        self._render_roster()

    def _render_roster(self) -> None:
        """The single render step (plan step 2.4): push ``roster.races``, the
        recorded keys and the skipped keys into the picker, then rebuild the chip
        and banners. Display order is file order — never sorted."""
        result = self.roster.result
        recorded = recorded_keys(self.controller.storage)
        self.ready.set_races(self.roster.races, recorded=recorded,
                             skipped=self.roster.skipped_keys())
        self._render_roster_chip(result)
        self._render_roster_banner(result, recorded)

    def _render_roster_chip(self, result: RosterLoad) -> None:
        import os
        if not result.ok:
            self.ready.set_roster("", 0, "")
            return
        filename = os.path.basename(result.path)
        self.ready.set_roster(filename, len(result.races), result.loaded_at,
                              duplicates=len(result.duplicates),
                              dup_callback=self._show_duplicates)

    def _render_roster_banner(self, result: RosterLoad, recorded: set) -> None:
        import os
        self.banner_host.clear()
        if not result.ok:
            if result.missing:
                self.banner_host.add_banner(Banner(
                    styles.AMBER,
                    f"No roster at {result.path}",
                    "Nothing was created. Racing without a roster is allowed.",
                    [("Load roster…", self._load_roster_dialog),
                     ("Write an example roster", self._write_example_roster)]))
            elif result.file_error:
                self.banner_host.add_banner(Banner(
                    styles.RED,
                    f"Roster unreadable · {os.path.basename(result.path)}",
                    result.file_error,
                    [("Reload", self._reload_roster),
                     ("Load another roster…", self._load_roster_dialog)]))
            elif result.errors:
                line = result.errors[0][0]
                self.banner_host.add_banner(Banner(
                    styles.RED,
                    f"Roster failed to parse · {os.path.basename(result.path)}"
                    f" line {line}",
                    "Expected race_no, heat_no, name. No roster is loaded.",
                    [("Reload", self._reload_roster),
                     ("Load another roster…", self._load_roster_dialog)]))
            return
        # A roster is loaded: duplicates and/or dropped recorded races.
        loaded_keys = {r.key for r in result.races}
        # Only numbered races count as "dropped"; provisional/unlisted races key
        # on a timestamp name ("name", ...) that can never be in the roster.
        dropped = sorted(k for k in (recorded - loaded_keys) if k[0] == "num")
        if result.duplicates:
            key, l_a, l_b = result.duplicates[0]
            headline = (f"Duplicate key {self._key_display(key)}"
                        f" · lines {l_a} and {l_b}")
            if len(result.duplicates) > 1:
                headline += f" (+{len(result.duplicates) - 1} more)"
            self.banner_host.add_banner(Banner(
                styles.AMBER, headline,
                "Rows are not silently dropped. Resolve in the file, or keep the first.",
                [("Show both", self._show_duplicates), ("Reload", self._reload_roster)]))
        if dropped:
            self.banner_host.add_banner(Banner(
                styles.BLUE,
                f"{len(dropped)} recorded race{'s' if len(dropped) != 1 else ''}"
                " are not in this roster",
                "After a reload. Results are untouched; the running order changed.",
                [("List them", lambda: self._show_dropped(dropped))]))

    @staticmethod
    def _key_display(key) -> str:
        if key and key[0] == "num":
            rn, hn = key[1], key[2]
            return f"{rn}-H{hn}" if hn else str(rn)
        return str(key[1]) if key else ""

    def _reload_roster(self) -> None:
        self.roster.load()
        self._render_roster()

    def _load_roster_dialog(self) -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't load a roster while armed or recording.")
            return
        path = choose_roster_file(self, self.config.data_root)
        if path:
            self.roster.load(path)
            self._render_roster()

    def _write_example_roster(self) -> None:
        if self.roster.path:
            try:
                self.roster.write_example()
            except OSError as exc:
                self._show_toast(f"Could not write example roster: {exc}")
                return
            self._render_roster()

    def _show_duplicates(self) -> None:
        dupes = self.roster.result.duplicates
        if not dupes:
            return
        parts = [f"{self._key_display(k)} (lines {a}, {b})" for k, a, b in dupes]
        self._show_toast("Duplicates: " + "; ".join(parts))

    def _show_dropped(self, dropped) -> None:
        names = " · ".join(self._key_display(d) for d in dropped[:10])
        if len(dropped) > 10:
            names += f" … +{len(dropped) - 10} more"
        self._show_toast(f"Recorded, not in roster: {names}", timeout_ms=12000)
