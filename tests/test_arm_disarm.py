"""Unit tests for trigger building, grab management, and arm/disarm lifecycle."""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from hallofframe.config import Config
from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer, Frame
from hallofframe.main import build_trigger
from hallofframe.storage import Storage
from hallofframe.ui.main_window import MainWindow
from hallofframe.ui.state import AppState

try:
    _QT = QApplication.instance() is not None or bool(QApplication([]))
except Exception:  # pragma: no cover
    _QT = False


def _make_config(data_root, device_path="/dev/input/event3"):
    data = {
        "paths": {"data_root": str(data_root)},
        "stream": {"assumed_fps": 30, "buffer_seconds": 10.0},
        "timing": {"viewing_mode": "screen", "reaction_offset_ms": 0.0,
                   "debounce_ms": 20, "start_mode": "direct", "radio_delay_ms": 0.0},
        "capture": {"window_before_ms": 50, "window_after_ms": 50},
        "archive": {"enabled": False, "every_nth_frame": 1},
        "trigger": {
            "device_path": device_path,
            "crossing_keycodes": [57],
            "start_keycodes": [28],
            "end_keycodes": [88],
            "grab_device": True,
        },
        "ui": {"finish_line_x": 0.5, "preview_fps": 10},
    }
    return Config(data=data, path=Path(data_root) / "config.toml")


def _seed_buffer(buffer: FrameBuffer, n=5):
    now = time.monotonic()
    for i in range(n):
        t = now - (n - 1 - i) * 0.033
        buffer.append(Frame(t, t, i + 1, b"\xff\xd8fake\xff\xd9"))


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestArmDisarm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.config = _make_config(self.data_root, device_path="")
        self.storage = Storage(self.data_root)
        self.buffer = FrameBuffer(assumed_fps=30)
        self.controller = CaptureController(self.config, self.storage, self.buffer)
        self.win = MainWindow(self.config, self.controller, self.buffer)

    def tearDown(self):
        self.controller.stop()
        self.storage.close()
        self.win.close()
        self.tmp.cleanup()

    def test_arm_and_disarm_via_f12(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.assertEqual(self.win._last_state, AppState.READY)

        # Arm the race
        self.win._arm_start()
        self.assertEqual(self.win._last_state, AppState.ARMED)
        self.assertTrue(self.win._armed)

        # F12 disarms
        self.win.on_evdev_end(time.monotonic(), code=88)
        self.assertFalse(self.win._armed)
        self.assertEqual(self.win._last_state, AppState.READY)

    def test_arm_and_disarm_via_esc(self):
        _seed_buffer(self.buffer)
        self.win._recompute_state()
        self.assertEqual(self.win._last_state, AppState.READY)

        # Arm the race
        self.win._arm_start()
        self.assertEqual(self.win._last_state, AppState.ARMED)
        self.assertTrue(self.win._armed)

        # Esc disarms (via evdev code 1 or via _esc)
        self.win.on_evdev_end(time.monotonic(), code=1)
        self.assertFalse(self.win._armed)
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

    def test_merge_sources_raw_duplicates_banner_excludes_provisional(self):
        p = self.data_root / "races.csv"
        p.write_text("race_no,heat_no,name,source,status\n"
                     "0102,1,First,sheet,\n"
                     "102,1,Second,sheet,\n"
                     "103,1,Heat,sheet,\n", encoding="utf-8")
        cfg = _make_config(self.data_root)
        cfg.data["races"] = {"csv_path": str(p)}
        storage = Storage(self.data_root)
        buffer = FrameBuffer(assumed_fps=30)
        ctl = CaptureController(cfg, storage, buffer)
        # A provisional/unlisted race keys on its timestamp name.
        ctl.start_race(1000.0, name="Race-20260829-134812",
                       race_no=None, heat_no=None)
        win = MainWindow(cfg, ctl, buffer)
        try:
            # Merge must source duplicates from the raw rows, not the de-duped
            # parsed roster.
            dups = win._has_duplicates()
            self.assertEqual(len(dups), 1)
            self.assertEqual([r.name for r in dups[0]], ["First", "Second"])
            self.assertEqual(win._recorded_count_for_key(("num", "102", "1")), 0)
            # The provisional (name-keyed) race must NOT fire the blue
            # "recorded, not in roster" banner.
            win._render_roster_banner(win._load_result, storage.race_keys())
            self.assertEqual(win.banner_host.lay.count(), 1)  # amber dup only
        finally:
            ctl.stop()
            storage.close()
            win.close()

    def test_missing_roster_defaults_to_000_heat_1(self):
        p = self.data_root / "does-not-exist.csv"
        cfg = _make_config(self.data_root)
        cfg.data["races"] = {"csv_path": str(p)}
        storage = Storage(self.data_root)
        ctl = CaptureController(cfg, storage, self.buffer)
        win = MainWindow(cfg, ctl, self.buffer)
        try:
            win._load_races()
            self.assertTrue(win._load_result.missing)
            race, is_unlisted = win.ready.current_selection()
            self.assertFalse(is_unlisted)
            self.assertEqual(race.race_no, "000")
            self.assertEqual(race.heat_no, "1")
        finally:
            ctl.stop()
            storage.close()
            win.close()

    def test_build_trigger_fallback_on_invalid_device(self):
        cfg = _make_config(self.data_root, device_path="/dev/input/nonexistent_device_xyz")
        listener, fallback = build_trigger(cfg, lambda *a: None, lambda *a: None, lambda *a: None)
        self.assertIsNone(listener)
        self.assertTrue(fallback)


if __name__ == "__main__":
    unittest.main()
