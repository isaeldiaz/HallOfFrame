"""Session state machine and derived UI state (plan Phase 3, steps 3.1/3.6).

Two things are tested here:

* ``Session`` transitions: every method from every phase (a 5x8 table); legal
  ones move the phase, illegal ones raise ``SessionError``.
* ``derive_state`` precedence, ported from the old ``test_state.py``.
"""
from __future__ import annotations

import pytest

from hallofframe.session import (KEYMAP, Phase, Session, SessionError,
                                 derive_state)
from hallofframe.ui.state import AppState

RACE = 42


def _session(phase: Phase, **kw) -> Session:
    return Session(phase=phase, **kw)


# ------------------------------------------------------------------ derive_state
class TestDeriveState:
    def state(self, phase=Phase.IDLE, alive=True, cal_ok=True):
        return derive_state(_session(phase), alive, cal_ok)

    def test_recording_wins_over_everything(self):
        # A stream drop mid-race must NOT knock the UI out of RECORDING.
        assert self.state(Phase.RECORDING, alive=False) is AppState.RECORDING
        assert self.state(Phase.RECORDING, alive=False,
                          cal_ok=False) is AppState.RECORDING

    def test_armed_beats_stream_down(self):
        assert self.state(Phase.ARMED, alive=False) is AppState.ARMED

    def test_stream_down_beats_recalibrate(self):
        assert self.state(Phase.IDLE, alive=False, cal_ok=False) is AppState.STREAM_DOWN

    def test_stream_down_beats_ready(self):
        assert self.state(Phase.IDLE, alive=False) is AppState.STREAM_DOWN

    def test_review_beats_stream_down(self):
        # Timing-only races run with the stream down; their crossings must
        # still be reviewable (bow numbers), so REVIEW outranks STREAM_DOWN.
        assert self.state(Phase.REVIEW, alive=False) is AppState.REVIEW

    def test_race_over_beats_stream_down(self):
        # A finished timing-only race must reach RACE_OVER (Review/Export/Next)
        # even while the stream is down.
        assert self.state(Phase.RACE_OVER, alive=False) is AppState.RACE_OVER

    def test_review_beats_race_over(self):
        # REVIEW and RACE_OVER are distinct phases; REVIEW is the explicit one.
        assert self.state(Phase.REVIEW) is AppState.REVIEW
        assert self.state(Phase.RACE_OVER) is AppState.RACE_OVER

    def test_race_over_beats_ready(self):
        assert self.state(Phase.RACE_OVER) is AppState.RACE_OVER

    def test_default_ready(self):
        assert self.state() is AppState.READY


# ------------------------------------------------------------------ transitions
_METHODS = ("arm", "disarm", "race_started", "race_ended", "load_race",
            "open_review", "close_review", "dismiss")
_ARGS = {"race_started": RACE, "race_ended": RACE, "load_race": RACE,
         "open_review": RACE}
_LEGAL = {
    ("arm", Phase.IDLE): Phase.ARMED,
    ("arm", Phase.RACE_OVER): Phase.ARMED,
    ("disarm", Phase.ARMED): Phase.IDLE,
    ("race_started", Phase.ARMED): Phase.RECORDING,
    ("race_ended", Phase.RECORDING): Phase.RACE_OVER,
    ("load_race", Phase.IDLE): Phase.RACE_OVER,
    ("open_review", Phase.RACE_OVER): Phase.REVIEW,
    ("open_review", Phase.IDLE): Phase.REVIEW,
    ("close_review", Phase.REVIEW): Phase.RACE_OVER,
    ("dismiss", Phase.RACE_OVER): Phase.IDLE,
}


def _invoke(session: Session, method: str):
    arg = _ARGS.get(method)
    return getattr(session, method)() if arg is None else getattr(session, method)(arg)


def test_transition_table_covers_every_method_and_phase():
    assert set(_METHODS) == {
        "arm", "disarm", "race_started", "race_ended", "load_race",
        "open_review", "close_review", "dismiss"}
    assert set(Phase) == {Phase.IDLE, Phase.ARMED, Phase.RECORDING,
                          Phase.RACE_OVER, Phase.REVIEW}
    # Every method has at least one legal source phase.
    assert {m for m, _ in _LEGAL} == set(_METHODS)


def test_every_transition_from_every_phase():
    for phase in Phase:
        for method in _METHODS:
            session = Session(phase=phase, return_to=Phase.RACE_OVER)
            legal = (method, phase) in _LEGAL
            if legal:
                _invoke(session, method)
                assert session.phase is _LEGAL[(method, phase)], \
                    f"{method} from {phase}"
            else:
                with pytest.raises(SessionError) as exc:
                    _invoke(session, method)
                assert str(exc.value), f"{method} from {phase} needs a message"


def test_focused_race_id_set_on_race_lifecycle():
    s = Session()
    s.arm()
    s.race_started(RACE)
    assert s.focused_race_id == RACE
    s.race_ended(RACE + 1)
    assert s.focused_race_id == RACE + 1
    assert s.phase is Phase.RACE_OVER


def test_load_race_focuses_and_shows_race_over():
    s = Session()
    s.load_race(RACE)
    assert s.phase is Phase.RACE_OVER
    assert s.focused_race_id == RACE


def test_open_review_from_race_over_returns_to_race_over():
    s = Session()
    s.arm()
    s.race_started(RACE)
    s.race_ended(RACE)
    s.open_review(RACE)
    assert s.phase is Phase.REVIEW
    assert s.focused_race_id == RACE
    assert s.return_to is Phase.RACE_OVER
    s.close_review()
    assert s.phase is Phase.RACE_OVER


def test_open_review_from_idle_returns_to_idle():
    s = Session()
    s.open_review(RACE)
    assert s.phase is Phase.REVIEW
    assert s.return_to is Phase.IDLE
    s.close_review()
    assert s.phase is Phase.IDLE


def test_dismiss_returns_to_idle():
    s = Session(phase=Phase.RACE_OVER, focused_race_id=RACE)
    s.dismiss()
    assert s.phase is Phase.IDLE


def test_arm_from_race_over_allowed():
    s = Session(phase=Phase.RACE_OVER, focused_race_id=RACE)
    s.arm()
    assert s.phase is Phase.ARMED


def test_disarm_while_armed():
    s = Session(phase=Phase.ARMED)
    s.disarm()
    assert s.phase is Phase.IDLE


# ------------------------------------------------------------------ KEYMAP
def test_keymap_covers_every_state():
    assert set(KEYMAP) == set(AppState)


def test_keymap_actions_are_real_mainwindow_methods():
    pytest.importorskip("PySide6")
    from hallofframe.ui.main_window import MainWindow
    for state, keys in KEYMAP.items():
        for k in keys:
            assert hasattr(MainWindow, k.action), \
                f"{state.name}: {k.key} -> missing MainWindow.{k.action}"


def test_keymap_has_no_duplicate_key_within_a_state():
    for state, keys in KEYMAP.items():
        seen = set()
        for k in keys:
            assert k.key not in seen, f"{state.name}: duplicate key {k.key!r}"
            seen.add(k.key)


def test_keymap_shortcut_keys_map_to_one_action_everywhere():
    actions: dict[str, str] = {}
    for keys in KEYMAP.values():
        for k in keys:
            if not k.shortcut:
                continue
            assert actions.setdefault(k.key, k.action) == k.action, \
                f"{k.key!r} maps to both {actions[k.key]!r} and {k.action!r}"


def test_review_keeps_race_keys_out_of_the_shortcut_map():
    # Enter/Space belong to the focused bow field in REVIEW, never a shortcut.
    review_keys = {k.key for k in KEYMAP[AppState.REVIEW] if k.shortcut}
    assert "Return" not in review_keys
    assert "Enter" not in review_keys
    assert "Space" not in review_keys
