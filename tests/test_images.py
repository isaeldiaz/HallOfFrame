"""``images.load_scaled`` tests (spec §7.2).

The loader is the single decode path for the UI: it reads paths or JPEG bytes,
fits the result to the requested size keeping aspect, and can crop a normalised
``roi`` before scaling (the deferred zoom/ROI hook, §13.3).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from PIL import Image
from PySide6.QtCore import QSize

from hallofframe.framebuffer import FrameBuffer
from hallofframe.ui.images import (load_scaled, load_view, set_view_roi,
                                   view_roi)
from hallofframe.ui.preview_widget import PreviewWidget

pytestmark = pytest.mark.qt


def _solid_jpeg(path, size=(64, 36), colour=(255, 0, 0)):
    Image.new("RGB", size, colour).save(path, "JPEG")
    return str(path)


def test_load_scaled_returns_the_requested_size(tmp_path, qapp):
    path = _solid_jpeg(tmp_path / "solid.jpg")
    pm = load_scaled(path, QSize(32, 18))
    assert pm is not None
    assert (pm.width(), pm.height()) == (32, 18)


def test_load_scaled_accepts_jpeg_bytes(tmp_path, qapp):
    path = _solid_jpeg(tmp_path / "solid.jpg")
    data = (tmp_path / "solid.jpg").read_bytes()
    pm = load_scaled(data, QSize(32, 18))
    assert pm is not None
    assert (pm.width(), pm.height()) == (32, 18)


def test_load_scaled_missing_source_is_none(tmp_path, qapp):
    assert load_scaled(str(tmp_path / "nope.jpg"), QSize(32, 18)) is None
    assert load_scaled(b"", QSize(32, 18)) is None


def test_preview_paints_a_decoded_pixmap(tmp_path, qapp):
    # Regression: load_scaled returns a QPixmap, and the preview used to hand it
    # to QPainter.drawImage (QPixmap-only drawPixmap). That raised a TypeError in
    # paintEvent on every frame, so the live preview never painted.
    path = _solid_jpeg(tmp_path / "solid.jpg")
    pm = load_scaled(path, QSize(32, 18))
    widget = PreviewWidget(FrameBuffer(assumed_fps=30))
    widget.resize(120, 80)
    widget._pm = pm
    rendered = widget.grab()  # forces paintEvent; would raise on the old code
    assert not rendered.isNull()


def test_roi_crops_to_the_expected_colour(tmp_path, qapp):
    # Left half red, right half blue — a crop of either half is one colour.
    img = Image.new("RGB", (64, 36), (255, 0, 0))
    img.paste(Image.new("RGB", (32, 36), (0, 0, 255)), (32, 0))
    path = str(tmp_path / "two.jpg")
    img.save(path, "JPEG")

    left = load_scaled(path, QSize(64, 64), roi=(0.0, 0.0, 0.5, 1.0))
    right = load_scaled(path, QSize(64, 64), roi=(0.5, 0.0, 0.5, 1.0))
    assert left is not None and right is not None

    lc = left.toImage().pixelColor(left.width() // 2, left.height() // 2)
    rc = right.toImage().pixelColor(right.width() // 2, right.height() // 2)
    assert lc.red() > 200 and lc.blue() < 60, f"left crop was {lc.name()}"
    assert rc.blue() > 200 and rc.red() < 60, f"right crop was {rc.name()}"


# --- view ROI (spec §13.3, package A.2) -----------------------------------
@pytest.fixture(autouse=True)
def _reset_view_roi():
    set_view_roi(None)
    yield
    set_view_roi(None)


def _two_colour_jpeg(path):
    img = Image.new("RGB", (128, 72), (255, 0, 0))
    img.paste(Image.new("RGB", (64, 72), (0, 0, 255)), (64, 0))
    img.save(str(path), "JPEG")
    return str(path)


def test_dct_crop_right_half_is_blue(tmp_path, qapp):
    path = _two_colour_jpeg(tmp_path / "two.jpg")
    pm = load_scaled(path, QSize(64, 72), roi=(0.5, 0.0, 0.5, 1.0))
    assert pm is not None
    assert pm.height() == 72
    c = pm.toImage().pixelColor(pm.width() // 2, pm.height() // 2)
    assert c.blue() > 200 and c.red() < 60, f"crop was {c.name()}"


def test_dct_crop_left_half_is_red(tmp_path, qapp):
    path = _two_colour_jpeg(tmp_path / "two.jpg")
    pm = load_scaled(path, QSize(64, 72), roi=(0.0, 0.0, 0.5, 1.0))
    assert pm is not None
    c = pm.toImage().pixelColor(pm.width() // 2, pm.height() // 2)
    assert c.red() > 200 and c.blue() < 60, f"crop was {c.name()}"


def test_load_view_full_frame_matches_load_scaled(tmp_path, qapp):
    path = _solid_jpeg(tmp_path / "solid.jpg")
    set_view_roi(None)
    a = load_view(path, QSize(32, 18))
    b = load_scaled(path, QSize(32, 18))
    assert a is not None and b is not None
    assert a.toImage() == b.toImage()


def test_set_view_roi_clamps_out_of_bounds():
    set_view_roi((0.9, 0.9, 0.5, 0.5))
    assert view_roi() == pytest.approx((0.5, 0.5, 0.5, 0.5))
    set_view_roi((0.0, 0.0, 0.001, 0.001))
    assert view_roi() == pytest.approx((0.0, 0.0, 0.05, 0.05))
    set_view_roi(None)
    assert view_roi() is None
