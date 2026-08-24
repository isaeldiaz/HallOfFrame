"""Live preview widget (spec §7.2).

Decodes at reduced size using QImageReader.setScaledSize() (DCT-domain
scaling, roughly an order of magnitude cheaper than a full decode on the
dual-core i7-6600U), at preview_fps, independent of ingest rate. Draws a
draggable, persisted finish-line overlay.
"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QTimer, Qt, Signal
from PySide6.QtGui import QImageReader, QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget

from ..framebuffer import FrameBuffer


class PreviewWidget(QWidget):
    finish_line_moved = Signal(float)

    def __init__(self, buffer: FrameBuffer, parent=None):
        super().__init__(parent)
        self.buffer = buffer
        self._preview_fps = 10
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000 // self._preview_fps)
        self._preview_scale = 0.25  # 1080p -> ~480px
        self.finish_line_x = 0.5
        self._last_frame: bytes | None = None
        self._pm = None
        self._dragging = False
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.lag_s: float | None = None  # measured glass-to-screen lag (screen mode)

    def nudge(self, delta: float) -> None:
        """Move the finish line by a fraction (0..1). Arrow keys call this."""
        self.set_finish_line(self.finish_line_x + delta)
        self.finish_line_moved.emit(self.finish_line_x)

    def keyPressEvent(self, event):  # noqa: N802
        # ←/→ nudge the finish line; Shift for fine steps (§5). 0.5% / 0.1%.
        if event.key() == Qt.Key_Left:
            step = 0.001 if event.modifiers() & Qt.ShiftModifier else 0.005
            self.nudge(-step)
            return
        if event.key() == Qt.Key_Right:
            step = 0.001 if event.modifiers() & Qt.ShiftModifier else 0.005
            self.nudge(step)
            return
        super().keyPressEvent(event)

    def set_lag(self, lag_s: float | None) -> None:
        self.lag_s = lag_s
        self.update()

    def set_finish_line(self, x: float) -> None:
        self.finish_line_x = max(0.0, min(1.0, x))
        self.update()

    def refresh(self) -> None:
        """Pull the newest frame from the buffer (called by a QTimer)."""
        frame = self.buffer.newest()  # O(1); nearest(1e30) walked all frames (§2.4)
        if frame is None:
            return
        if frame.jpeg == self._last_frame:
            return
        self._last_frame = frame.jpeg
        buf = QBuffer()
        buf.setData(frame.jpeg)
        buf.open(QBuffer.ReadOnly)
        reader = QImageReader(buf)
        if reader.canRead():
            target_w = max(1, int(self.width() * 0.7))
            reader.setScaledSize(reader.size().scaled(
                target_w, target_w, Qt.KeepAspectRatio))
            img = reader.read()
            if not img.isNull():
                self._pm = img.scaled(self.size(), Qt.KeepAspectRatio,
                                      Qt.FastTransformation)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 20))
        if self._pm is not None:
            x = (self.width() - self._pm.width()) // 2
            y = (self.height() - self._pm.height()) // 2
            p.drawImage(x, y, self._pm)
        # finish-line overlay
        fx = int(self.finish_line_x * self.width())
        pen = QPen(QColor(255, 60, 60), 2)
        p.setPen(pen)
        p.drawLine(fx, 0, fx, self.height())
        p.end()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._set_line(event.position().x())

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._dragging:
            self._set_line(event.position().x())

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.finish_line_moved.emit(self.finish_line_x)

    def _set_line(self, x: float) -> None:
        self.finish_line_x = max(0.0, min(1.0, x / max(1, self.width())))
        self.update()
