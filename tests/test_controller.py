"""T6 — CaptureController + storage, headless (spec §6.5, §6.7, N4)."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hallofframe.config import Config
from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer
from hallofframe.mjpeg import Frame
from hallofframe.storage import Storage


def make_config(data_root, window_ms=50, viewing="screen", image_mode="auto"):
    data = {
        "paths": {"data_root": str(data_root)},
        "stream": {"assumed_fps": 30, "buffer_seconds": 10.0},
        "timing": {"viewing_mode": viewing, "reaction_offset_ms": 0.0,
                   "debounce_ms": 20, "start_mode": "direct", "radio_delay_ms": 0.0,
                   "image_mode": image_mode},
        "capture": {"window_before_ms": window_ms, "window_after_ms": window_ms},
        "archive": {"enabled": False, "every_nth_frame": 1},
    }
    return Config(data=data, path=Path(data_root) / "config.toml")


def seed_buffer(buffer: FrameBuffer, t0=1000.0, fps=30, seconds=6.0):
    dt = 1.0 / fps
    n = int(seconds * fps)
    for i in range(n):
        t = t0 + i * dt
        buffer.append(Frame(t, t, i + 1, b"\xff\xd8jpeg%d\xff\xd9" % i))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.config = make_config(self.data_root)
        self.storage = Storage(self.data_root)
        self.buffer = FrameBuffer(assumed_fps=30)
        self.controller = CaptureController(self.config, self.storage, self.buffer)

    def tearDown(self):
        self.controller.stop()
        time.sleep(0.05)
        self.storage.close()
        self.tmp.cleanup()


class TestController(Base):
    def _wait_for_frames(self, capture_id, timeout=3.0, spanning=False):
        """Wait for the deferred selection. ``spanning`` waits for the whole
        window: _select_images commits one frame at a time, so the first row
        appears while the after-window frames are still being written."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frames = self.storage.frames_for_capture(capture_id)
            if frames and (not spanning
                           or (any(f["offset_ms"] <= 0 for f in frames)
                               and any(f["offset_ms"] >= 0 for f in frames))):
                return True
            time.sleep(0.02)
        return False

    def test_trigger_without_race_ignored(self):
        seed_buffer(self.buffer)
        self.assertIsNone(self.controller.record_crossing(2000.0))
        self.assertEqual(self.storage.captures_for_race(99999), [])

    def test_normal_crossing(self):
        seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        cap_id = self.storage.insert_capture(race_id, 1, 2000.0, 2000.0, 1000.0, 0.0)
        # use controller path instead
        self.storage.update_capture(cap_id, deleted=1)
        t_press = 1000.0 + 5.0  # 5 s after start
        self.controller.record_crossing(t_press)
        time.sleep(0.05)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["elapsed_s"], 5.0, places=6)
        self.assertTrue(self._wait_for_frames(row["id"], spanning=True))
        frames = self.storage.frames_for_capture(row["id"])
        self.assertTrue(any(f["offset_ms"] <= 0 for f in frames))
        self.assertTrue(any(f["offset_ms"] >= 0 for f in frames))

    def test_deferred_window(self):
        seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        t_press = 1000.0 + 5.0
        self.controller.record_crossing(t_press)
        time.sleep(0.02)  # row committed, selection not yet
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 1)
        # frames not yet attached (deferred ~window_after_ms + margin)
        self.assertEqual(self.storage.frames_for_capture(rows[0]["id"]), [])
        self.assertTrue(self._wait_for_frames(rows[0]["id"]))

    def test_soft_delete_sequence_not_reused(self):
        seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        for i in range(3):
            self.controller.record_crossing(1000.0 + 5.0 + i)
        time.sleep(0.1)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3])
        # soft delete middle
        self.storage.update_capture(rows[1]["id"], deleted=1)
        # new capture gets sequence 4, not 2
        self.controller.record_crossing(1000.0 + 9.0)
        time.sleep(0.1)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual([r["sequence"] for r in rows], [1, 3, 4])

    def test_debounced_press_recorded(self):
        seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        t_press = 1000.0 + 5.0
        self.controller.record_crossing(t_press)
        self.controller.record_crossing(t_press + 0.02, debounce_suspect=True)
        time.sleep(0.1)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["debounce_suspect"], 1)

    def test_buffer_empty_records_missing(self):
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        time.sleep(0.2)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["primary_image"])
        self.assertEqual(rows[0]["image_flag"], "missing")

    def test_target_older_than_span(self):
        seed_buffer(self.buffer)  # oldest t=1000
        race_id = self.controller.start_race(1000.0, name="Race-T")
        # delta negative so target is way before the buffer's oldest frame
        self.controller.delta = -500.0
        t_press = 1000.0 + 2.0
        self.controller.record_crossing(t_press)
        self.assertTrue(self._wait_for_frames(1, timeout=3))
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(rows[0]["image_flag"], "approximate")

    def test_target_newer_than_newest(self):
        seed_buffer(self.buffer)  # newest ~ t0+6
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.delta = -10.0  # target = t_press + 10
        t_press = 1000.0 + 20.0  # target way past newest
        self.controller.record_crossing(t_press)
        time.sleep(0.2)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(rows[0]["image_flag"], "approximate")

    def test_bow_number_update(self):
        seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        time.sleep(0.1)
        rows = self.storage.captures_for_race(race_id)
        cap_id = rows[0]["id"]
        self.controller.set_bow_number(cap_id, "14")
        row = self.storage.capture(cap_id)
        self.assertEqual(row["bow_number"], "14")
        # nothing else touched
        self.assertAlmostEqual(row["elapsed_s"], 5.0, places=6)


class TestCalibrationValidation(Base):
    """Spec §8: water mode refuses to start unless calibration matches the live
    stream; screen mode never needs calibration (§5.4)."""

    def _seed_real_jpeg(self, buffer, jpg, n=60, t0=1000.0, fps=30):
        for i in range(n):
            t = t0 + i / fps
            buffer.append(Frame(t, t, i + 1, jpg))

    def test_screen_mode_no_calibration_ok(self):
        # default make_config is viewing="screen": latency cancels, no cal needed
        seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.assertIsNotNone(race_id)

    def test_water_mode_refuses_without_calibration(self):
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (160, 90), (120, 120, 120)).save(bio, "JPEG")
        self._seed_real_jpeg(self.buffer, bio.getvalue())
        cfg = make_config(self.data_root, viewing="water")
        c = CaptureController(cfg, self.storage, self.buffer)
        self.assertIsNone(c.start_race(1000.0, name="Race-T"))
        c.stop()

    def test_water_mode_starts_with_stream_down(self):
        # A dead stream (empty buffer) auto-degrades to timing-only in ANY
        # mode: no calibration required, no image attached (§6.5).
        cfg = make_config(self.data_root, viewing="water", image_mode="auto")
        c = CaptureController(cfg, self.storage, self.buffer)
        try:
            race_id = c.start_race(1000.0, name="Race-Down")
            self.assertIsNotNone(race_id)
            self.assertTrue(c.image_off)
            self.assertEqual(c.delta, 0.0)
            c.record_crossing(1020.0)
            deadline = time.monotonic() + 3.0
            rows = []
            while time.monotonic() < deadline:
                rows = self.storage.captures_for_race(race_id)
                if rows:
                    break
                time.sleep(0.02)
        finally:
            c.stop()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_flag"], "missing")
        # The degraded mode is persisted so a restart keeps it timing-only.
        row = self.storage.get_race(race_id)
        self.assertEqual(row["image_off"], 1)

    def test_water_mode_matching_calibration_starts(self):
        from PIL import Image
        import json
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1440, 1080), (120, 120, 120)).save(bio, "JPEG")
        jpg = bio.getvalue()
        self._seed_real_jpeg(self.buffer, jpg)
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1440x1080",
            "fps": 30, "lens": "", "mean_frame_bytes": len(jpg)}))
        cfg = make_config(self.data_root, viewing="water")
        c = CaptureController(cfg, self.storage, self.buffer)
        self.assertIsNotNone(c.start_race(1000.0, name="Race-T"))
        c.stop()

    def test_timing_only_mode_starts_without_calibration(self):
        # image_mode="off": timing-only, no camera, no calibration needed — even
        # in water viewing mode, and with an empty buffer (stream off).
        cfg = make_config(self.data_root, viewing="water", image_mode="off")
        c = CaptureController(cfg, self.storage, self.buffer)
        try:
            race_id = c.start_race(1000.0, name="Timing-Only")
            self.assertIsNotNone(race_id)
            self.assertEqual(c.delta, 0.0)
            cap = c.record_crossing(1020.0)
            self.assertIsNone(cap)  # fast path; committed off-thread
            deadline = time.monotonic() + 3.0
            rows = []
            while time.monotonic() < deadline:
                rows = self.storage.captures_for_race(race_id)
                if rows:
                    break
                time.sleep(0.02)
        finally:
            c.stop()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_flag"], "missing")

    def test_water_mode_mismatched_resolution_refuses(self):
        import json
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1440, 1080), (120, 120, 120)).save(bio, "JPEG")
        jpg = bio.getvalue()
        self._seed_real_jpeg(self.buffer, jpg)
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1920x1080",
            "fps": 30, "lens": "", "mean_frame_bytes": len(jpg)}))
        cfg = make_config(self.data_root, viewing="water")
        c = CaptureController(cfg, self.storage, self.buffer)
        self.assertIsNone(c.start_race(1000.0, name="Race-T"))
        c.stop()

    def test_water_mode_scene_change_does_not_block_start(self):
        # Scene complexity changes JPEG size but not pipeline latency; the
        # calibration-vs-live validation must NOT gate on mean_frame_bytes.
        import json
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1440, 1080), (120, 120, 120)).save(bio, "JPEG")
        jpg = bio.getvalue()
        self._seed_real_jpeg(self.buffer, jpg)
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1440x1080",
            "fps": 30, "lens": "", "mean_frame_bytes": 1}))  # live is >>1
        cfg = make_config(self.data_root, viewing="water")
        c = CaptureController(cfg, self.storage, self.buffer)
        self.assertIsNotNone(c.start_race(1000.0, name="Race-T"))
        c.stop()

    # --- calibration_status (proactive UI indicator) ----------------------
    def test_calibration_status_missing(self):
        from hallofframe.controller import calibration_status
        ok, detail = calibration_status(self.config, self.buffer)
        self.assertFalse(ok)
        self.assertIn("no calibration.json", detail)

    def test_calibration_status_matching(self):
        import json
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1440, 1080), (120, 120, 120)).save(bio, "JPEG")
        self._seed_real_jpeg(self.buffer, bio.getvalue(), fps=30)
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1440x1080",
            "fps": 30}))
        from hallofframe.controller import calibration_status
        ok, detail = calibration_status(self.config, self.buffer)
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_calibration_status_resolution_changed(self):
        import json
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1920, 1080), (120, 120, 120)).save(bio, "JPEG")
        self._seed_real_jpeg(self.buffer, bio.getvalue(), fps=30)
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1440x1080",
            "fps": 30}))
        from hallofframe.controller import calibration_status
        ok, detail = calibration_status(self.config, self.buffer)
        self.assertFalse(ok)
        self.assertIn("RESOLUTION CHANGED", detail)

    def test_calibration_status_fps_changed(self):
        import json
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1440, 1080), (120, 120, 120)).save(bio, "JPEG")
        self._seed_real_jpeg(self.buffer, bio.getvalue(), fps=15)  # live 15 fps
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1440x1080",
            "fps": 30}))
        from hallofframe.controller import calibration_status
        ok, detail = calibration_status(self.config, self.buffer)
        self.assertFalse(ok)
        self.assertIn("FPS CHANGED", detail)

    def test_calibration_status_startup_burst_no_false_alarm(self):
        # A burst of frames within a short span (startup) reads an inflated
        # instantaneous fps; the check must defer rather than flag stale.
        import json
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (1440, 1080), (120, 120, 120)).save(bio, "JPEG")
        self._seed_real_jpeg(self.buffer, bio.getvalue(), fps=30)
        # collapse all frame timestamps into a tiny span to emulate a burst
        burst = list(self.buffer._buf)
        for i, f in enumerate(burst):
            f.t_recv = burst[0].t_recv + i * 0.001
        (self.data_root / "calibration.json").write_text(json.dumps({
            "latency_median_ms": 94.0, "resolution": "1440x1080",
            "fps": 30}))
        from hallofframe.controller import calibration_status
        ok, detail = calibration_status(self.config, self.buffer)
        self.assertTrue(ok)
        self.assertEqual(detail, "")


if __name__ == "__main__":
    unittest.main()
