"""Unit tests for the explicit state machine (REDESIGN-PLAN §1)."""
import unittest

from hallofframe.ui.state import AppState, derive_state


class _Buf:
    def __init__(self, alive):
        self._alive = alive

    def health(self):
        return self._alive, 30.0 if self._alive else 0.0, 0.2


class _Ctl:
    def __init__(self, running=False):
        self.running = running


class TestDeriveState(unittest.TestCase):
    def state(self, running=False, alive=True, cal_ok=True, armed=False,
              reviewing=False, race_over=False):
        return derive_state(_Ctl(running), _Buf(alive), cal_ok, armed,
                            reviewing, race_over)

    def test_recording_wins_over_everything(self):
        # A stream drop mid-race must NOT knock the UI out of RECORDING (§1).
        self.assertIs(self.state(running=True, alive=False), AppState.RECORDING)
        self.assertIs(self.state(running=True, alive=False, cal_ok=False,
                                 armed=True, reviewing=True), AppState.RECORDING)

    def test_armed_beats_stream_down(self):
        self.assertIs(self.state(armed=True, alive=False), AppState.ARMED)

    def test_stream_down_beats_recalibrate(self):
        self.assertIs(self.state(alive=False, cal_ok=False), AppState.STREAM_DOWN)

    def test_recalibrate_beats_review_and_race_over(self):
        self.assertIs(self.state(cal_ok=False, reviewing=True, race_over=True),
                      AppState.RECALIBRATE)

    def test_review_beats_race_over(self):
        self.assertIs(self.state(reviewing=True, race_over=True), AppState.REVIEW)

    def test_race_over_beats_ready(self):
        self.assertIs(self.state(race_over=True), AppState.RACE_OVER)

    def test_default_ready(self):
        self.assertIs(self.state(), AppState.READY)


if __name__ == "__main__":
    unittest.main()
