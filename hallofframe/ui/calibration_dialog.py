"""Calibration dialog (spec §5.5).

Sequential by design (single-display constraint 2): a full-screen millisecond
counter, then capture of 20 frames, then (after leaving full screen) entry of
the 20 counter values one at a time. Writes calibration.json.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import calibration as cal
from ..framebuffer import FrameBuffer

_SAMPLES = 20


class FullScreenCounter(QWidget):
    """Large high-contrast monotonic-milliseconds counter (spec §5.5 step 1)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(
            "background: #000000; color: #ffffff; border: none;")
        self.label = QLabel("", self)
        self.label.setAlignment(Qt.AlignCenter)
        font = QFont("sans-serif", 180, QFont.Bold)
        self.label.setFont(font)
        # Slightly off-white, not pure white: extreme contrast over a dark scene
        # blows the digits out in the phone's exposure (overexposure).
        self.label.setStyleSheet("color: #d8d8d8; background: #000000;")
        instr = QLabel("RECORDING — POINT THE PHONE AT THIS COUNTER",
                       self, alignment=Qt.AlignHCenter)
        instr.setStyleSheet("color: #ff4444; background: #000000;"
                            "font-size: 34px; font-weight: bold;")
        lay = QVBoxLayout(self)
        lay.addWidget(self.label, 1)
        lay.addWidget(instr)
        lay.setContentsMargins(0, 0, 0, 60)
        # Show time.monotonic() in ms (spec §5.5) — the SAME domain as
        # Frame.t_recv, so L = t_recv - T_shown is meaningful. (ms since boot.)
        # Update at the panel rate (~16 ms / 60 Hz), not 5 ms: digits that change
        # faster than the camera exposure smear (the fast ms digits blur while
        # the stable high digits stay crisp). One change per exposure frame.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def present(self) -> None:
        """Full-screen, front, focused. Do NOT call show() again afterwards —
        it knocks the window out of fullscreen and behind the app."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        # Some WMs/compositors ignore the initial raise or get it wrong; re-raise
        # on a timer so nothing can sit on top of the counter during capture.
        self._keep_top = QTimer(self)
        self._keep_top.timeout.connect(self._top)
        self._keep_top.start(100)

    def _top(self) -> None:
        self.raise_()
        self.activateWindow()

    def close(self) -> bool:  # noqa: N802
        if getattr(self, "_keep_top", None):
            self._keep_top.stop()
        return super().close()

    def _tick(self) -> None:
        ms = int(time.monotonic() * 1000)
        # Space the digits so they stay distinguishable under slight blur.
        self.label.setText(" ".join(f"{ms:08d}"))


class CalibrationDialog(QDialog):
    def __init__(self, buffer: FrameBuffer, data_root, config, parent=None):
        super().__init__(parent)
        self.buffer = buffer
        self.data_root = data_root
        self.config = config
        self.captured: list = []

        self.setWindowTitle("Calibrate latency")
        lay = QVBoxLayout(self)
        hint = QLabel(
            "Point the phone at the full-screen counter at ~0.5 m so it fills "
            "the frame, then press Capture. Use the same lens/resolution/fps "
            "the race will use, over a HIGH-ENTROPY background (spec §5.5).\n\n"
            "After capture, the 20 saved images are shown below. For each image, "
            "read the NUMBER on the counter visible in that image (an 8-digit "
            "millisecond value) and type it into the box beside it.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        row = QHBoxLayout()
        self.capture_btn = QPushButton("Capture 20 frames")
        self.capture_btn.clicked.connect(self._capture)
        row.addWidget(self.capture_btn)
        lay.addLayout(row)

        self.status = QLabel("No frames captured.")
        lay.addWidget(self.status)

        # Scrollable area that holds one image + input row per captured frame.
        self.entries_host = QWidget()
        self.entries_layout = QVBoxLayout(self.entries_host)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.entries_host)
        self.scroll.setMinimumHeight(420)
        lay.addWidget(self.scroll, 1)

        self.value_fields: list[QLineEdit] = []
        self.finish_btn = QPushButton("Compute and save calibration.json")
        self.finish_btn.setEnabled(False)
        self.finish_btn.clicked.connect(self._finish)
        lay.addWidget(self.finish_btn)

    def _capture(self) -> None:
        # Non-blocking so the full-screen counter actually paints and ticks while
        # the phone films it (spec §5.5). Sample the buffer on a QTimer; each tick
        # must not block the event loop.
        self.counter = FullScreenCounter()
        self.counter.present()
        try:
            self._capture_impl()
        except Exception as exc:
            self.counter.close()
            self.show()
            self.capture_btn.setEnabled(True)
            QMessageBox.critical(self, "Calibration", f"Capture error: {exc}")

    def _capture_impl(self) -> None:
        self.captured = []
        self._seen = set()
        # Frames received at/after this instant are the ones showing the counter.
        # The 10 s ring buffer also holds older frames from before the counter
        # appeared — those must NOT be sampled (§5.5).
        self._t0_capture = time.monotonic()
        self.capture_btn.setEnabled(False)
        self.status.setText("Capturing... keep the phone on the counter.")
        self._deadline = time.monotonic() + 10.0
        self._cap_timer = QTimer(self)
        self._cap_timer.timeout.connect(self._sample)
        # Hide the modal dialog so the full-screen counter is the only thing on
        # screen (spec §5.5 constraint 2 is sequential). Re-shown in _finish_capture.
        self.hide()
        # IMPORTANT: do NOT start the sampling timer immediately. An immediate
        # tick (interval 0) accesses the shared FrameBuffer while the full-screen
        # counter is still being mapped, which prevents the counter window from
        # ever appearing when the reader thread is live. Start only after the
        # 1.5 s aiming delay so the counter is on screen first.
        QTimer.singleShot(1500, lambda: self._cap_timer.start(30))

    def _sample(self) -> None:
        span = self.buffer.span()
        if span is not None:
            try:
                with self.buffer._lock:
                    snapshot = list(self.buffer._buf)
            except AttributeError:
                snapshot = []
            # Walk NEWEST first (the deque is oldest→newest, so reversed), and
            # stop as soon as frames fall before the capture started — those are
            # stale pre-counter frames and every earlier one is older still.
            for f in reversed(snapshot):
                if f.t_recv < self._t0_capture:
                    break
                if id(f) not in self._seen:
                    self._seen.add(id(f))
                    self.captured.append(f)
                    if len(self.captured) >= _SAMPLES:
                        break
        if len(self.captured) >= _SAMPLES:
            self._cap_timer.stop()
            self._finish_capture()
        elif time.monotonic() > self._deadline:
            self._cap_timer.stop()
            self._finish_capture(partial=True)

    def _finish_capture(self, partial: bool = False) -> None:
        self.counter.close()
        self.show()  # bring the dialog back over the (now closed) counter
        self.capture_btn.setEnabled(True)
        frames = self.captured
        if len(frames) < _SAMPLES:
            QMessageBox.warning(self, "Calibration",
                                f"Only {len(frames)}/{_SAMPLES} frames captured. "
                                "Is the stream running and the phone in focus?")
            return
        self.captured = frames
        # Save the captured JPEGs so the operator can read the counter value
        # visible in each (spec §5.5 step 3 + 5).
        caldir = self.data_root / "calibration_frames"
        caldir.mkdir(parents=True, exist_ok=True)
        self.frame_paths = []
        for i, f in enumerate(frames, 1):
            p = caldir / f"frame_{i:02d}_{f.seq:08d}.jpg"
            p.write_bytes(f.jpeg)
            self.frame_paths.append(p)

        self.status.setText(
            f"Captured {len(frames)} frames at "
            f"{int(self.buffer.assumed_fps)} fps nominal. For each image, read "
            "the 8-digit counter number and type it into the box beside it.")
        # Clear any previous entries.
        while self.entries_layout.count():
            item = self.entries_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.value_fields = []
        for i, path in enumerate(self.frame_paths, 1):
            lay_row = QHBoxLayout()
            img = QLabel()
            pm = QPixmap(str(path))
            img.setPixmap(pm.scaled(460, 346, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation))
            img.setAlignment(Qt.AlignCenter)
            lay_row.addWidget(img, 1)
            right = QVBoxLayout()
            right.addWidget(QLabel(f"Image {i}: counter shown (ms)"))
            fld = QLineEdit()
            fld.setPlaceholderText("e.g. 83270995")
            right.addWidget(fld)
            right.addStretch(1)
            lay_row.addLayout(right)
            self.entries_layout.addLayout(lay_row)
            self.value_fields.append(fld)
        self.entries_layout.addStretch(1)
        self.finish_btn.setEnabled(True)

    def _finish(self) -> None:
        values = []
        for fld in self.value_fields:
            text = fld.text().strip()
            if not text:
                QMessageBox.warning(self, "Calibration", "All fields required.")
                return
            try:
                values.append(float(text))
            except ValueError:
                QMessageBox.warning(self, "Calibration", f"Not a number: {text!r}")
                return
        result = cal.compute_latency(self.captured, values)
        resolution, mean_bytes = _measure_format(self.captured)
        cal.write_calibration(
            self.data_root, result["latency_median_ms"], result["latency_iqr_ms"],
            result["samples_ms"], self.config.section("timing")["viewing_mode"],
            resolution=resolution, fps=_measure_fps(self.captured), lens="",
            mean_frame_bytes=mean_bytes)
        QMessageBox.information(
            self, "Calibration",
            f"median L = {result['latency_median_ms']:.1f} ms\n"
            f"IQR      = {result['latency_iqr_ms']:.1f} ms\n\n"
            "Latency written to calibration.json.")
        self.accept()


def _measure_fps(frames):
    """Actual stream fps from the captured frames' arrival timestamps."""
    if len(frames) < 2:
        return 0.0
    dt = abs(frames[-1].t_recv - frames[0].t_recv)
    if dt <= 0:
        return 0.0
    return (len(frames) - 1) / dt


def _measure_format(frames):
    """Return (resolution_string, mean_frame_bytes) from the captured frames."""
    from PIL import Image
    import io as _io
    w = h = 0
    total = 0
    for f in frames:
        total += len(f.jpeg)
        try:
            im = Image.open(_io.BytesIO(f.jpeg))
            im.load()
            w, h = im.size
        except Exception:
            pass
    mean = int(total / len(frames)) if frames else 0
    res = f"{w}x{h}" if w and h else ""
    return res, mean
