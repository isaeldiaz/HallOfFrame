"""Live preview widget (spec §7.2).

Decodes at reduced size using QImageReader.setScaledSize() (DCT-domain
scaling, roughly an order of magnitude cheaper than a full decode on the
dual-core i7-6600U), at preview_fps, independent of ingest rate. Draws a
draggable, persisted finish-line overlay.
"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, Qt, Signal
from PySide6.QtGui import QImageReader, QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget

from ..framebuffer import FrameBuffer


class PreviewWidget(QWidget):
    finish_line_moved = Signal(float)

    def __init__(self, buffer: FrameBuffer, parent=None):
        super().__init__(parent)
        self.buffer = buffer
        self._preview_fps = 10
        self._preview_scale = 0.25  # 1080p -> ~480px
        self.finish_line_x = 0.5
        self._last_frame: bytes | None = None
        self._pm = None
        self._dragging = False
        self.setMinimumSize(320, 200)

    def set_finish_line(self, x: float) -> None:
        self.finish_line_x = max(0.0, min(1.0, x))
        self.update()

    def refresh(self) -> None:
        """Pull the newest frame from the buffer (called by a QTimer)."""
        frame = self.buffer.nearest(1e30)
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
