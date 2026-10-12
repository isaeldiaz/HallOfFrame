"""AudioView envelope tests (sheet 12b)."""
from __future__ import annotations

import array
import math

import pytest

pytest.importorskip("PySide6")

from hallofframe.audio import RATE
from hallofframe.ui import styles
from hallofframe.ui.audio_view import AudioView

pytestmark = pytest.mark.qt


def _pcm():
    """1 s silence + 1 s 1 kHz tone (amplitude 10000), like write_tone_wav."""
    samples = array.array("h", [0] * (2 * RATE))
    for i in range(RATE, 2 * RATE):
        samples[i] = int(10000 * math.sin(2 * math.pi * 1000 * i / RATE))
    return samples.tobytes()


def test_set_clip_stores_state(qapp):
    v = AudioView()
    v.set_clip(_pcm(), 2.0, 1.0, [0.4, 1.7])
    assert len(v._env) == AudioView.BINS
    assert v._dur_s == 2.0
    assert v._press_s == 1.0
    assert v._others == [0.4, 1.7]
    assert v._timer.isActive()
    assert isinstance(v.playhead_s(), float)
    assert v.playhead_s() >= 0.0
    v.deleteLater()


def test_clear_stops_playhead(qapp):
    v = AudioView()
    v.set_clip(_pcm(), 2.0, 1.0, [])
    v.clear()
    assert v._env == []
    assert not v._timer.isActive()
    assert v.playhead_s() is None
    v.deleteLater()


def test_press_mark_is_red(qapp):
    v = AudioView()
    v.resize(401, 72)
    v.set_clip(_pcm(), 2.0, 1.0, [])
    v.stop_playhead()
    img = v.grab().toImage()
    assert img.pixelColor(200, 36).name() == styles.RED.lower()
    v.deleteLater()


def test_other_tick_is_amber(qapp):
    v = AudioView()
    v.resize(401, 72)
    v.set_clip(_pcm(), 2.0, 1.0, [0.5])
    v.stop_playhead()
    img = v.grab().toImage()
    assert img.pixelColor(100, 36).name() == styles.AMBER.lower()
    v.deleteLater()


def test_empty_paint_smoke(qapp):
    v = AudioView()
    v.resize(401, 72)
    img = v.grab()
    assert not img.isNull()
    v.deleteLater()
