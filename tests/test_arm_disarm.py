"""Unit tests for trigger building, grab management, and arm/disarm lifecycle."""
import time
import unittest
from unittest import mock

import pytest

from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer, Frame
from hallofframe.main import build_trigger
from hallofframe.roster import recorded_keys
from hallofframe.session import Phase
from hallofframe.storage import Storage
from hallofframe.ui.main_window import MainWindow
from hallofframe.ui.state import AppState

pytestmark = pytest.mark.qt


def _seed_buffer(buffer: FrameBuffer, n=5):
    now = time.monotonic()
    for i in range(n):
        t = now - (n - 1 - i) * 0.033
        buffer.append(Frame(t, t, i + 1, b"\xff\xd8fake\xff\xd9"))


@pytest.fixture
def arm_env(request, data_root, config, storage, buffer, qapp):
    inst = request.instance
    inst.data_root = data_root
    inst.config_factory = config
    inst.config = config(trigger={"device_path": ""})
    inst.storage = storage
    inst.buffer = buffer
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer)
    inst.win = MainWindow(inst.config, inst.controller, inst.buffer)
    yield
    inst.controller.stop()
    inst.win.close()


@pytest.mark.usefixtures("arm_env")
class TestArmDisarm(unittest.TestCase):
    def test_arm_and_disarm_via_f12(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.assertEqual(self.win._last_state, AppState.READY)

        # Arm the race
        self.win._arm_start()
        self.assertEqual(self.win._last_state, AppState.ARMED)
        self.assertIs(self.win.session.phase, Phase.ARMED)

        # F12 disarms
        self.win.on_evdev_end(time.monotonic(), code=88)
        self.assertIs(self.win.session.phase, Phase.IDLE)
        self.assertEqual(self.win._last_state, AppState.READY)

    def test_arm_and_disarm_via_esc(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.assertEqual(self.win._last_state, AppState.READY)

        # Arm the race
        self.win._arm_start()
        self.assertEqual(self.win._last_state, AppState.ARMED)
        self.assertIs(self.win.session.phase, Phase.ARMED)

        # Esc disarms (via evdev code 1 or via _esc)
        self.win.on_evdev_end(time.monotonic(), code=1)
        self.assertIs(self.win.session.phase, Phase.IDLE)
        self.assertEqual(self.win._last_state, AppState.READY)

    def test_esc_during_recording_does_not_stop_race(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.win._arm_start()
        self.win.on_evdev_start(1000.0)
        self.assertEqual(self.win._last_state, AppState.RECORDING)
        self.assertTrue(self.controller.running)

        # Esc (code 1) should be ignored during recording
        self.win.on_evdev_end(1001.0, code=1)
        self.assertTrue(self.controller.running)
        self.assertEqual(self.win._last_state, AppState.RECORDING)

        # F12 (code 88) ends the race
        self.win.on_evdev_end(1002.0, code=88)
        self.assertFalse(self.controller.running)
        self.assertEqual(self.win._last_state, AppState.RACE_OVER)

    def test_calibrate_blocked_while_armed_and_recording(self):
        # No full-screen/modal calibration UI while armed or recording (§7.5).
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.win._arm_start()
        with mock.patch("hallofframe.ui.main_window.CalibrationDialog") as dlg:
            self.win._calibrate()
            dlg.assert_not_called()

        self.win.on_evdev_start(1000.0)
        self.assertEqual(self.win._last_state, AppState.RECORDING)
        with mock.patch("hallofframe.ui.main_window.CalibrationDialog") as dlg:
            self.win._calibrate()
            dlg.assert_not_called()

    def test_qt_fallback_disabled_when_evdev_active(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.win.evdev_active = True
        with mock.patch.object(self.win, "on_evdev_start") as start, \
                mock.patch.object(self.controller, "record_crossing") as rec:
            self.win._start_key()
            self.win._crossing_key()
            self.win._armed_start_clicked()
            start.assert_not_called()
            rec.assert_not_called()

        # Without evdev the Qt fallback must work (degraded precision).
        self.win.evdev_active = False
        with mock.patch.object(self.win, "on_evdev_start") as start:
            self.win._start_key()
            start.assert_called_once()

    def test_resume_is_refused_while_a_race_is_running(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.win._arm_start()
        self.win.on_evdev_start(1000.0)
        self.assertEqual(self.win._last_state, AppState.RECORDING)
        running_id = self.controller.race_id
        # A lingering Resume banner must not hijack the race now on screen.
        self.win._resume_race(running_id + 999)
        self.assertEqual(self.controller.race_id, running_id)
        self.assertEqual(self.win._last_state, AppState.RECORDING)

    def test_roster_banner_excludes_provisional_race(self):
        p = self.data_root / "races.csv"
        p.write_text("race_no,heat_no,name,source,status\n"
                     "0102,1,First,sheet,\n"
                     "102,1,Second,sheet,\n"
                     "103,1,Heat,sheet,\n", encoding="utf-8")
        cfg = self.config_factory(races={"csv_path": str(p)})
        storage = Storage(self.data_root)
        buffer = FrameBuffer(assumed_fps=30)
        ctl = CaptureController(cfg, storage, buffer)
        # A provisional/unlisted race keys on its timestamp name.
        ctl.start_race(1000.0, name="Race-20260829-134812",
                       race_no=None, heat_no=None)
        win = MainWindow(cfg, ctl, buffer)
        try:
            # The provisional (name-keyed) race must NOT fire the blue
            # "recorded, not in roster" banner.
            win.roster_view.render_banner(win.roster.result, recorded_keys(storage))
            self.assertEqual(win.banner_host.lay.count(), 1)  # amber dup only
        finally:
            ctl.stop()
            storage.close()
            win.close()

    def test_missing_roster_defaults_to_000_heat_1(self):
        p = self.data_root / "does-not-exist.csv"
        cfg = self.config_factory(races={"csv_path": str(p)})
        storage = Storage(self.data_root)
        ctl = CaptureController(cfg, storage, self.buffer)
        win = MainWindow(cfg, ctl, self.buffer)
        try:
            win.roster_view.load()
            self.assertTrue(win.roster.result.missing)
            race, is_unlisted = win.ready.current_selection()
            self.assertFalse(is_unlisted)
            self.assertEqual(race.race_no, "000")
            self.assertEqual(race.heat_no, "1")
        finally:
            ctl.stop()
            storage.close()
            win.close()

    def test_build_trigger_fallback_on_invalid_device(self):
        cfg = self.config_factory(
            trigger={"device_path": "/dev/input/nonexistent_device_xyz"})
        listener, fallback, extras = build_trigger(cfg, lambda *a: None, lambda *a: None, lambda *a: None)
        self.assertIsNone(listener)
        self.assertTrue(fallback)
        self.assertEqual(extras, [])


if __name__ == "__main__":
    unittest.main()
