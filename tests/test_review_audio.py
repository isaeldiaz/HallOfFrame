"""Review audio replay with P (sheet 12, §13.3.4)."""
from __future__ import annotations

import io
import time
import unittest
from unittest import mock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QLineEdit

from hallofframe.audio import RATE, read_clip
from hallofframe.controller import CaptureController
from hallofframe.ui.audio_view import AudioView
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
               "play_before_s": 1.0, "play_after_s": 1.0,
               "gain_db": 6.0206, "highpass_hz": 0.0, "press_mute_ms": 100},
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

    def test_no_audio_leaves_view_empty(self):
        self.storage.set_race_audio(self.race_id, None, None)
        screen = self.review()
        assert screen.audio_view._env == []
        assert screen._span_start is None

    def test_load_captures_does_not_reread_audio(self):
        screen = self.review()
        assert screen.audio_view._env != []
        with mock.patch("hallofframe.ui.review_screen.read_clip",
                        wraps=read_clip) as rc:
            screen.load_captures()
        rc.assert_not_called()
        assert screen.audio_view._env != []

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

    def test_p_processes_clip_with_config(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.process_clip",
                        side_effect=lambda pcm, **kw: pcm) as proc, \
                mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        proc.assert_called_once()
        kw = proc.call_args.kwargs
        assert kw["gain_db"] == 6.0206
        assert kw["highpass_hz"] == 0.0
        assert kw["mute"] == pytest.approx((0.95, 1.05), abs=1e-6)
        assert play.call_args[0][0] is proc.call_args[0][0]

    def test_p_mutes_the_press(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        pcm = play.call_args[0][0]
        assert rms(pcm[30400:33600]) == 0
        assert rms(pcm[33600:38400]) > 10000
        assert rms(pcm[0:28800]) == 0

    def test_seek_replays_from_clicked_point(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            self.key(Qt.Key_P)
        clip = screen._clip
        assert clip is not None
        assert len(play.call_args[0][0]) == 2 * RATE * 2
        # Clicking the red press mark (span 1.5, press 1.0 s into the clip)
        # replays from the press, not from the start of the clip.
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            screen._seek_audio(1.5)
        play.assert_called_once()
        pcm = play.call_args[0][0]
        assert len(pcm) == RATE * 2
        assert pcm == clip["pcm"][RATE * 2:]
        assert screen.audio_view._play_offset == pytest.approx(1.5)
        assert screen.audio_view._timer.isActive()

    def test_waveform_click_triggers_seek(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip"):
            self.key(Qt.Key_P)
        view = screen.audio_view
        view.resize(401, 72)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 36),
                             QPointF(200, 36), Qt.LeftButton, Qt.LeftButton,
                             Qt.NoModifier)
            view.mousePressEvent(ev)
        play.assert_called_once()
        # x=200 on a 401 px view → span 0.5 * 3.5 = 1.75 s; the seq-2 clip
        # starts at WAV 0.5 s with 1.0 s left after 1.25 s in.
        assert len(play.call_args[0][0]) == int(0.75 * RATE) * 2

    def test_seek_without_prior_play_builds_free_clip(self):
        screen = self.review()
        screen._select(2)
        assert screen._clip is None
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            screen._seek_audio(0.5)
        play.assert_called_once()
        assert len(play.call_args[0][0]) == 2 * RATE * 2

    def test_seek_outside_cached_clip_builds_free_clip(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip"):
            self.key(Qt.Key_P)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            screen._seek_audio(3.0)   # WAV 3.0, after the seq-2 clip (0.5–2.5)
        play.assert_called_once()
        assert len(play.call_args[0][0]) == 2 * RATE * 2

    def test_selection_resets_cached_clip(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip"):
            self.key(Qt.Key_P)
        assert screen._clip is not None
        screen._select(1)
        assert screen._clip is None

    def test_play_from_end_does_not_spawn_player(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip"):
            self.key(Qt.Key_P)
        with mock.patch("hallofframe.ui.review_screen.play_clip") as play:
            screen._play_from(screen._clip["dur_s"])
        play.assert_not_called()

    def test_selecting_stops_playback(self):
        screen = self.review()
        screen._select(2)
        player = mock.Mock()
        player.poll.return_value = None
        with mock.patch("hallofframe.ui.review_screen.play_clip",
                        return_value=player):
            self.key(Qt.Key_P)
        screen._select(1)
        player.kill.assert_called_once()

    def test_unmapped_key_stops_playback(self):
        screen = self.review()
        screen._select(2)
        player = mock.Mock()
        player.poll.return_value = None
        with mock.patch("hallofframe.ui.review_screen.play_clip",
                        return_value=player):
            self.key(Qt.Key_P)
        assert screen.audio_view._timer.isActive()
        assert screen.audio_view.playhead_s() is not None
        # Q has no review action: it must still stop playback without moving.
        self.key(Qt.Key_Q)
        player.kill.assert_called_once()
        assert screen._selected_seq == 2
        assert screen.audio_view.playhead_s() is None

    def test_navigation_stops_but_still_moves(self):
        screen = self.review()
        screen._select(2)
        player = mock.Mock()
        player.poll.return_value = None
        with mock.patch("hallofframe.ui.review_screen.play_clip",
                        return_value=player):
            self.key(Qt.Key_P)
        self.key(Qt.Key_Down)
        player.kill.assert_called_once()
        assert screen._selected_seq == 3

    def test_typing_in_bow_field_stops_playback(self):
        screen = self.review()
        screen._select(2)
        screen.list.focus_bow(2)
        self.app.processEvents()
        player = mock.Mock()
        player.poll.return_value = None
        with mock.patch("hallofframe.ui.review_screen.play_clip",
                        return_value=player):
            self.key(Qt.Key_P)
        self.app.sendEvent(self.app.focusWidget(),
                           QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier, "1"))
        self.app.processEvents()
        player.kill.assert_called_once()

    def test_event_filter_lifecycle(self):
        screen = self.review()
        assert screen._filter_installed
        self.win._close_review()
        self.app.processEvents()
        assert not screen._filter_installed

    def test_key_after_hide_does_not_stop_playback(self):
        screen = self.review()
        self.win._close_review()
        self.app.processEvents()
        with mock.patch.object(screen, "stop_playback") as stop:
            self.app.sendEvent(self.win, QKeyEvent(QEvent.KeyPress, Qt.Key_Q,
                                                   Qt.NoModifier))
            self.app.processEvents()
        stop.assert_not_called()

    def test_hide_stops_playback(self):
        screen = self.review()
        screen._select(2)
        player = mock.Mock()
        player.poll.return_value = None
        with mock.patch("hallofframe.ui.review_screen.play_clip",
                        return_value=player):
            self.key(Qt.Key_P)
        self.win._close_review()
        self.app.processEvents()
        player.kill.assert_called_once()


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


@pytest.mark.usefixtures("review_audio_env")
class TestReviewAudioView(unittest.TestCase):
    """The waveform spans the first→last crossing and only its red mark moves."""

    def review(self):
        self.win._open_review()
        self.app.processEvents()
        return self.win._review_screen

    def key(self, key):
        target = self.app.focusWidget() or self.win
        self.app.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
        self.app.processEvents()

    def test_waveform_loads_without_play(self):
        # The envelope is up as soon as REVIEW opens, before any Play.
        screen = self.review()
        view = screen.audio_view
        presses = [1.0 - 0.5, 2.0 - 0.5, 3.0 - 0.5]  # WAV seconds
        span = (max(0.0, min(presses) - 1.0), min(5.0, max(presses) + 1.0))
        assert view._dur_s == pytest.approx(span[1] - span[0])
        assert len(view._env) == AudioView.BINS

    def test_marks_track_the_selection(self):
        screen = self.review()
        view = screen.audio_view
        screen._select(2)
        assert view._press_s == pytest.approx(1.5)
        # Every crossing is an amber tick; the selected one is also red on top.
        assert sorted(view._others) == [pytest.approx(0.5), pytest.approx(1.5),
                                        pytest.approx(2.5)]

    def test_select_keeps_waveform_moves_mark(self):
        screen = self.review()
        screen._select(2)
        view = screen.audio_view
        env, dur, press, others = list(view._env), view._dur_s, view._press_s, list(view._others)
        screen._select(1)
        assert view._env == env
        assert view._dur_s == dur
        assert view._others == others     # the ticks never change
        assert view._press_s == pytest.approx(0.5)
        assert view._press_s != press

    def test_view_shown_even_without_aplay(self):
        screen = self.review()
        screen._select(2)
        env = list(screen.audio_view._env)
        notes = []
        screen.notify.connect(notes.append)
        with mock.patch("hallofframe.ui.review_screen.play_clip", return_value=None):
            self.key(Qt.Key_P)
        assert notes
        # The waveform was populated at load and P's failure left it untouched.
        assert screen.audio_view._env == env
        assert screen.audio_view._env != []

    def test_waveform_reflects_audio_energy(self):
        screen = self.review()
        env = screen.audio_view._env
        assert max(env) == pytest.approx(1.0)          # the burst normalises to 1
        assert min(env) == 0.0                          # the silence stays flat
        # The burst (WAV 1.5–1.7) is the loudest region of the span.
        lo = int(1.5 / 3.5 * AudioView.BINS) - 2
        hi = int(1.7 / 3.5 * AudioView.BINS) + 2
        assert lo <= env.index(max(env)) <= hi

    def test_deleting_crossing_keeps_span(self):
        screen = self.review()
        view = screen.audio_view
        env, start, dur = list(view._env), screen._span_start, screen._span_dur
        screen._delete(2)
        assert view._env == env
        assert screen._span_start == start
        assert screen._span_dur == dur
        assert len(view._others) == 3

    def test_play_positions_playhead_in_span(self):
        screen = self.review()
        screen._select(2)
        with mock.patch("hallofframe.ui.review_screen.play_clip"):
            self.key(Qt.Key_P)
        clip = screen._clip
        assert screen.audio_view._play_offset == pytest.approx(
            clip["start_wav"] - screen._span_start)
