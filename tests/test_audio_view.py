"""AudioView envelope tests (sheet 12b)."""
from __future__ import annotations

import array
import math

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

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


def test_set_waveform_and_marks_store_state(qapp):
    v = AudioView()
    v.set_waveform(_pcm(), 2.0)
    assert not v._timer.isActive()          # no playhead until playback starts
    v.set_marks(1.0, [0.4, 1.7])
    assert len(v._env) == AudioView.BINS
    assert v._dur_s == 2.0
    assert v._press_s == 1.0
    assert v._others == [0.4, 1.7]
    v.set_play_start(0.0)
    assert v._timer.isActive()
    assert isinstance(v.playhead_s(), float)
    assert v.playhead_s() >= 0.0
    v.deleteLater()


def test_set_press_moves_mark_only(qapp):
    v = AudioView()
    v.set_waveform(_pcm(), 2.0)
    v.set_marks(0.5, [1.0, 1.5])
    v.set_play_start(0.5)
    env = list(v._env)
    play_t0 = v._play_t0
    v.set_press(1.25)
    assert v._press_s == 1.25
    assert v._others == [1.0, 1.5]
    assert v._env == env
    assert v._timer.isActive()          # a running playhead is left alone
    assert v._play_t0 == play_t0
    v.deleteLater()


def test_clear_stops_playhead(qapp):
    v = AudioView()
    v.set_waveform(_pcm(), 2.0)
    v.set_marks(1.0, [])
    v.set_play_start(0.0)
    v.clear()
    assert v._env == []
    assert not v._timer.isActive()
    assert v.playhead_s() is None
    v.deleteLater()


def test_press_mark_is_red(qapp):
    v = AudioView()
    v.resize(401, 72)
    v.set_waveform(_pcm(), 2.0)
    v.set_marks(1.0, [])
    v.stop_playhead()
    img = v.grab().toImage()
    assert img.pixelColor(200, 36).name() == styles.RED.lower()
    v.deleteLater()


def test_other_tick_is_amber(qapp):
    v = AudioView()
    v.resize(401, 72)
    v.set_waveform(_pcm(), 2.0)
    v.set_marks(1.0, [0.5])
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


def test_click_emits_seek_at_position(qapp):
    v = AudioView()
    v.resize(401, 72)
    v.set_waveform(_pcm(), 2.0)
    v.set_marks(1.0, [])
    v.stop_playhead()
    seen = []
    v.seek_requested.connect(seen.append)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 36),
                     QPointF(200, 36), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    v.mousePressEvent(ev)
    assert seen == [pytest.approx(1.0, abs=1e-6)]
    v.deleteLater()


def test_click_on_empty_emits_nothing(qapp):
    v = AudioView()
    v.resize(401, 72)
    seen = []
    v.seek_requested.connect(seen.append)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 36),
                     QPointF(200, 36), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    v.mousePressEvent(ev)
    assert seen == []
    v.deleteLater()


def test_right_click_emits_nothing(qapp):
    v = AudioView()
    v.resize(401, 72)
    v.set_waveform(_pcm(), 2.0)
    v.set_marks(1.0, [])
    v.stop_playhead()
    seen = []
    v.seek_requested.connect(seen.append)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 36),
                     QPointF(200, 36), Qt.RightButton, Qt.RightButton, Qt.NoModifier)
    v.mousePressEvent(ev)
    assert seen == []
    v.deleteLater()


def test_set_play_start_offsets_playhead(qapp):
    v = AudioView()
    v.set_waveform(_pcm(), 2.0)
    v.set_play_start(1.5)
    assert v.playhead_s() == pytest.approx(1.5, abs=0.05)
    v.deleteLater()
