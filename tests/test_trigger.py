"""Trigger dispatch tests (Phase 0, step 0.6).

These feed synthetic key events straight into ``TriggerListener._dispatch`` so
no /dev/input device (and no evdev install) is required. The kernel-timestamp
semantics are exercised directly: the timestamp passed in is used verbatim.
"""
from __future__ import annotations

import time

import pytest

from hallofframe.trigger import TriggerError, TriggerListener


def make_listener(debounce_ms: float = 20.0,
                  keycodes: tuple[int, ...] = (57,)) -> TriggerListener:
    listener = TriggerListener.__new__(TriggerListener)
    listener.handlers = {}
    listener.keycodes = set(keycodes)
    listener.debounce_s = debounce_ms / 1000.0
    listener._last_trigger_mono = 0.0
    listener.debounce_suspect_count = 0
    listener.calls = []
    for code in keycodes:
        listener.handlers[code] = (
            lambda ts, c, suspect, _l=listener:
                _l.calls.append((ts, c, suspect))
        )
    return listener


def test_key_up_is_ignored():
    listener = make_listener()
    listener._dispatch(57, 0, 1000.0)
    assert listener.calls == []
    assert listener._last_trigger_mono == 0.0


def test_auto_repeat_is_ignored():
    listener = make_listener()
    listener._dispatch(57, 2, 1000.0)
    assert listener.calls == []
    assert listener._last_trigger_mono == 0.0


def test_second_press_inside_debounce_is_delivered_suspect():
    listener = make_listener(debounce_ms=20.0)
    listener._dispatch(57, 1, 1000.0)
    listener._dispatch(57, 1, 1000.01)  # 10 ms later, inside the window
    assert listener.calls == [(1000.0, 57, False), (1000.01, 57, True)]
    assert listener.debounce_suspect_count == 1


def test_press_after_debounce_is_not_suspect():
    listener = make_listener(debounce_ms=20.0)
    listener._dispatch(57, 1, 1000.0)
    listener._dispatch(57, 1, 1000.05)  # 50 ms later, outside the window
    assert listener.calls == [(1000.0, 57, False), (1000.05, 57, False)]
    assert listener.debounce_suspect_count == 0


def test_unknown_keycode_is_ignored():
    listener = make_listener(keycodes=(57,))
    listener._dispatch(99, 1, 1000.0)
    assert listener.calls == []
    assert listener._last_trigger_mono == 0.0


class _FakeDevice:
    fd = 0

    def __init__(self, event):
        self._event = event

    def read_one(self):
        return self._event


def _clock_check_listener(event, monkeypatch):
    import select
    listener = TriggerListener.__new__(TriggerListener)
    listener._device = _FakeDevice(event)
    monkeypatch.setattr(select, "select", lambda *a, **k: ([0], [], []))
    return listener


def test_clock_domain_mismatch_raises(monkeypatch):
    class Event:
        def timestamp(self):
            return 1000.0  # CLOCK_REALTIME-ish, far from monotonic

    with pytest.raises(TriggerError):
        _clock_check_listener(Event(), monkeypatch)._verify_clock_domain()


def test_clock_domain_match_passes(monkeypatch):
    class Event:
        def timestamp(self):
            return time.monotonic()

    # No exception.
    _clock_check_listener(Event(), monkeypatch)._verify_clock_domain()


def test_timestamp_is_used_verbatim():
    listener = make_listener()
    listener._dispatch(57, 1, 12345.678)
    assert listener.calls == [(12345.678, 57, False)]
    assert listener._last_trigger_mono == 12345.678
