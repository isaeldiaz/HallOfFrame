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

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QLineEdit, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..calibration import Calibration
from ..controller import (CalibrationError, CaptureController, RaceStateError,
                          calibration_status)
from ..render import local_hms
from ..render.clipboard import clipboard_data
from ..render.html import export_all_html
from ..framebuffer import FrameBuffer
from ..roster import Roster, format_display, race_key, recorded_keys
from ..session import (KEYBAR_NOTE, KEYMAP, Phase, Session, SessionError,
                       derive_state)
from ..ui import styles
from ..ui.calibration_dialog import CalibrationDialog
from ..ui.misc_screens import ArmedScreen, RaceOverScreen
from ..ui.race_screen import RaceScreen
from ..ui.ready_screen import ReadyScreen
from ..ui.review_screen import ReviewScreen
from ..ui.about_screen import AboutOverlay
from ..ui.roster_view import RosterView
from ..ui.state import AppState
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
    """Human-readable names for a list of Linux keycodes (e.g. [88] -> 'F12').

    Degrades to the raw codes when ``evdev`` is not installed (the Qt-fallback
    setup and CI), so the window can be built without the evdev dependency."""
    try:
        import evdev.ecodes as ec
    except ImportError:
        ec = None
    names = []
    for c in codes:
        name = ec.KEY.get(int(c), "") if ec is not None else ""
        names.append(name.removeprefix("KEY_") if name else str(c))
    return ", ".join(names) if names else str(codes)


class MainWindow(QMainWindow):
    # Emitted at the end of every ``_apply_state`` so ``main.py`` can tie the
    # trigger-device grab to state without the window knowing about evdev (§9).
    state_changed = Signal(object)

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
        # The operator's position in the lifecycle lives in one object; the
        # window only reads ``session.phase`` / ``session.focused_race_id``.
        self.session = Session()
        self._review_screen: ReviewScreen | None = None
        self._cal_ok = True
        self._cal_detail = ""
        self._cal_check_at = 0.0
        self._last_capture: int | None = None
        self._last_state = None
        self._advance_race_default = False
        self._resume_banner: Banner | None = None
        self.roster = Roster(None)
        self.roster_view = RosterView(
            ready=self.ready, banner_host=self.banner_host, roster=self.roster,
            storage=self.controller.storage, toast=self._show_toast, parent=self,
            logger=self._logger, state=lambda: self._last_state,
            focused_race_id=lambda: (self.session.focused_race_id
                                     or self.controller.race_id),
            recompute=self._recompute_state)

        self.roster_view.load()
        self.ready.race_selected.connect(self._on_race_selected)
        self.ready.add_race_clicked.connect(self.roster_view.open_add_race)
        self.ready.skip_clicked.connect(self.roster_view.toggle_skip)
        self.ready.set_finish_line(float(self.config.section("ui")["finish_line_x"]))
        self._set_trigger_label()

        self._connect_controller()
        self._install_keymap()
        self._apply_state(self._derive())
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

    def _install_keymap(self) -> None:
        """Build one application shortcut per distinct ``KEYMAP`` key (step 3.3).

        ApplicationShortcut context: these never lose to a focused button (§4).
        Each distinct key maps to one action across every state, so a single
        QShortcut per key is enough; ``_enable_shortcuts`` turns on exactly the
        ones the current state lists."""
        self._shortcuts: dict[str, QShortcut] = {}
        for keys in KEYMAP.values():
            for k in keys:
                if not k.shortcut or k.key in self._shortcuts:
                    continue
                sc = QShortcut(QKeySequence(k.key), self,
                               context=Qt.ApplicationShortcut)
                sc.activated.connect(getattr(self, k.action))
                self._shortcuts[k.key] = sc
        app = QApplication.instance()
        if app is not None:
            # Bound method, not a lambda: Qt drops the connection when this
            # window is destroyed instead of calling into a dead object.
            app.focusChanged.connect(self._focus_changed)

    def _focus_changed(self, _old=None, _new=None) -> None:
        self._enable_shortcuts()

    def _enable_shortcuts(self, state: AppState | None = None) -> None:
        """Enable exactly the shortcuts the current state lists, and silence the
        typable ones while a QLineEdit has focus (§4) so bow numbers arrive."""
        state = self._last_state if state is None else state
        typing = isinstance(QApplication.focusWidget(), QLineEdit)
        keys = KEYMAP.get(state, [])
        active = {k.key for k in keys if k.shortcut}
        typable = {k.key for k in keys if k.shortcut and k.typable}
        for key, shortcut in self._shortcuts.items():
            shortcut.setEnabled(key in active and not (typing and key in typable))

    def _rebuild_keybar(self, state: AppState) -> None:
        kb = self.keybar
        kb.clear()
        for k in KEYMAP.get(state, []):
            if not k.show:
                continue
            callback = None if k.action == "_noop" else getattr(self, k.action)
            kb.add(k.key, k.label, k.hot, callback)
        note = self._note_for(state)
        if note:
            kb.set_note(note)

    def _note_for(self, state: AppState) -> str:
        if state == AppState.RECORDING:
            return self._grab_note()
        if state == AppState.ARMED:
            end = keycode_names(self.config.section("trigger")["end_keycodes"])
            return (f"Trigger device grabbed while armed — {end} or Esc disarms "
                    "and releases it, then quit normally.")
        return KEYBAR_NOTE.get(state, "")

    def _apply_keymap(self, state: AppState) -> None:
        self._rebuild_keybar(state)
        self._enable_shortcuts(state)

    # ---------------------------------------------------------- keymap actions
    def _noop(self) -> None:
        """Informational key-bar cap (REVIEW arrows / Tab / Del): no shortcut."""

    def _undo_last(self) -> None:
        self.controller.undo_last()

    def _end_key(self) -> None:
        self.on_evdev_end(time.monotonic(), 88)

    def _start_key(self) -> None:
        self.on_evdev_start(time.monotonic())

    def _crossing_key(self) -> None:
        self.controller.record_crossing(time.monotonic())

    def _move_up(self) -> None:
        self.roster_view.move_selected(-1)

    def _move_down(self) -> None:
        self.roster_view.move_selected(1)

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

    def _derive(self) -> AppState:
        alive, _fps, _age = self.buffer.health()
        return derive_state(self.session, alive, self._cal_ok)

    def _recompute_state(self) -> None:
        # Calibration is only gateable in water mode; screen mode cancels
        # latency entirely and needs no calibration file (§5.4, §8).
        if self.config.section("timing")["viewing_mode"] == "screen":
            self._cal_ok, self._cal_detail = True, ""
        # Re-check calibration ~every 3 s (decodes a frame for resolution).
        elif time.monotonic() - self._cal_check_at >= 3.0:
            self._cal_check_at = time.monotonic()
            self._cal_ok, self._cal_detail = calibration_status(self.config, self.buffer)
        state = self._derive()
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
        self._apply_keymap(state)
        if state == AppState.READY:
            if self._advance_race_default:
                # A race just finished: jump the default to the next race in the
                # roster that is not yet recorded (skipping the one we just ran).
                self.ready.select_first_unrecorded()
                self._advance_race_default = False
            self._refresh_race_selector()
            self.ready.set_checks(self._pre_race_checks())
            self.ready.set_lag(self._measure_lag())
        self.state_changed.emit(state)

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
        if self.session.phase is Phase.ARMED:
            return
        if self._advance_race_default and self.session.phase in (Phase.IDLE,
                                                                 Phase.RACE_OVER):
            # A race just finished: advance the default to the next unrecorded
            # race even when arming straight from RACE_OVER (without passing
            # READY, which is where _apply_state would otherwise do this).
            self.ready.select_first_unrecorded()
            self._advance_race_default = False
        # With no stream, start_race auto-degrades to timing-only (§6.5), so
        # arming is allowed in STREAM_DOWN. RECALIBRATE (stream up, stale Δ) is
        # still blocked: start_race's calibration gate will refuse it anyway.
        if self._last_state == AppState.RECALIBRATE:
            self._show_toast("Calibration no longer matches the stream — "
                             "press C to re-calibrate.")
            return
        try:
            self.session.arm()
        except SessionError as exc:
            self._show_toast(str(exc))
            return
        trig = self.config.section("trigger")
        start_keys = keycode_names(trig["start_keycodes"])
        device = trig["device_path"] or "keyboard"
        self._recompute_state()
        if self._single_key_mode():
            self._show_toast(f"Armed. First press on {start_keys} starts the race; "
                             f"each press after records a crossing ({device}).",
                             timeout_ms=0)
        else:
            self._show_toast(f"Armed. Press {start_keys} on the trigger device "
                             f"({device}) to start.", timeout_ms=0)

    def on_evdev_start(self, t_press: float) -> None:
        if self.session.phase is not Phase.ARMED:
            return
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
            # prior race never ended). Surface it without a modal (§7.5) and
            # fall back to IDLE, exactly as the pre-Session code disarmed.
            self._show_toast(str(exc))
            self.session.disarm()
            self._recompute_state()
            return
        if self.controller.running:
            self.session.race_started(self.controller.race_id)
            self._last_capture = None
            self.recording.clear_captures()
        self._recompute_state()

    def on_evdev_crossing(self, t_press: float, code: int, suspect: bool = False) -> None:
        # Single-key flow (§5.3): when the trigger key is armed, the first press
        # IS t0 (start). On the main thread via the bridge, so reading the
        # session phase here is safe. Once a race is running, every press is a
        # crossing.
        if self.session.phase is Phase.ARMED:
            self.on_evdev_start(t_press)
            return
        self.controller.record_crossing(t_press, debounce_suspect=suspect)

    def on_evdev_end(self, t_press: float, code: int = 0) -> None:
        if self.session.phase is Phase.ARMED:
            # Escape hatch while armed: the trigger keyboard is grabbed, so the
            # Qt shortcuts (Esc / Ctrl+Q) are unreachable. The END key disarms
            # and releases the grab back to READY, where normal quit works.
            # Timing is unaffected — this is the pre-race ARMED state, and the
            # RECORDING end path below is untouched.
            try:
                self.session.disarm()
            except SessionError:
                return
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
        if self.session.phase is Phase.RECORDING:
            self.session.race_ended(race_id)
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
            race_id = self.session.focused_race_id or self.controller.race_id
        if race_id is None:
            return
        try:
            self.session.open_review(race_id)
        except SessionError as exc:
            self._show_toast(str(exc))
            return
        if self._review_screen is None:
            self._review_screen = ReviewScreen(self.controller, self.config.data_root,
                                               race_id=race_id)
            self.center.addWidget(self._review_screen)
            self._review_screen.edit_race_requested.connect(self.roster_view.edit_race)
        elif self._review_screen.race_id != race_id:
            self._review_screen.race_id = race_id
        self._review_screen.load_captures()
        self._recompute_state()
        self._review_screen.setFocus()

    def _close_review(self) -> None:
        race_id = self.session.focused_race_id
        if race_id is not None:
            self.controller.storage.mark_race_reviewed(race_id)
        try:
            self.session.close_review()
        except SessionError as exc:
            self._show_toast(str(exc))
            return
        if self.session.phase is Phase.RACE_OVER and race_id is not None:
            caps = self.controller.storage.captures_for_race(race_id)
            self.race_over.set_summary(list(caps), self._start_text(race_id))
        self._recompute_state()

    # Review keybar caps forward to the review screen's selected crossing
    # (plan step 7.2). The keys themselves are handled by the screen's
    # keyPressEvent, so these exist for the clickable key-bar caps and to keep
    # every KEYMAP action resolving to a real MainWindow method.
    def _review_remove(self) -> None:
        if self._review_screen is not None:
            self._review_screen.remove_selected()

    def _review_restore(self) -> None:
        if self._review_screen is not None:
            self._review_screen.restore_selected()

    def _review_clone(self) -> None:
        if self._review_screen is not None:
            self._review_screen.clone_selected()

    # ---------------------------------------------------------------- actions
    def _on_race_selected(self, row) -> None:
        if self._last_state == AppState.RACE_OVER:
            self.session.dismiss()
            self._recompute_state()

    def _next_race(self) -> None:
        if self._last_state == AppState.READY:
            self.ready.next_race()
        elif self._last_state == AppState.RACE_OVER:
            self.ready.next_race()
            self.session.dismiss()
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
        try:
            self.session.load_race(target["id"])
        except SessionError as exc:
            self._show_toast(str(exc))
            return
        caps = self.controller.storage.captures_for_race(target["id"])
        self.race_over.set_summary(list(caps), self._start_text(target["id"]))
        self._recompute_state()

    def _on_e(self) -> None:
        """E: edit the selected/under-review race; export in Race-over."""
        if self._last_state == AppState.RACE_OVER:
            self._export()
        elif self._last_state == AppState.REVIEW:
            self.roster_view.edit_race()
        else:
            self.roster_view.open_rename()

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
        race_id = self.session.focused_race_id or self.controller.race_id
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
        if self.session.phase is Phase.REVIEW:
            self._close_review()
        elif self.session.phase is Phase.ARMED:
            self.session.disarm()
            self._recompute_state()
        elif self.session.phase is Phase.RACE_OVER:
            self.session.dismiss()
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

    # ----------------------------------------------------------------- resume
    def offer_resume(self, race_id: int) -> None:
        """Startup prompt for a race left un-ended (plan step 3.5, N4). Non-modal:
        the operator may resume timing or discard the incomplete race."""
        self._resume_banner = Banner(
            styles.AMBER,
            f"Race {race_id} was not ended — Resume / Discard",
            "The app was closed mid-race. Resume continues timing; "
            "Discard marks the race finished.",
            [("Resume", lambda: self._resume_race(race_id)),
             ("Discard", lambda: self._discard_race(race_id))])
        self.banner_host.add_banner(self._resume_banner)

    def _clear_resume_banner(self) -> None:
        if self._resume_banner is not None:
            self.banner_host.remove(self._resume_banner)
            self._resume_banner = None

    def _resume_race(self, race_id: int) -> None:
        self._clear_resume_banner()
        self.controller.resume_race(race_id)
        self.session.arm()
        self.session.race_started(race_id)
        self._recompute_state()

    def _discard_race(self, race_id: int) -> None:
        self._clear_resume_banner()
        self.controller.storage.mark_race_ended(race_id, None)
        self._refresh_race_selector()
