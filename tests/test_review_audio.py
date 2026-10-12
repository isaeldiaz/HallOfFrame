"""Review audio replay with P (sheet 12, §13.3.4)."""
from __future__ import annotations

import io
import time
import unittest
from unittest import mock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit

from hallofframe.audio import RATE
from hallofframe.controller import CaptureController
from test_audio import rms, write_tone_wav

pytestmark = pytest.mark.qt


@pytest.fixture
def review_audio_env(request, data_root, config, storage, buffer, qapp):
    from hallofframe.ui.main_window import MainWindow

    inst = request.instance
    root = data_root
    (root / "races" / "0001_R").mkdir(parents=True)
    write_tone_wav(root / "races" / "0001_R" / "audio.wav",
                   total_s=5.0, burst=(1.5, 1.7))

    inst.app = qapp
    inst.data_root = root
    inst.config = config(
        voice={"enabled": getattr(inst, "voice_enabled", True),
               "play_before_s": 1.0, "play_after_s": 1.0},
        trigger={"device_path": "", "start_keycodes": [28],
                 "end_keycodes": [88], "crossing_keycodes": [57],
                 "grab_device": False},
        races={"csv_path": str(root / "races.csv")},
    )
    inst.storage = storage
    inst.buffer = buffer
    inst.buffer.health = lambda: (True, 30.0, 0.1)
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer)
    inst.race_id = inst.storage.create_race("R", 0.0, time.time(), "direct",
                                            0.0, 0.0, "screen",
                                            window_before_ms=500,
                                            window_after_ms=500)
    for seq in (1, 2, 3):
        inst.storage.insert_capture(inst.race_id, seq, float(seq),
                                    time.time(), float(seq), 0.0,
                                    target_ms=seq * 1000)
    inst.storage.set_race_audio(inst.race_id, "races/0001_R/audio.wav", 0.5)
    inst.controller.race_id = inst.race_id

    inst.win = MainWindow(inst.config, inst.controller, inst.buffer)
    inst.win.show()
    qapp.processEvents()
    yield
    inst.win.status_timer.stop()
    inst.controller.stop()
    inst.win.close()


@pytest.mark.usefixtures("review_audio_env")
class TestReviewAudio(unittest.TestCase):
    def review(self):
        self.win._open_review()
        self.app.processEvents()
        return self.win._review_screen

    def key(self, key):
        target = self.app.focusWidget() or self.win
        self.app.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
        self.app.processEvents()

    def test_p_plays_clip_around_press(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        play.assert_called_once()
        pcm = play.call_args[0][0]
        assert len(pcm) == 2 * RATE * 2
        assert rms(pcm[32000:38400]) > 5000
        assert rms(pcm[0:28800]) == 0
        assert screen.play_btn.isEnabled()

    def test_no_audio_notifies(self):
        self.storage.set_race_audio(self.race_id, None, None)
        screen = self.review()
        notes = []
        screen.notify.connect(notes.append)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        play.assert_not_called()
        assert notes
        assert not screen.play_btn.isEnabled()

    def test_second_press_kills_previous(self):
        screen = self.review()
        screen._select(2)
        first, second = mock.Mock(), mock.Mock()
        first.poll.return_value = None
        second.poll.return_value = None
        with mock.patch("hallofframe.ui.review_screen.play_clip",
                        side_effect=[first, second]) as play:
            self.key(Qt.Key_P)
            self.key(Qt.Key_P)
        assert play.call_count == 2
        first.kill.assert_called_once()
        second.kill.assert_not_called()

    def test_reconstructed_crossing_notifies(self):
        self.storage.insert_capture(self.race_id, 4, 4.0, time.time(), 4.0, 0.0,
                                    target_ms=4000, t0_reconstructed=1)
        screen = self.review()
        screen._select(4)
        notes = []
        screen.notify.connect(notes.append)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        play.assert_not_called()
        assert notes == ["No audio for crossings recorded after a restart"]

    def test_p_from_bow_field_plays(self):
        screen = self.review()
        screen._select(2)
        screen.list.focus_bow(2)
        self.app.processEvents()
        assert isinstance(self.app.focusWidget(), QLineEdit)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        play.assert_called_once()
        assert self.app.focusWidget().text() == ""

    def test_play_uses_output_device(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        assert play.call_args.kwargs["device"] == "default"

    def test_player_failure_notifies(self):
        screen = self.review()
        screen._select(2)
        notes = []
        screen.notify.connect(notes.append)
        failed = mock.Mock()
        failed.poll.return_value = 1
        failed.returncode = 1
        failed.stdin = None
        failed.stderr = io.BytesIO(b"aplay: main:830: audio open error: No such file\n")
        with mock.patch("hallofframe.ui.review_screen.play_clip", return_value=failed):
            self.key(Qt.Key_P)
        assert screen._play_timer.isActive()
        screen._poll_player()
        assert notes == ["aplay failed: aplay: main:830: audio open error: No such file"]
        assert not screen._play_timer.isActive()
        assert screen._player is None

    def test_play_cap_in_keybar(self):
        self.review()
        assert "Play audio" in self.win.keybar.labels()

    def test_crossing_outside_recording_notifies(self):
        self.storage.set_race_audio(self.race_id, "races/0001_R/audio.wav", 100.0)
        screen = self.review()
        screen._select(2)
        notes = []
        screen.notify.connect(notes.append)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        play.assert_not_called()
        assert notes == ["Crossing is outside the recording"]


@pytest.mark.usefixtures("review_audio_env")
class TestReviewAudioDisabled(unittest.TestCase):
    """[voice] enabled = false: no button, no cap, P is a no-op."""

    voice_enabled = False

    def test_play_hidden_and_inert(self):
        self.win._open_review()
        self.app.processEvents()
        screen = self.win._review_screen
        assert not screen.play_btn.isVisibleTo(self.win)
        assert "Play audio" not in self.win.keybar.labels()
        screen._select(2)
        notes = []
        screen.notify.connect(notes.append)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.app.sendEvent(self.app.focusWidget() or self.win,
                               QKeyEvent(QEvent.KeyPress, Qt.Key_P, Qt.NoModifier))
            self.app.processEvents()
            screen.play_btn.click()
        play.assert_not_called()
        assert notes == []
