"""Main window (spec §7). Status bar, live preview, capture list, controls.

During a race there are NO modal dialogs (§7.5); errors appear as a dismissible
banner. Start-Race is armed via Qt but the actual t0 timestamp comes from evdev
(§5.3, §7.4).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QVBoxLayout, QWidget)

from ..controller import CaptureController
from ..export import export_csv
from ..framebuffer import FrameBuffer
from ..ui.calibration_dialog import CalibrationDialog
from ..ui.capture_list import CaptureList, FrameReviewPanel
from ..ui.preview_widget import PreviewWidget
from ..ui.review_dialog import RaceReviewDialog


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

        self.setWindowTitle("Regatta Finish-Line Timer")
        self.resize(1280, 720)

        central = QWidget()
        root = QVBoxLayout(central)
        self.banner = QLabel("")
        self.banner.setStyleSheet("background:#c0392b; color:white; padding:4px;")
        self.banner.hide()
        root.addWidget(self.banner)

        self.status = QLabel("No race")
        root.addWidget(self.status)

        mid = QHBoxLayout()
        self.preview = PreviewWidget(buffer)
        mid.addWidget(self.preview, 3)
        self.list = CaptureList()
        self.list.capture_clicked.connect(self._open_review)
        self.list.bow_edited.connect(self._bow_edited)
        self.list.delete_requested.connect(self._delete)
        mid.addWidget(self.list, 2)
        root.addLayout(mid, 1)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("Arm Start Race (Ctrl+S)")
        self.start_btn.clicked.connect(self._arm_start)
        controls.addWidget(self.start_btn)
        self.end_btn = QPushButton("End Race")
        self.end_btn.setEnabled(False)
        self.end_btn.clicked.connect(self._end_race)
        controls.addWidget(self.end_btn)
        self.cal_btn = QPushButton("Calibrate")
        self.cal_btn.clicked.connect(self._calibrate)
        controls.addWidget(self.cal_btn)
        self.review_btn = QPushButton("Review Race")
        self.review_btn.setEnabled(False)
        self.review_btn.clicked.connect(self._open_review_dialog)
        controls.addWidget(self.review_btn)
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self._export)
        controls.addWidget(self.export_btn)
        quit_btn = QPushButton("Quit (Ctrl+Q)")
        quit_btn.clicked.connect(self._quit)
        controls.addWidget(quit_btn)
        trig = self.config.section("trigger")
        start_keys = keycode_names(trig["start_keycodes"])
        end_keys = keycode_names(trig["end_keycodes"])
        self.trigger_label = QLabel(
            f"Start: {start_keys} · End: {end_keys}  "
            f"({trig['device_path'] or 'Qt fallback'})")
        controls.addWidget(self.trigger_label)
        root.addLayout(controls)

        self.setCentralWidget(central)

        # Preview timer (§7.2)
        preview_fps = float(self.config.section("ui")["preview_fps"])
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self.preview.refresh)
        self.preview_timer.start(int(1000 / preview_fps))

        # Status timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(500)

        self._armed = False
        self._race_over = False
        self._race_over_detail = ""
        self._stream_down_notified = False
        self._cal_stale_notified = False
        self._cal_check_at = 0.0
        self._cal_detail = ""
        self._last_capture: int | None = None
        self._review_dialog = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self._connect_controller()
        self._install_shortcuts()

    def _connect_controller(self) -> None:
        # signal_capture_added is overridden in main.py to route through the Qt
        # bridge; this default keeps the controller usable outside Qt (tests).
        self.controller.signal_capture_added = self.on_capture
        self.controller.signal_warning = self._show_banner

    def _flash_capture(self, cap) -> None:
        """Transient on-screen confirmation that a crossing registered."""
        if self._armed:
            # Arming text is more important; don't clobber it. Still give a
            # small flash so the press is visibly acknowledged.
            self.status.setText(
                f"CAPTURE #{cap.sequence:03d}  {_fmt_clock(cap.elapsed_s)}")
        else:
            self.banner.setText(
                f"CAPTURE #{cap.sequence:03d}  {_fmt_clock(cap.elapsed_s)}")
            self.banner.show()
        self._flash_timer.start(900)

    def _end_flash(self) -> None:
        # Let the normal status/elapsed update resume; the last capture stays
        # visible in the running status line until the next tick.
        if self.controller.running:
            return
        self.banner.hide()

    def _show_banner(self, msg: str) -> None:
        self.banner.setText(msg)
        self.banner.show()

    def _dismiss_banner(self) -> None:
        self.banner.hide()

    def _update_status(self) -> None:
        if self.controller.running:
            el = time.monotonic() - self.controller.t0
            fps = self.trigger.fps if self.trigger else 0.0
            last = f"  · last capture #{self._last_capture:03d}" if self._last_capture else ""
            self.status.setText(
                f"REC {_fmt_clock(el)}   {fps:.1f} fps{last}")
            return
        if self._race_over:
            self.status.setText(self._race_over_detail)
            return
        alive, fps, _ = self.buffer.health()
        if not alive:
            self.status.setText("No race · STREAM DOWN")
            if not self._stream_down_notified:
                self._stream_down_notified = True
                self._cal_stale_notified = False
                self._show_banner(
                    "STREAM DOWN — no frames arriving. Check the phone camera "
                    "app and cable (tunnel on 8081).")
            return

        self._stream_down_notified = False
        # Re-check calibration against the live stream ~every 3 s (decodes a
        # frame for resolution, so avoid doing it every 500 ms).
        if time.monotonic() - self._cal_check_at >= 3.0:
            from ..controller import calibration_status
            self._cal_check_at = time.monotonic()
            ok, self._cal_detail = calibration_status(self.config, self.buffer)
            if ok:
                self._cal_stale_notified = False

        if self._cal_detail:
            self.status.setText(
                f"No race · stream OK ({fps:.1f} fps) · {self._cal_detail}")
            if not self._cal_stale_notified:
                self._cal_stale_notified = True
                self._show_banner(
                    f"RECALIBRATION NEEDED — {self._cal_detail}. "
                    "Click Calibrate before starting.")
        else:
            self.status.setText(f"No race · stream OK ({fps:.1f} fps)")

    def _arm_start(self) -> None:
        # Confirmation happens BEFORE arming (§5.3, §7.4); the next press on the
        # start keycode is t0.
        trig = self.config.section("trigger")
        start_keys = keycode_names(trig["start_keycodes"])
        device = trig["device_path"] or "keyboard"
        self._armed = True
        self._show_banner(
            f"Armed. Press {start_keys} on the trigger device ({device}) to start.")
        self.start_btn.setText(f"Armed — press {start_keys}")

    def on_evdev_start(self, t_press: float) -> None:
        if not self._armed:
            return
        self._armed = False
        self.controller.start_race(t_press, name=time.strftime("Race-%Y%m%d-%H%M"))
        if self.controller.running:
            self._race_over = False
            self.end_btn.setEnabled(True)
        self._dismiss_banner()
        self.start_btn.setText("Arm Start Race (Ctrl+S)")

    def on_evdev_crossing(self, t_press: float, code: int) -> None:
        self.controller.record_crossing(t_press)

    def on_evdev_end(self, t_press: float) -> None:
        self._end_race()

    def on_race_ended(self, race_id: int) -> None:
        n = self.controller.ended_capture_count
        last = (f"last capture #{self._last_capture:03d}"
                if self._last_capture else "no ends recorded")
        self._race_over = True
        self._race_over_detail = f"RACE OVER — {n} ends · {last}"
        self.end_btn.setEnabled(False)
        self.review_btn.setEnabled(True)
        self._show_banner(
            f"RACE OVER — {n} ends recorded. Archive stopped; trigger keyboard "
            "released (you can use Ctrl+Q or Quit).")

    def _end_race(self) -> None:
        self.controller.end_race()

    def _quit(self) -> None:
        self.close()

    def on_capture(self, cap) -> None:
        primary = cap.primary_image if hasattr(cap, "primary_image") else None
        self.list.add_capture(cap.sequence, cap.elapsed_s,
                              str(self.config.data_root / primary) if primary else None,
                              suspect=cap.debounce_suspect,
                              image_flag=cap.image_flag)
        # Instant, non-modal confirmation so the operator sees each press register
        # (the list row updates a beat later once images are selected). A stale
        # "last capture" lets the operator know which press ended the sequence.
        self._last_capture = cap.sequence
        self._flash_capture(cap)

    def _open_review(self, sequence: int) -> None:
        if self.controller.race_id is None:
            return
        race = self.controller.storage.captures_for_race(self.controller.race_id)
        cap = next((c for c in race if c["sequence"] == sequence), None)
        if cap is None:
            return
        frames = self.controller.storage.frames_for_capture(cap["id"])
        fl = [(str(self.config.data_root / f["path"]), f["offset_ms"])
              for f in frames]
        panel = FrameReviewPanel(cap["id"], fl, self)
        # Without the Window flag a parented QWidget is a child and just draws
        # inside the main window; make it a real, raisable top-level window.
        panel.setWindowFlag(Qt.Window, True)
        panel.setWindowTitle(f"Capture {sequence}")
        panel.resize(max(640, panel.sizeHint().width()),
                     max(480, panel.sizeHint().height()))
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def _bow_edited(self, sequence: int, value: str) -> None:
        if self.controller.race_id is None:
            return
        race = self.controller.storage.captures_for_race(self.controller.race_id)
        cap = next((c for c in race if c["sequence"] == sequence), None)
        if cap:
            self.controller.set_bow_number(cap["id"], value or None)
        self.list.update_bow(sequence, value)

    def _delete(self, sequence: int) -> None:
        if self.controller.race_id is None:
            return
        race = self.controller.storage.captures_for_race(self.controller.race_id)
        cap = next((c for c in race if c["sequence"] == sequence), None)
        if cap:
            self.controller.soft_delete(cap["id"])

    def _calibrate(self) -> None:
        # Non-modal so the full-screen counter can appear on top during capture
        # (a modal exec() would block it — spec §5.5 constraint 2 is sequential).
        dlg = CalibrationDialog(self.buffer, self.config.data_root, self.config, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _open_review_dialog(self) -> None:
        if self.controller.race_id is None:
            return
        if self._review_dialog is not None:
            self._review_dialog.show()
            self._review_dialog.raise_()
            self._review_dialog.activateWindow()
            return
        dlg = RaceReviewDialog(self.controller, self.controller.race_id,
                               self.config.data_root, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.bow_edited.connect(self._bow_edited)
        dlg.delete_requested.connect(self._delete)
        dlg.open_frames.connect(self._open_review)
        dlg.destroyed.connect(lambda: setattr(self, "_review_dialog", None))
        self._review_dialog = dlg
        dlg.show()

    def _export(self) -> None:
        if self.controller.race_id is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", str(self.config.data_root / "export.csv"), "CSV (*.csv)")
        if path:
            export_csv(self.controller.storage, self.controller.race_id, path)

    def _install_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._arm_start)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.controller.undo_last)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=lambda: None)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self._quit)


def _fmt_clock(secs: float) -> str:
    ms = int(secs * 1000)
    hours, rem = divmod(ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis}"
    return f"{minutes:02d}:{seconds:02d}.{millis}"
