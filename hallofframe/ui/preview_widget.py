"""Live preview widget (spec §7.2, §13.3).

Decodes at reduced size through :func:`images.load_view` (DCT-domain scaling,
roughly an order of magnitude cheaper than a full decode on the dual-core
i7-6600U), at preview_fps, independent of ingest rate. Draws a draggable,
persisted finish-line overlay.

The zoom/ROI nice-to-have (spec §13.3) lives here: ``Z`` enters draw-zoom mode
and the next left-drag draws a rectangle locked to the pixmap's aspect ratio,
composed with the current ROI and handed to :func:`images.set_view_roi`. The
finish line is stored full-frame image-normalised and drawn at its position
inside the current ROI.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..framebuffer import FrameBuffer
from .images import load_view, set_view_roi, view_roi


class PreviewWidget(QWidget):
    finish_line_moved = Signal(float)
    roi_changed = Signal(object)          # tuple | None
    zoom_mode_changed = Signal(bool)

    def __init__(self, buffer: FrameBuffer, parent=None):
        super().__init__(parent)
        self.buffer = buffer
        self._preview_fps = 10
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000 // self._preview_fps)
        self._preview_scale = 0.25  # 1080p -> ~480px
        # Full-frame image-normalised finish line (spec §13.3): the web renderer
        # already assumed 50 % = image centre; the app now matches it.
        self.finish_line_x = 0.5
        self._last_frame: bytes | None = None
        self._pm = None
        self._img_rect = QRect()
        self._roi = view_roi()
        self._dragging = False
        self._draw_zoom = False
        self._anchor: QPointF | None = None
        self._rubber: QRectF | None = None
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.lag_s: float | None = None  # measured glass-to-screen lag (screen mode)

    # --- finish line ------------------------------------------------------
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

    # --- ROI --------------------------------------------------------------
    def set_roi(self, roi) -> None:
        """Restore a ROI at startup (no ``roi_changed`` signal)."""
        set_view_roi(roi)
        self._roi = view_roi()
        self.update()

    def reset_zoom(self) -> None:
        set_view_roi(None)
        self._roi = None
        self.roi_changed.emit(None)
        self.update()

    def begin_zoom_draw(self) -> None:
        self._draw_zoom = True
        self._anchor = None
        self._rubber = None
        self.zoom_mode_changed.emit(True)
        self.update()

    def cancel_zoom_draw(self) -> None:
        self._draw_zoom = False
        self._anchor = None
        self._rubber = None
        self.zoom_mode_changed.emit(False)
        self.update()

    def is_zoom_draw(self) -> bool:
        return self._draw_zoom

    # --- frame ------------------------------------------------------------
    def refresh(self) -> None:
        """Pull the newest frame from the buffer (called by a QTimer)."""
        frame = self.buffer.newest()  # O(1); nearest(1e30) walked all frames (§2.4)
        if frame is None:
            return
        if frame.jpeg == self._last_frame:
            return
        self._last_frame = frame.jpeg
        target_w = max(1, int(self.width() * 0.7))
        pm = load_view(frame.jpeg, QSize(target_w, target_w))
        if pm is not None:
            self._pm = pm.scaled(self.size(), Qt.KeepAspectRatio,
                                 Qt.FastTransformation)
        self._img_rect = self._img_geometry()
        self.update()

    def _img_geometry(self) -> QRect:
        if self._pm is None:
            return QRect()
        x = (self.width() - self._pm.width()) // 2
        y = (self.height() - self._pm.height()) // 2
        return QRect(x, y, self._pm.width(), self._pm.height())

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 20))
        self._img_rect = self._img_geometry()
        if self._pm is not None:
            p.drawPixmap(self._img_rect.topLeft(), self._pm)
            self._paint_finish_line(p)
            if self._draw_zoom:
                self._paint_zoom(p)
        p.end()

    def _paint_finish_line(self, p: QPainter) -> None:
        if not self._img_rect.isValid():
            return
        roi = self._roi or (0.0, 0.0, 1.0, 1.0)
        fx = self._img_rect.left() + (
            (self.finish_line_x - roi[0]) / roi[2]) * self._img_rect.width()
        if self._img_rect.left() <= fx <= self._img_rect.right():
            p.setPen(QPen(QColor(255, 60, 60), 2))
            p.drawLine(int(fx), self._img_rect.top(),
                       int(fx), self._img_rect.bottom())
        else:
            p.setPen(QColor(255, 180, 58))
            p.drawText(self._img_rect.left() + 8,
                       self._img_rect.top() + 20, "line outside zoom")

    def _paint_zoom(self, p: QPainter) -> None:
        if not self._img_rect.isValid():
            return
        p.setPen(QPen(QColor(255, 180, 58), 1, Qt.DashLine))
        p.drawRect(self._img_rect.adjusted(0, 0, -1, -1))
        if self._rubber is not None:
            p.setPen(QPen(QColor(255, 180, 58), 2))
            p.drawRect(self._rubber)

    # --- mouse ------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        if self._draw_zoom:
            self._anchor = self._clamp_to_img(event.position())
            self._rubber = QRectF(self._anchor, self._anchor)
            self.update()
            return
        self._dragging = True
        self._set_line(event.position().x())

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._draw_zoom and self._anchor is not None:
            self._update_rubber(event.position())
            self.update()
            return
        if self._dragging:
            self._set_line(event.position().x())

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        if self._draw_zoom:
            self._finish_zoom_draw()
            return
        self._dragging = False
        self.finish_line_moved.emit(self.finish_line_x)

    def _clamp_to_img(self, pos) -> QPointF:
        if not self._img_rect.isValid():
            return QPointF(pos)
        x = min(max(pos.x(), self._img_rect.left()), self._img_rect.right())
        y = min(max(pos.y(), self._img_rect.top()), self._img_rect.bottom())
        return QPointF(x, y)

    def _update_rubber(self, pos) -> None:
        if not self._img_rect.isValid() or self._pm is None:
            return
        aspect = (self._pm.width() / self._pm.height()
                  if self._pm.height() else 1.0)
        end = self._clamp_to_img(pos)
        ax, ay = self._anchor.x(), self._anchor.y()
        width = abs(end.x() - ax)
        max_w = min(self._img_rect.width(), self._img_rect.height() * aspect)
        width = min(width, max_w)
        height = width / aspect if aspect else width
        left = min(ax, end.x())
        left = min(max(left, self._img_rect.left()),
                   self._img_rect.right() - width)
        top = ay if end.y() >= ay else ay - height
        top = min(max(top, self._img_rect.top()),
                  self._img_rect.bottom() - height)
        self._rubber = QRectF(left, top, width, height)

    def _finish_zoom_draw(self) -> None:
        rect = self._rubber
        self._rubber = None
        self._anchor = None
        self._draw_zoom = False
        self.zoom_mode_changed.emit(False)
        if (rect is not None and self._img_rect.isValid()
                and rect.width() >= 0.05 * self._img_rect.width()):
            roi = self._roi or (0.0, 0.0, 1.0, 1.0)
            rx = (rect.left() - self._img_rect.left()) / self._img_rect.width()
            ry = (rect.top() - self._img_rect.top()) / self._img_rect.height()
            rw = rect.width() / self._img_rect.width()
            rh = rect.height() / self._img_rect.height()
            set_view_roi((roi[0] + rx * roi[2], roi[1] + ry * roi[3],
                          rw * roi[2], rh * roi[3]))
            self._roi = view_roi()
            self.roi_changed.emit(self._roi)
        self.update()

    def _set_line(self, x: float) -> None:
        if not self._img_rect.isValid():
            return
        roi = self._roi or (0.0, 0.0, 1.0, 1.0)
        rel = (x - self._img_rect.left()) / self._img_rect.width()
        self.finish_line_x = max(0.0, min(1.0, roi[0] + rel * roi[2]))
        self.update()
