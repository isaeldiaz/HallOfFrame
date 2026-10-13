"""ReactionPage trial-loop tests (sheet 22): injected clock/scheduler/stimulus."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal

from fakes import FakeClock, FakeScheduler
from hallofframe.calibration import Calibration, ReactionRun, calibration_path
from hallofframe.ui.reaction_page import ReactionPage

pytestmark = pytest.mark.qt


class FakeStimulus(QObject):
    qt_press = Signal(float)

    def __init__(self, clock):
        super().__init__()
        self._clock = clock
        self.presented = 0
        self.blanks = 0
        self.shown = 0
        self.closed = 0

    def present(self):
        self.presented += 1

    def blank(self):
        self.blanks += 1

    def show_stimulus(self):
        self.shown += 1
        return self._clock()

    def close(self):
        self.closed += 1


class FakeBeep:
    def __init__(self, clock, ok=True):
        self._clock = clock
        self._ok = ok
        self.prepared = 0
        self.fired = 0
        self.closed = 0

    def prepare(self):
        self.prepared += 1
        return self._ok

    def fire(self):
        self.fired += 1
        return self._clock()

    def error(self):
        return None

    def close(self):
        self.closed += 1


def _build(data_root, clock, sched, stim, beep, *, evdev=True):
    return ReactionPage(
        data_root, {"output": "default"}, evdev=evdev, clock=clock,
        schedule=sched,
        run_factory=lambda k: ReactionRun(k, trials=6, rng=lambda a, b: 2.0),
        stimulus_factory=lambda: stim, beep_factory=lambda: beep)


def _tick(clock, sched, seconds):
    clock.advance(seconds)
    sched.advance(seconds)


def test_visual_run_completes_and_saves(qapp, data_root):
    clock, sched = FakeClock(100.0), FakeScheduler()
    stim, beep = FakeStimulus(clock), FakeBeep(clock)
    page = _build(data_root, clock, sched, stim, beep)
    page.start()
    assert stim.presented == 1
    for _ in range(6):
        _tick(clock, sched, 2.0)
        page.on_press(clock() + 0.25)
        _tick(clock, sched, 0.8)
    assert page.run.done is True
    assert "median 250 ms" in page.result.text()
    assert "n=6" in page.result.text()
    assert page.save_btn.isEnabled()
    page._save()
    assert Calibration.load(data_root).reaction_visual_ms == 250.0
    data = json.loads(calibration_path(data_root).read_text())
    assert data["reaction"]["visual"]["press_source"] == "evdev"


def test_false_start_repeats_trial(qapp, data_root):
    clock, sched = FakeClock(100.0), FakeScheduler()
    stim, beep = FakeStimulus(clock), FakeBeep(clock)
    page = _build(data_root, clock, sched, stim, beep)
    page.start()
    _tick(clock, sched, 1.0)
    page.on_press(clock())
    assert page.status.text() == "False start — again"
    assert page.run.false_starts == 1
    assert page.run.samples_ms == []
    _tick(clock, sched, 0.8)
    assert page.run.state == "waiting"


def test_timeout_counts_miss(qapp, data_root):
    clock, sched = FakeClock(100.0), FakeScheduler()
    stim, beep = FakeStimulus(clock), FakeBeep(clock)
    page = _build(data_root, clock, sched, stim, beep)
    page.start()
    _tick(clock, sched, 2.0)
    _tick(clock, sched, 1.5)
    assert page.run.misses == 1
    assert page.status.text() == "Missed — again"


def test_evdev_ignores_qt_keys(qapp, data_root):
    clock, sched = FakeClock(100.0), FakeScheduler()
    stim, beep = FakeStimulus(clock), FakeBeep(clock)
    page = _build(data_root, clock, sched, stim, beep, evdev=True)
    page.start()
    _tick(clock, sched, 2.0)
    stim.qt_press.emit(clock() + 0.3)
    assert page.run.samples_ms == []
    assert page.run.state == "shown"


def test_beep_unavailable_aborts(qapp, data_root):
    clock, sched = FakeClock(100.0), FakeScheduler()
    stim, beep = FakeStimulus(clock), FakeBeep(clock, ok=False)
    page = _build(data_root, clock, sched, stim, beep)
    messages = []
    page.notify.connect(messages.append)
    page.kind_audio.setChecked(True)
    page.start()
    assert messages == ["aplay not available — install alsa-utils"]
    assert page.run is None
