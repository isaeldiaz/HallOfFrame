"""Late-regatta soak in pytest form (the "almost the whole day is over" check).

Synthesises a near-complete regatta DB (many races, crossings, window frames and
image files) through the real Storage API, drives the FINAL race of the day
through the real CaptureController, and asserts the store is still consistent at
that scale: integrity, sequence discipline, image files on disk, exports at full
scale, and that the trigger path still returns immediately. The heavier,
parameterised version of the same helpers lives in
``hallofframe.tools.late_regatta_soak`` (``--session-races`` for the
accumulation half).
"""
import tempfile
import time
import unittest
from pathlib import Path

from hallofframe.config import Config
from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer
from hallofframe.storage import Storage
from hallofframe.tools.late_regatta_soak import (
    make_config, run_final_race, run_session, seed_regatta, verify_end_of_day,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root, window_ms=500, fps=30)
        self.storage = Storage(self.root)
        self.buffer = FrameBuffer(assumed_fps=30)
        self.controller = CaptureController(self.config, self.storage, self.buffer)

    def tearDown(self):
        self.controller.stop()
        time.sleep(0.05)
        self.storage.close()
        self.tmp.cleanup()


class TestNearEndOfRegatta(Base):
    def test_full_day_seed_and_final_race(self):
        seed = seed_regatta(self.storage, races=40, min_captures=4,
                            max_captures=6, frames_per_capture=31, fps=30)
        self.assertEqual(seed["races"], 40)
        self.assertGreaterEqual(seed["captures"], 160)
        self.assertEqual(seed["frames"], seed["captures"] * 31)

        final = run_final_race(self.config, self.storage, self.buffer,
                               self.controller, crossings=5, fps=30)
        self.assertTrue(final["ended"])
        self.assertEqual(final["live_seqs"], [1, 3, 4, 5])
        self.assertEqual(final["deleted_seqs"], [2])
        self.assertEqual(final["race_id"], seed["races"] + 1)

        # --- the recorded time is the press time (never a later Qt read) ----
        race = self.storage.get_race(final["race_id"])
        for seq in final["live_seqs"]:
            row = next(c for c in self.storage.captures_for_race(final["race_id"])
                       if c["sequence"] == seq)
            self.assertLess(abs(row["t_press"] - final["presses"][seq]), 0.01,
                            f"seq {seq}: t_press drifted from the press time")
            self.assertAlmostEqual(row["elapsed_s"],
                                   row["t_press"] - race["t0_monotonic"], places=6)
            self.assertIsNone(row["image_flag"],
                              f"seq {seq} should have a clean frame selection")
        suspect = next(c for c in self.storage.captures_for_race(final["race_id"])
                       if c["sequence"] == 4)
        self.assertEqual(suspect["debounce_suspect"], 1)

        # --- deferred selection still lands after end_race ------------------
        last = next(c for c in self.storage.captures_for_race(final["race_id"])
                    if c["sequence"] == 5)
        frames = self.storage.frames_for_capture(last["id"])
        self.assertTrue(frames, "last crossing got no window frames after end")
        self.assertTrue(last["primary_image"], "last crossing has no primary")
        self.assertTrue((self.root / last["primary_image"]).exists())
        self.assertTrue(any(seq == 5 for seq, _ in final["image_ready"]),
                        "image_ready was never emitted for the last crossing")

        # --- trigger path never blocks ---------------------------------------
        self.assertLess(final["max_enqueue_s"], 0.05,
                        "record_crossing blocked on disk (trigger path)")
        lat = final["commit_latencies_s"]
        if lat:
            self.assertLess(lat[min(len(lat) - 1, int(len(lat) * 0.99))], 0.25,
                            "commit-to-signal p99 grew with a large DB")

        # --- end-of-day verification on the whole store ---------------------
        checks = verify_end_of_day(self.storage, self.root, seed, final)
        failures = [(name, detail) for name, ok, detail in checks if not ok]
        self.assertEqual(failures, [], f"{len(failures)} checks failed: {failures}")

    def test_session_timers_drain_between_races(self):
        # The accumulation half: many races on one controller, deferred
        # selection timers must drain to zero every time (no leak).
        seed_regatta(self.storage, races=20, min_captures=4, max_captures=6,
                     frames_per_capture=7, fps=30)
        cfg = make_config(self.root, window_ms=50, fps=30)
        controller = CaptureController(cfg, self.storage, self.buffer)
        try:
            session = run_session(cfg, self.storage, self.buffer, controller,
                                  races=4, crossings=3, fps=30)
        finally:
            controller.stop()
        self.assertEqual(session["races_done"], 4)
        self.assertEqual(session["crossings"], 12)
        self.assertTrue(session["timers_drained"],
                        "deferred-selection timers leaked across races")
        self.assertLessEqual(session["max_timers_pending"], 3,
                             "more pending timers than presses in a race")
        self.assertTrue(self.storage.integrity_ok())


if __name__ == "__main__":
    unittest.main()