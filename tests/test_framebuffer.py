"""T3 — FrameBuffer (spec §6.3). Pure unit test, no hardware."""
import threading
import unittest

from hallofframe.framebuffer import FrameBuffer
from hallofframe.mjpeg import Frame


def make_frames(t0=1000.0, fps=30, count=300):
    dt = 1.0 / fps
    return [Frame(t0 + i * dt, t0 + i * dt, i + 1, b"\xff\xd8jpeg%d\xff\xd9" % i)
            for i in range(count)]


class TestFrameBuffer(unittest.TestCase):
    def test_newest_returns_last_appended(self):
        fb = FrameBuffer()
        frames = make_frames()
        for f in frames:
            fb.append(f)
        self.assertEqual(fb.newest().seq, frames[-1].seq)

    def test_newest_empty(self):
        self.assertIsNone(FrameBuffer().newest())

    def test_nearest_between(self):
        fb = FrameBuffer()
        for f in make_frames():
            fb.append(f)
        # target exactly between frame 0 (t=1000) and frame 1 (t=1000+1/30)
        target = 1000.0 + (0.5 / 30.0)
        got = fb.nearest(target)
        self.assertIn(got.seq, (1, 2))

    def test_nearest_empty(self):
        fb = FrameBuffer()
        self.assertIsNone(fb.nearest(123.0))

    def test_nearest_before_oldest(self):
        fb = FrameBuffer()
        for f in make_frames():
            fb.append(f)
        self.assertEqual(fb.nearest(0.0).seq, 1)

    def test_nearest_after_newest(self):
        fb = FrameBuffer()
        frames = make_frames()
        for f in frames:
            fb.append(f)
        self.assertEqual(fb.nearest(1e9).seq, frames[-1].seq)

    def test_window_mid(self):
        fb = FrameBuffer()
        frames = make_frames()
        for f in frames:
            fb.append(f)
        target = 1000.0 + 5.0  # ~5 s in
        win = fb.window(target, 0.5, 0.5)
        self.assertTrue(win)
        for f in win:
            self.assertGreaterEqual(f.t_recv, target - 0.5)
            self.assertLessEqual(f.t_recv, target + 0.5)
        # in time order
        times = [f.t_recv for f in win]
        self.assertEqual(times, sorted(times))

    def test_window_near_start_truncated(self):
        fb = FrameBuffer()
        for f in make_frames():
            fb.append(f)
        win = fb.window(1000.0, 0.5, 0.5)
        self.assertTrue(win)
        # no padding with frames before the start
        self.assertTrue(all(f.t_recv >= 1000.0 - 1e-9 for f in win))

    def test_window_empty_span(self):
        fb = FrameBuffer()
        for f in make_frames():
            fb.append(f)
        win = fb.window(999999.0, 0.5, 0.5)
        self.assertEqual(win, [])

    def test_window_fps_independent(self):
        def span_for(fps):
            fb = FrameBuffer(seconds=20.0, assumed_fps=fps)
            for f in make_frames(fps=fps, count=600):
                fb.append(f)
            return fb.window(1000.0 + 5.0, 0.5, 0.5)
        win30 = span_for(30)
        win60 = span_for(60)
        # covered time span is equal regardless of fps
        span30 = win30[-1].t_recv - win30[0].t_recv
        span60 = win60[-1].t_recv - win60[0].t_recv
        self.assertAlmostEqual(span30, span60, delta=2.0 / 30.0)

    def test_span_empty(self):
        self.assertIsNone(FrameBuffer().span())

    def test_health_empty(self):
        alive, fps, age = FrameBuffer().health()
        self.assertFalse(alive)
        self.assertEqual(fps, 0.0)
        self.assertIsNone(age)

    def test_health_recent_frames(self):
        import time
        now = time.monotonic()
        fb = FrameBuffer()
        dt = 1.0 / 30
        for i in range(30):
            fb.append(Frame(now - (30 - i) * dt, now - (30 - i) * dt,
                            i + 1, b"\xff\xd8\xff\xd9"))
        alive, fps, age = fb.health()
        self.assertTrue(alive)
        self.assertLess(age, 1.5)
        self.assertAlmostEqual(fps, 30.0, delta=2.0)

    def test_health_stale(self):
        import time
        now = time.monotonic()
        fb = FrameBuffer()
        old = now - 10.0
        fb.append(Frame(old, old, 1, b"\xff\xd8\xff\xd9"))
        alive, fps, age = fb.health()
        self.assertFalse(alive)
        self.assertGreater(age, 1.5)

    def test_ring_eviction(self):
        fb = FrameBuffer(seconds=1.0, assumed_fps=30)
        self.assertEqual(fb.maxlen, int(1.0 * 30 * 1.5))  # 45
        for f in make_frames(fps=30, count=200):
            fb.append(f)
        self.assertLessEqual(len(fb._buf), 45)
        # oldest evicted first: oldest present seq should be the maxlen-th appended
        seqs = [f.seq for f in fb._buf]
        self.assertEqual(seqs[0], 200 - 45 + 1)

    def test_concurrent_append_nearest(self):
        fb = FrameBuffer()
        stop = threading.Event()
        errors = []

        def producer():
            dt = 1.0 / 30
            i = 0
            while not stop.is_set():
                t = 1000.0 + i * dt
                fb.append(Frame(t, t, i + 1, b"\xff\xd8\xff\xd9"))
                i += 1

        def reader():
            for _ in range(20000):
                try:
                    fb.nearest(1000.0)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        th_prod = threading.Thread(target=producer)
        th_reader = threading.Thread(target=reader)
        th_prod.start()
        th_reader.start()
        th_reader.join()
        stop.set()
        th_prod.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
