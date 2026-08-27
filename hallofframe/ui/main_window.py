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

from ..controller import CaptureController, calibration_status
from ..export import clipboard_data, export_all_html
from ..framebuffer import FrameBuffer
from ..races import (RaceInfo, RosterLoad, _cell, format_display, load_races,
                     race_key, read_rows, skip_race, write_example)
from ..ui import styles
from ..ui.calibration_dialog import CalibrationDialog
from ..ui.misc_screens import ArmedScreen, RaceOverScreen
from ..ui.race_screen import RaceScreen
from ..ui.ready_screen import ReadyScreen
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
        self._races = []
        self._roster_path: str | None = None
        self._roster_rows: list | None = None
        self._load_result: RosterLoad | None = None
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
        sc("F1", self._toggle_about)
        letters = [sc("C", self._calibrate), sc("E", self._on_e),
                   sc("L", self._load_selected_race),
                   sc("D", self._export_html), sc("R", self._on_r),
                   sc("N", self._next_race), sc("/", self._focus_filter),
                   sc("End", self._end_unlisted)]
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
        self.ready.refresh_recorded(self.controller.storage.race_keys())

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
            kb.set_note(f"Keyboard grabbed while armed — {end} or Esc disarms "
                        "and releases it, then quit normally.")
        elif state == AppState.REVIEW:
            kb.add("↑/↓", "Select crossing", True)
            kb.add("Tab", "Next bow field")
            kb.add("Del", "Soft-delete")
            kb.add("E", "Copy as Excel", callback=self._export)
            kb.add("D", "Save DB HTML", callback=self._export_html)
            kb.add("Esc", "Back to Ready", callback=self._close_review)
        elif state == AppState.RACE_OVER:
            kb.add("R", "Review crossings", True, callback=self._open_review)
            kb.add("E", "Copy as Excel", callback=self._export)
            kb.add("D", "Save DB HTML", callback=self._export_html)
            kb.add("N", "Next race", callback=self._next_race)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
        elif state == AppState.RECALIBRATE:
            kb.add("C", "Calibrate", True, callback=self._calibrate)
            kb.add("Ctrl+Q", "Quit", callback=self._quit)
        else:  # READY / STREAM_DOWN
            kb.add("Ctrl+S", "Arm", True, callback=self._arm_start)
            kb.add("C", "Calibrate", callback=self._calibrate)
            kb.add("L", "Load race", callback=self._load_selected_race)
            kb.add("D", "Save DB HTML", callback=self._export_html)
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
        self._show_toast(f"Armed. Press {start_keys} on the trigger device "
                         f"({device}) to start.", timeout_ms=0)

    def on_evdev_start(self, t_press: float) -> None:
        if not self._armed:
            return
        self._armed = False
        # WP7: an unlisted race records under a provisional (timestamp) key with
        # null race_no/heat_no, identified once afterwards in review.
        race, is_unlisted = self.ready.current_selection()
        self.controller.start_race(
            t_press, name=race.name,
            race_no=None if is_unlisted else race.race_no,
            heat_no=None if is_unlisted else race.heat_no)
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
        self._race_over_race_id = race_id
        self._reviewing = False
        caps = self.controller.storage.captures_for_race(race_id)
        self.race_over.set_summary(list(caps))
        self._advance_race_default = True
        self.ready.reset_provisional()
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
            self._review_screen.identify_requested.connect(self._open_identify)
            self._review_screen.merge_requested.connect(self._open_merge)
        elif self._review_screen.race_id != race_id:
            self._review_screen.race_id = race_id
        self._review_screen.load_captures()
        row = self.controller.storage.get_race(race_id)
        is_unlisted = not (row and (row["race_no"] or ""))
        self._review_screen.refresh_roster_actions(
            is_unlisted=is_unlisted, has_duplicates=bool(self._has_duplicates()))
        self._reviewing = True
        self._recompute_state()
        self._review_screen.setFocus()

    def _has_duplicates(self) -> list:
        """Duplicate roster keys, from the raw rows (the parsed ``_races`` is
        already de-duplicated, so it cannot be the source — WP6 merge)."""
        groups: dict = {}
        if not self._roster_rows:
            return []
        for row in self._roster_rows[1:]:
            r = RaceInfo(race_no=_cell(row[0]),
                         heat_no=_cell(row[1]) if len(row) > 1 else "",
                         name=_cell(row[2]) if len(row) > 2 else "")
            groups.setdefault(r.key, []).append(r)
        return [v for v in groups.values() if len(v) >= 2]

    def _recorded_count_for_key(self, key) -> int:
        return sum(1 for r in self.controller.storage.all_races()
                   if race_key(r["race_no"], r["heat_no"], r["name"]) == key)

    def _open_identify(self) -> None:
        race_id = self._review_race_id or self.controller.race_id
        if race_id is None:
            return
        row = self.controller.storage.get_race(race_id)
        if not row or (row["race_no"] or ""):
            self._show_toast("This race has already been identified.")
            return
        if not self._roster_path:
            self._show_toast("No roster loaded.")
            return
        from ..ui.roster_dialog import IdentifyDialog
        dlg = IdentifyDialog(self._roster_path, row["id"], row["name"],
                             self.controller.storage,
                             expected=self._roster_rows, logger=self._logger,
                             parent=self)
        dlg.result_applied.connect(self._apply_roster_result)
        dlg.exec()

    def _open_merge(self) -> None:
        groups = self._has_duplicates()
        if not groups or not self._roster_path:
            self._show_toast("No duplicate roster rows to merge.")
            return
        dup = groups[0]
        # Merge availability is decided by the number of *recorded races* for
        # the shared key (BEHAVIOUR §8): 0 = pure CSV merge, 1 = re-point that
        # race, >=2 = two real results, blocked.
        count = self._recorded_count_for_key(dup[0].key)
        if count >= 2:
            self._show_toast("Two recorded races share this key — merge is "
                             "unavailable.")
            return
        recorded = self.controller.storage.race_keys()
        keep = next((r for r in dup if r.key in recorded), dup[0])
        remove = next((r for r in dup if r is not keep), dup[1])
        from ..ui.roster_dialog import MergeDialog
        dlg = MergeDialog(self._roster_path, keep, remove,
                          self.controller.storage,
                          expected=self._roster_rows, parent=self,
                          recorded_count=count, logger=self._logger)
        dlg.result_applied.connect(self._apply_roster_result)
        dlg.exec()

    def _close_review(self) -> None:
        self._reviewing = False
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
        if getattr(row, "kind", None) == "create":
            self._open_add_race(race_no=row.create_race_no,
                                heat_no=row.create_heat_no)

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
        self.race_over.set_summary(list(caps))
        self._recompute_state()

    def _on_e(self) -> None:
        """E: rename the selected race in Ready/review; export in Race-over."""
        if self._last_state == AppState.RACE_OVER:
            self._export()
            return
        self._open_rename()

    # ---------------------------------------------------------------- roster editing
    def _open_add_race(self, race_no: str = "", heat_no: str = "") -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't edit the roster while armed or recording.")
            return
        if not self._roster_path:
            self._show_toast("No roster loaded — Load roster… first.")
            return
        from ..ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(self._roster_path, race_no, heat_no,
                            expected=self._roster_rows, logger=self._logger,
                            parent=self)
        dlg.result_applied.connect(self._apply_roster_result)
        dlg.exec()
        chosen = dlg.chosen_race
        if chosen is not None:
            self._select_race_by_key(chosen.key)

    def _select_race_by_key(self, key) -> None:
        recorded = self.controller.storage.race_keys()
        self.ready.set_races(self._races, recorded=recorded,
                             skipped=self._skipped_keys())
        # Rebuild happened inside set_races; select the row by key.
        for i, row in enumerate(self.ready._rows):
            if row.kind == "race" and row.key == key:
                self.ready._sel_row = i
                self.ready._sync_combo()
                return

    def _open_rename(self) -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't rename while armed or recording.")
            return
        if not self._roster_path:
            self._show_toast("No roster loaded — Load roster… first.")
            return
        if self._reviewing:
            # E in review renames the race under review, not the Ready selection.
            row = self.controller.storage.get_race(self.controller.race_id)
            if not row or not (row["race_no"] or ""):
                self._show_toast("Nothing to rename on an unlisted race.")
                return
            race = RaceInfo(race_no=row["race_no"], heat_no=row["heat_no"],
                            name=row["name"])
        else:
            if self.ready.selected_is_unlisted():
                self._show_toast("Nothing to rename on an unlisted race.")
                return
            race, _ = self.ready.current_selection()
            if race is None:
                return
        from ..ui.roster_dialog import RenameDialog
        dlg = RenameDialog(self._roster_path, race,
                           self.controller.storage.race_keys(),
                           self.controller.storage,
                           expected=self._roster_rows, logger=self._logger,
                           parent=self)
        dlg.result_applied.connect(self._apply_roster_result)
        dlg.exec()

    def _toggle_skip(self) -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't skip while armed or recording.")
            return
        if self.ready.selected_is_unlisted():
            return
        race, _ = self.ready.current_selection()
        if race is None or not self._roster_path:
            return
        skipping = race.key not in self._skipped_keys()
        try:
            result = skip_race(self._roster_path, race.key, skip=skipping,
                               expected=self._roster_rows)
        except Exception as exc:
            self._show_toast(f"Could not update roster: {exc}")
            return
        if self._logger is not None:
            self._logger.info("roster", "skip" if skipping else "unskip",
                              key=str(race.key), name=race.name,
                              file=self._roster_path)
        self._apply_roster_result(result)

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
    def _load_races(self, csv_path: str | None = None) -> None:
        """Load the roster (startup or a manual Load roster…) and surface the
        result loudly: fill the roster chip and render any banner (F10).

        A configured-but-missing path never auto-writes an example roster; it is
        offered as an action instead (BEHAVIOUR §4).
        """
        import os
        if csv_path is None:
            races_cfg = self.config.section("races")
            csv_path = os.path.expanduser(races_cfg["csv_path"])
            if not os.path.isabs(csv_path):
                csv_path = os.path.join(self.config.data_root, csv_path)
        self._roster_path = csv_path
        result = load_races(csv_path)
        self._apply_roster_result(result)

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

    def _skipped_keys(self) -> set:
        keys = set()
        if not self._roster_rows:
            return keys
        for row in self._roster_rows[1:]:
            if len(row) >= 5 and row[4] == "skipped":
                r = RaceInfo(race_no=_cell(row[0]),
                             heat_no=_cell(row[1]) if len(row) > 1 else "",
                             name=_cell(row[2]) if len(row) > 2 else "")
                keys.add(r.key)
        return keys

    def _apply_roster_result(self, result: RosterLoad) -> None:
        self._load_result = result
        self._races = result.races
        self._roster_rows = read_rows(self._roster_path) \
            if self._roster_path else None
        recorded = self.controller.storage.race_keys()
        if result.missing:
            # No roster found: still armable. Default the picker to a synthetic
            # race 000 / heat 1 so the operator can start immediately.
            picker_races = [RaceInfo(race_no="000", heat_no="1")]
        else:
            picker_races = self._races
        self.ready.set_races(picker_races, recorded=recorded,
                             skipped=self._skipped_keys())
        self._render_roster_chip(result)
        self._render_roster_banner(result, recorded)

    def _reload_roster(self) -> None:
        self._load_races(self._roster_path)

    def _load_roster_dialog(self) -> None:
        if self._last_state in (AppState.ARMED, AppState.RECORDING):
            self._show_toast("Can't load a roster while armed or recording.")
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Load roster…", str(self.config.data_root),
            "Roster CSV (*.csv);;All files (*)")
        if path:
            self._load_races(path)

    def _write_example_roster(self) -> None:
        if self._roster_path:
            try:
                write_example(self._roster_path)
            except OSError as exc:
                self._show_toast(f"Could not write example roster: {exc}")
                return
            self._load_races(self._roster_path)

    def _show_duplicates(self) -> None:
        if not self._load_result or not self._load_result.duplicates:
            return
        parts = [f"{self._key_display(k)} (lines {a}, {b})"
                 for k, a, b in self._load_result.duplicates]
        self._show_toast("Duplicates: " + "; ".join(parts))

    def _show_dropped(self, dropped) -> None:
        names = " · ".join(self._key_display(d) for d in dropped[:10])
        if len(dropped) > 10:
            names += f" … +{len(dropped) - 10} more"
        self._show_toast(f"Recorded, not in roster: {names}", timeout_ms=12000)
