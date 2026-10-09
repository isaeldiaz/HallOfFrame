"""Preview ROI / zoom-draw and finish-line mapping (spec §13.3, package A.3)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from hallofframe.framebuffer import FrameBuffer
from hallofframe.mjpeg import Frame
from hallofframe.ui.images import set_view_roi, view_roi
from hallofframe.ui.preview_widget import PreviewWidget

pytestmark = pytest.mark.qt


@pytest.fixture(autouse=True)
def _reset_view_roi():
    set_view_roi(None)
    yield
    set_view_roi(None)


def _frame_buffer(tmp_path) -> FrameBuffer:
    path = tmp_path / "frame.jpg"
    Image.new("RGB", (1920, 1080), (200, 30, 30)).save(path, "JPEG")
    buf = FrameBuffer(assumed_fps=30)
    buf.append(Frame(0.0, 0.0, 1, path.read_bytes()))
    return buf


def _widget(qapp, tmp_path) -> PreviewWidget:
    w = PreviewWidget(_frame_buffer(tmp_path))
    w.resize(960, 540)
    w.refresh()
    return w


def _event(kind, x, y) -> QMouseEvent:
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def _press(w, x, y):
    w.mousePressEvent(_event(QEvent.Type.MouseButtonPress, x, y))


def _move(w, x, y):
    w.mouseMoveEvent(_event(QEvent.Type.MouseMove, x, y))


def _release(w, x, y):
    w.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, x, y))


def _drag_line(w, x):
    _press(w, x, 270)
    _release(w, x, 270)


def test_draw_zoom_composes_roi(qapp, tmp_path):
    w = _widget(qapp, tmp_path)
    seen = []
    w.roi_changed.connect(seen.append)
    w.begin_zoom_draw()
    assert w.is_zoom_draw()
    _press(w, 240, 135)
    _move(w, 720, 405)
    _release(w, 720, 405)
    assert seen
    assert seen[-1] == pytest.approx((0.25, 0.25, 0.5, 0.5), abs=0.01)
    assert view_roi() == pytest.approx((0.25, 0.25, 0.5, 0.5), abs=0.01)
    assert not w.is_zoom_draw()


def test_small_drag_leaves_mode_without_roi(qapp, tmp_path):
    w = _widget(qapp, tmp_path)
    seen = []
    w.roi_changed.connect(seen.append)
    w.begin_zoom_draw()
    _press(w, 240, 135)
    _move(w, 250, 135)
    _release(w, 250, 135)
    assert seen == []
    assert not w.is_zoom_draw()
    assert view_roi() is None


def test_finish_line_maps_full_frame(qapp, tmp_path):
    w = _widget(qapp, tmp_path)
    _drag_line(w, 480)
    assert w.finish_line_x == pytest.approx(0.5, abs=0.01)
    _drag_line(w, 240)
    assert w.finish_line_x == pytest.approx(0.25, abs=0.01)


def test_finish_line_maps_through_roi(qapp, tmp_path):
    w = _widget(qapp, tmp_path)
    w.set_roi((0.25, 0.25, 0.5, 0.5))
    _drag_line(w, 480)
    assert w.finish_line_x == pytest.approx(0.5, abs=0.01)
    # widget x=240 is a quarter of the way into a ROI whose left edge is 0.25.
    _drag_line(w, 240)
    assert w.finish_line_x == pytest.approx(0.375, abs=0.01)


def test_reset_zoom_clears_and_signals(qapp, tmp_path):
    w = _widget(qapp, tmp_path)
    w.set_roi((0.25, 0.25, 0.5, 0.5))
    seen = []
    w.roi_changed.connect(seen.append)
    w.reset_zoom()
    assert view_roi() is None
    assert seen == [None]


def test_set_roi_redecodes_the_current_frame(qapp, tmp_path):
    # A frozen stream must still pick up the zoom immediately: set_roi has to
    # force a re-decode (refresh early-returns on an unchanged JPEG).
    img = Image.new("RGB", (1920, 1080), (255, 0, 0))
    img.paste(Image.new("RGB", (960, 1080), (0, 0, 255)), (960, 0))
    path = tmp_path / "two.jpg"
    img.save(path, "JPEG")
    buf = FrameBuffer(assumed_fps=30)
    buf.append(Frame(0.0, 0.0, 1, path.read_bytes()))
    w = PreviewWidget(buf)
    w.resize(960, 540)
    w.refresh()
    w.set_roi((0.0, 0.0, 0.5, 1.0))  # left half only
    pm = w._pm
    c = pm.toImage().pixelColor(pm.width() // 2, pm.height() // 2)
    assert c.red() > 200 and c.blue() < 60, f"centre was {c.name()}"


def test_arming_cancels_pending_zoom_draw(config, storage, buffer, controller,
                                          qapp):
    from hallofframe.ui.main_window import MainWindow
    win = MainWindow(config(), controller, buffer)
    win.ready.begin_zoom_draw()
    assert win.ready.preview.is_zoom_draw()
    win.session.arm()
    win._recompute_state()
    # Entering ARMED drops the pending gesture, so the next Esc disarms.
    assert not win.ready.preview.is_zoom_draw()
