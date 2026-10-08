"""T6 — CaptureController + storage, headless (spec §6.5, §6.7, N4).

Phase 0, step 0.5: the deferred image-selection timer is driven by the
deterministic :class:`~fakes.FakeScheduler` instead of real sleeps. The
persistence writer thread stays real, so tests drain it with
``controller._queue.join()`` (which relies on the ``task_done()`` calls added to
``CaptureController._writer_loop``).
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

import pytest

from fakes import FakeScheduler
from hallofframe.controller import (CalibrationError, Capture,
                                    CaptureController, RaceStateError)
from hallofframe.mjpeg import Frame


@pytest.fixture
def controller_env(request, data_root, config, storage, buffer, seeded_buffer):
    inst = request.instance
    inst.data_root = data_root
    inst.config_factory = config
    inst.config = config()
    inst.storage = storage
    inst.buffer = buffer
    inst.scheduler = FakeScheduler()
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer,
                                        scheduler=inst.scheduler)
    inst.seed_buffer = seeded_buffer
    yield
    inst.controller.stop()


class Base(unittest.TestCase):
    def commit(self):
        """Wait for the real writer thread to drain the capture queue."""
        self.controller._queue.join()

    def settle(self):
        """Drain the writer queue, then fire the deferred selection timers."""
        self.controller._queue.join()
        self.scheduler.advance(0.6)


@pytest.mark.usefixtures("controller_env")
class TestController(Base):
    def test_trigger_without_race_ignored(self):
        self.seed_buffer(self.buffer)
        self.assertIsNone(self.controller.record_crossing(2000.0))
        self.assertEqual(self.storage.captures_for_race(99999), [])

    def test_normal_crossing(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        cap_id = self.storage.insert_capture(race_id, 1, 2000.0, 2000.0, 1000.0, 0.0)
        # use controller path instead
        self.storage.update_capture(cap_id, deleted=1)
        t_press = 1000.0 + 5.0  # 5 s after start
        self.controller.record_crossing(t_press)
        self.commit()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["elapsed_s"], 5.0, places=6)
        self.scheduler.advance(0.6)
        cap = self.storage.capture(row["id"])
        target_ms = cap["target_ms"]
        frames = self.controller.frames_for_capture(row["id"])
        self.assertTrue(any(f["t_ms"] <= target_ms for f in frames))
        self.assertTrue(any(f["t_ms"] >= target_ms for f in frames))

    def test_deferred_window(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        t_press = 1000.0 + 5.0
        self.controller.record_crossing(t_press)
        self.commit()  # row committed, selection not yet
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 1)
        # frames not yet attached (deferred ~window_after_ms + margin)
        self.assertEqual(self.storage.frames_for_capture(rows[0]["id"]), [])
        self.scheduler.advance(0.6)
        self.assertTrue(self.storage.frames_for_capture(rows[0]["id"]))

    def test_two_crossings_share_one_frames_directory(self):
        # Plan step 5.7: two crossings 200 ms apart with a +/-500 ms window must
        # produce ONE frames/ directory (no per-crossing copies); its file count
        # equals the number of distinct frames in the union, and both captures
        # list their frames.
        self.controller.window_before_s = 0.5
        self.controller.window_after_s = 0.5
        self.seed_buffer(self.buffer)          # t0=1000, 6 s at 30 fps
        race_id = self.controller.start_race(1000.0, name="Race-T")
        t_press = 1000.0 + 3.0
        self.controller.record_crossing(t_press)
        self.controller.record_crossing(t_press + 0.2)
        self.controller._queue.join()
        self.scheduler.advance(0.6)

        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 2)

        race_dir = self.controller.race_dir
        frames_dir = race_dir / "frames"
        self.assertTrue(frames_dir.is_dir(), "no frames/ directory written")
        self.assertFalse((race_dir / "captures").exists(),
                         "the retired per-crossing captures/ directory appeared")

        f0 = self.controller.frames_for_capture(rows[0]["id"])
        f1 = self.controller.frames_for_capture(rows[1]["id"])
        self.assertTrue(f0, "first capture listed no frames")
        self.assertTrue(f1, "second capture listed no frames")

        union = {f["id"] for f in f0} | {f["id"] for f in f1}
        shared = {f["id"] for f in f0} & {f["id"] for f in f1}
        self.assertTrue(shared, "200 ms apart windows must overlap")
        self.assertEqual(len(list(frames_dir.glob("*.jpg"))), len(union),
                         "file count must equal the distinct frames in the union")
        self.assertGreater(len(union), len(f0),
                           "the union must exceed one capture's window")

    def test_soft_delete_sequence_not_reused(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        for i in range(3):
            self.controller.record_crossing(1000.0 + 5.0 + i)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3])
        # soft delete middle
        self.storage.update_capture(rows[1]["id"], deleted=1)
        # new capture gets sequence 4, not 2
        self.controller.record_crossing(1000.0 + 9.0)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual([r["sequence"] for r in rows], [1, 3, 4])

    def test_undo_last_marks_newest_deleted_and_signals(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        for i in range(3):
            self.controller.record_crossing(1000.0 + 5.0 + i)
        self.settle()
        deleted = []
        self.controller.events = lambda kind, payload: (
            deleted.append(payload["sequence"]) if kind == "capture_deleted"
            else None)
        self.controller.undo_last()
        rows = self.storage.captures_for_race(race_id, include_deleted=True)
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3])
        self.assertEqual([r["deleted"] for r in rows], [0, 0, 1])
        self.assertEqual(deleted, [3])
        # visible (non-deleted) captures exclude the undone row
        visible = self.storage.captures_for_race(race_id)
        self.assertEqual([r["sequence"] for r in visible], [1, 2])

    def test_undo_last_noop_without_race(self):
        self.controller.undo_last()  # must not raise
        self.controller.events = lambda kind, payload: self.fail(
            "unexpected event")

    def test_debounced_press_recorded(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        t_press = 1000.0 + 5.0
        self.controller.record_crossing(t_press)
        self.controller.record_crossing(t_press + 0.02, debounce_suspect=True)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["debounce_suspect"], 1)

    def test_buffer_empty_records_missing(self):
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["primary_image"])
        self.assertEqual(rows[0]["image_flag"], "missing")

    def test_target_older_than_span(self):
        self.seed_buffer(self.buffer)  # oldest t=1000
        race_id = self.controller.start_race(1000.0, name="Race-T")
        # delta positive so target is just before the buffer's oldest frame; the
        # +/-50 ms window still catches the oldest frame, so a primary exists and
        # the flag is "approximate" (plan 5.3: no buffer.nearest fallback).
        self.controller.delta = 2.01
        t_press = 1000.0 + 2.0  # target = 999.99, 10 ms before span[0]
        self.controller.record_crossing(t_press)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(rows[0]["image_flag"], "approximate")
        self.assertIsNotNone(rows[0]["primary_image"])

    def test_target_newer_than_newest(self):
        self.seed_buffer(self.buffer)  # newest ~ t0+5.9667
        race_id = self.controller.start_race(1000.0, name="Race-T")
        # target just past the newest frame; the window still catches it, so the
        # flag is "approximate" rather than "missing".
        t_press = 1000.0 + 5.99  # target = 1005.99 > span[1] ~ 1005.9667
        self.controller.record_crossing(t_press)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(rows[0]["image_flag"], "approximate")
        self.assertIsNotNone(rows[0]["primary_image"])

    def test_target_far_outside_span_is_missing(self):
        # With no buffer.nearest fallback, a target far outside the buffer has no
        # frame in its window: the image is missing, not approximate.
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.delta = -500.0  # target = t_press + 500, way past newest
        self.controller.record_crossing(1000.0 + 2.0)
        self.controller._queue.join()
        # The selection margin is extended by |Δ| (capped at the buffer span),
        # so advance well past it before checking the empty window.
        self.scheduler.advance(100.0)
        rows = self.storage.captures_for_race(race_id)
        self.assertEqual(rows[0]["image_flag"], "missing")
        self.assertIsNone(rows[0]["primary_image"])

    def test_bow_number_update(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        cap_id = rows[0]["id"]
        self.controller.set_bow_number(cap_id, "14")
        row = self.storage.capture(cap_id)
        self.assertEqual(row["bow_number"], "14")
        # nothing else touched
        self.assertAlmostEqual(row["elapsed_s"], 5.0, places=6)

    def test_update_crossing_time_recomputes_press_timestamps(self):
        self.seed_buffer(self.buffer)
        t0 = 1000.0
        race_id = self.controller.start_race(t0, name="Race-T")
        self.controller.record_crossing(t0 + 5.0)
        self.settle()
        cap_id = self.storage.captures_for_race(race_id)[0]["id"]
        t0_wall = self.storage.get_race(race_id)["t0_wall"]

        self.assertTrue(self.controller.update_crossing_time(cap_id, 12.345))
        row = self.storage.capture(cap_id)
        self.assertAlmostEqual(row["elapsed_s"], 12.345, places=3)
        # derived timestamps stay consistent with the race's origin
        self.assertAlmostEqual(row["t_press"], t0 + 12.345, places=3)
        self.assertAlmostEqual(row["t_press_wall"], t0_wall + 12.345, places=3)

    # --- plan step 7.1: remove / restore / clone / time-edit re-target -----
    def test_clone_copies_fields_and_uses_max_sequence_including_deleted(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        self.controller.record_crossing(1000.0 + 6.0)
        self.settle()
        rows = self.storage.captures_for_race(race_id)
        src, other = rows[0], rows[1]
        # A deleted row still holds sequence 2; MAX(sequence)+1 must skip past it.
        self.controller.remove(other["id"])
        events = []
        self.controller.events = lambda kind, payload: events.append(
            (kind, payload))
        clone = self.controller.clone(src["id"])
        self.assertIsInstance(clone, Capture)
        new = self.storage.capture(clone.id)
        self.assertEqual(new["sequence"], 3)
        for field in ("t_press", "t_press_wall", "elapsed_s", "delta_used",
                      "target_ms", "primary_frame_id", "primary_image",
                      "image_flag"):
            self.assertEqual(new[field], src[field], field)
        self.assertIsNone(new["bow_number"])
        self.assertIsNone(new["notes"])
        self.assertEqual(new["debounce_suspect"], 0)
        self.assertEqual(new["deleted"], 0)
        self.assertEqual(events, [("capture_added", {"capture": clone})])

    def test_remove_then_restore_round_trips_and_emits(self):
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        self.settle()
        cap_id = self.storage.captures_for_race(race_id)[0]["id"]
        events = []
        self.controller.events = lambda kind, payload: events.append(
            (kind, payload))
        self.controller.remove(cap_id)
        self.assertEqual(self.storage.capture(cap_id)["deleted"], 1)
        self.assertEqual(self.storage.captures_for_race(race_id), [])
        self.assertEqual(events, [("capture_deleted", {"sequence": 1})])
        restored = self.controller.restore(cap_id)
        self.assertEqual(self.storage.capture(cap_id)["deleted"], 0)
        self.assertEqual(len(self.storage.captures_for_race(race_id)), 1)
        self.assertIsInstance(restored, Capture)
        self.assertEqual(events[1], ("capture_added", {"capture": restored}))

    def test_time_edit_without_frames_flags_missing_and_clears_primary(self):
        self.seed_buffer(self.buffer)
        t0 = 1000.0
        race_id = self.controller.start_race(t0, name="Race-T")
        self.controller.record_crossing(t0 + 5.0)
        self.settle()
        cap_id = self.storage.captures_for_race(race_id)[0]["id"]
        self.assertIsNotNone(self.storage.capture(cap_id)["primary_image"])
        self.assertTrue(self.controller.update_crossing_time(cap_id, 120.0))
        row = self.storage.capture(cap_id)
        self.assertEqual(row["image_flag"], "missing")
        self.assertIsNone(row["primary_frame_id"])
        self.assertIsNone(row["primary_image"])
        self.assertEqual(
            row["target_ms"],
            round((row["t_press"] - row["delta_used"] - t0) * 1000))
        self.assertEqual(self.storage.frames_for_capture(cap_id), [])

    def test_unlisted_race_creates_provisional_key(self):
        # WP7: an unlisted race is created with a timestamp name and null
        # race_no/heat_no, identified once afterwards in review.
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-20260829-134812",
                                             race_no=None, heat_no=None)
        row = self.storage.get_race(race_id)
        self.assertIsNone(row["race_no"])
        self.assertIsNone(row["heat_no"])
        self.assertEqual(row["name"], "Race-20260829-134812")
        self.assertTrue(self.storage.identify_race(race_id, "124", "1", "D U17 2x"))
        row = self.storage.get_race(race_id)
        self.assertEqual((row["race_no"], row["heat_no"]), ("124", "1"))
        # export carries the identified fields
        from hallofframe.render.csv import export_all_csv
        out = self.data_root / "export.csv"
        export_all_csv(self.storage, out)
        text = out.read_text()
        self.assertIn("124", text)
        self.assertIn("D U17 2x", text)

    def test_race_recognised_after_name_change(self):
        # WP1: identity is (race_no, heat_no); renaming the roster label must
        # not un-dimm a recorded race or split the recorded set.
        self.controller.start_race(1000.0, name="Old name",
                                   race_no="102", heat_no="1")
        from hallofframe.roster import RaceInfo, race_key, recorded_keys
        recorded = recorded_keys(self.storage)
        renamed = RaceInfo(race_no="102", heat_no="1", name="New name")
        self.assertIn(renamed.key, recorded)
        self.assertEqual(renamed.key, race_key("102", "1", "anything"))

    # --- start_race refusal semantics + the single events hook (step 1.3/1.6)
    def test_start_race_raises_when_already_running(self):
        self.seed_buffer(self.buffer)
        self.controller.start_race(1000.0, name="Race-1")
        with self.assertRaises(RaceStateError):
            self.controller.start_race(1001.0, name="Race-2")

    def test_start_race_raises_when_prior_race_open(self):
        self.seed_buffer(self.buffer)
        self.controller.start_race(1000.0, name="Race-1")
        # Simulate a prior race that was never ended (running False, no ended_at).
        self.controller.running = False
        with self.assertRaises(RaceStateError):
            self.controller.start_race(1001.0, name="Race-2")

    def test_second_race_after_ended_starts_without_warning(self):
        self.seed_buffer(self.buffer)
        warnings = []
        self.controller.events = lambda kind, payload: (
            warnings.append(payload["message"]) if kind == "warning" else None)
        first = self.controller.start_race(1000.0, name="Race-1")
        self.controller.end_race(1005.0)
        second = self.controller.start_race(1010.0, name="Race-2")
        self.assertNotEqual(first, second)
        self.assertEqual(warnings, [])

    def test_events_arrive_through_single_hook(self):
        self.seed_buffer(self.buffer)
        events = []
        self.controller.events = lambda kind, payload: events.append(
            (kind, payload))
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.controller.record_crossing(1000.0 + 5.0)
        self.settle()
        self.controller.end_race(1006.0)
        kinds = [kind for kind, _ in events]
        self.assertEqual(events[0], ("race_started", {"race_id": race_id}))
        added = next(payload for kind, payload in events
                     if kind == "capture_added")
        self.assertIsInstance(added["capture"], Capture)
        self.assertEqual(added["capture"].sequence, 1)
        ready = next(payload for kind, payload in events
                     if kind == "image_ready")
        self.assertEqual(ready["sequence"], 1)
        self.assertIn("path", ready)
        self.assertEqual(events[-1], ("race_ended", {"race_id": race_id}))
        self.assertIn("capture_added", kinds)
        self.assertIn("image_ready", kinds)

    def test_deferred_selection_attributes_to_the_press_race(self):
        # The deferred selector must use the race that owned the press even when
        # the next race has already started (record_crossing snapshots it).
        # Before the fix it used the live store/t0, so the photo was lost and the
        # crossing was mislabelled ``missing``.
        self.controller.window_before_s = 0.5
        self.controller.window_after_s = 0.5
        self.seed_buffer(self.buffer, t0=1000.0, seconds=6.0)
        race_a = self.controller.start_race(1000.0, name="A")
        self.controller.record_crossing(1000.5)
        self.commit()  # committed, selection still pending
        self.controller.end_race(1001.0)
        race_b = self.controller.start_race(1100.0, name="B")
        self.scheduler.advance(0.6)  # fire A's timer while B is live

        cap = self.storage.captures_for_race(race_a)[0]
        self.assertIsNone(cap["image_flag"])
        self.assertIsNotNone(cap["primary_image"])
        self.assertTrue(self.controller.frames_for_capture(cap["id"]))
        # and nothing leaked into race B's frame table
        rows_b = self.storage._conn.execute(
            "SELECT COUNT(*) FROM frame WHERE race_id=?", (race_b,)).fetchone()[0]
        self.assertEqual(rows_b, 0)

    def test_negative_delta_extends_the_selection_margin(self):
        # Water-mode Δ = R − L can be negative; the deferred selection must wait
        # |Δ| longer so the frames after the target are in the buffer.
        self.seed_buffer(self.buffer)
        self.controller.start_race(1000.0, name="R")
        self.controller.delta = -0.2
        self.controller.record_crossing(1000.0 + 5.0)
        self.controller._queue.join()
        delay = self.scheduler._items[-1].when
        self.assertAlmostEqual(
            delay, self.controller.window_after_s + self.controller._margin_s
            + 0.2, places=6)

    def test_radio_start_applies_delay_to_t0(self):
        cfg = self.config_factory(timing={"start_mode": "radio",
                                          "radio_delay_ms": 500.0})
        self.seed_buffer(self.buffer)
        ctl = CaptureController(cfg, self.storage, self.buffer,
                                scheduler=FakeScheduler())
        try:
            rid = ctl.start_race(1000.0, name="R")
            self.assertAlmostEqual(ctl.t0, 999.5, places=6)
            ctl.record_crossing(1001.0)
            ctl._queue.join()
            cap = self.storage.captures_for_race(rid)[0]
            self.assertAlmostEqual(cap["elapsed_s"], 1.5, places=6)
            self.assertEqual(self.storage.get_race(rid)["radio_delay_ms"], 500.0)
        finally:
            ctl.stop()

    def test_resume_race_reattaches_same_boot(self):
        # N4: after a restart the same boot_id resumes the race with its
        # original t0 and a rebuilt frame store.
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="R")
        self.controller.record_crossing(1000.0 + 5.0)
        self.commit()
        fresh = CaptureController(self.config, self.storage, self.buffer,
                                  scheduler=FakeScheduler())
        try:
            fresh.resume_race(race_id)
            self.assertTrue(fresh.running)
            self.assertEqual(fresh.race_id, race_id)
            self.assertAlmostEqual(fresh.t0, 1000.0, places=6)
            self.assertIsNotNone(fresh.store)
            self.assertFalse(fresh.image_off)
        finally:
            fresh.stop()

    def test_resume_with_boot_mismatch_flags_captures(self):
        # N4: a resume on a different boot reconstructs t0; captures recorded
        # after it must carry t0_reconstructed so export can mark them.
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="R")
        self.controller.record_crossing(1000.0 + 5.0)
        self.commit()
        fresh = CaptureController(self.config, self.storage, self.buffer,
                                  scheduler=FakeScheduler())
        try:
            with mock.patch(
                    "hallofframe.controller.storage_mod.current_boot_id",
                    return_value="different-boot"):
                fresh.resume_race(race_id)
            self.assertTrue(fresh.t0_reconstructed)
            fresh.record_crossing(1000.0 + 6.0)
            fresh._queue.join()
            rows = self.storage.captures_for_race(race_id)
            self.assertEqual(rows[-1]["t0_reconstructed"], 1)
            self.assertEqual(
                self.storage.get_race(race_id)["t0_reconstructed"], 1)
        finally:
            fresh.stop()

    def test_writer_thread_survives_a_failed_commit(self):
        # A single failed commit (disk full, sequence collision) must not kill
        # the persistence writer; later crossings must still be saved.
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        original = self.storage.insert_capture
        state = {"n": 0}

        def flaky(*args, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("simulated disk failure")
            return original(*args, **kwargs)

        self.storage.insert_capture = flaky
        self.controller.record_crossing(1000.0 + 1.0)
        self.controller.record_crossing(1000.0 + 2.0)
        self.controller._queue.join()
        self.assertTrue(self.controller._writer_thread.is_alive())
        self.assertEqual(len(self.storage.captures_for_race(race_id)), 1)


@pytest.mark.usefixtures("controller_env")
class TestCalibrationValidation(Base):
    """Spec §8: water mode refuses to start unless calibration matches the live
    stream; screen mode never needs calibration (§5.4)."""

    def _seed_real_jpeg(self, buffer, jpg, n=60, t0=1000.0, fps=30):
        for i in range(n):
            t = t0 + i / fps
            buffer.append(Frame(t, t, i + 1, jpg))

    def test_screen_mode_no_calibration_ok(self):
        # default make_config is viewing="screen": latency cancels, no cal needed
        self.seed_buffer(self.buffer)
        race_id = self.controller.start_race(1000.0, name="Race-T")
        self.assertIsNotNone(race_id)

    def test_water_mode_refuses_without_calibration(self):
        from PIL import Image
        import io as _io
        bio = _io.BytesIO()
        Image.new("RGB", (160, 90), (120, 120, 120)).save(bio, "JPEG")
        self._seed_real_jpeg(self.buffer, bio.getvalue())
        cfg = self.config_factory(timing={"viewing_mode": "water"})
        c = CaptureController(cfg, self.storage, self.buffer)
        with self.assertRaises(CalibrationError):
            c.start_race(1000.0, name="Race-T")
        c.stop()

    def test_water_mode_starts_with_stream_down(self):
        # A dead stream (empty buffer) auto-degrades to timing-only in ANY
        # mode: no calibration required, no image attached (§6.5).
        cfg = self.config_factory(
            timing={"viewing_mode": "water", "image_mode": "auto"})
        c = CaptureController(cfg, self.storage, self.buffer,
                              scheduler=FakeScheduler())
        rows = []
        try:
            race_id = c.start_race(1000.0, name="Race-Down")
            self.assertIsNotNone(race_id)
            self.assertTrue(c.image_off)
            self.assertEqual(c.delta, 0.0)
            c.record_crossing(1020.0)
            c._queue.join()
            rows = self.storage.captures_for_race(race_id)
        finally:
            c.stop()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_flag"], "missing")
        # The degraded mode is persisted so a restart keeps it timing-only.
        row = self.storage.get_race(race_id)
        self.assertEqual(row["image_off"], 1)

    def test_stale_frames_after_stream_drop_degrades(self):
        # Regression: the stream dies after the race was armed. The ring still
        # holds seconds of frames from before it went down, so span() is NOT
        # None — yet the stream is down. The race must still degrade to
        # timing-only rather than attach a stale pre-arming frame to crossings.
        self.seed_buffer(self.buffer)
        # Emulate the stream having stopped ~5 s ago.
        self.buffer._last_append_mono = time.monotonic() - 5.0
        cfg = self.config_factory(
            timing={"viewing_mode": "water", "image_mode": "auto"})
        c = CaptureController(cfg, self.storage, self.buffer,
                              scheduler=FakeScheduler())
        rows = []
        try:
            race_id = c.start_race(1000.0, name="Race-Drop")
            self.assertIsNotNone(race_id)
            self.assertTrue(c.image_off)
            self.assertEqual(c.delta, 0.0)
            c.record_crossing(1000.0 + 5.0)
            c._queue.join()
            rows = self.storage.captures_for_race(race_id)
        finally:
            c.stop()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["primary_image"])
        self.assertEqual(rows[0]["image_flag"], "missing")
        self.assertEqual(self.storage.get_race(race_id)["image_off"], 1)

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
        cfg = self.config_factory(timing={"viewing_mode": "water"})
        c = CaptureController(cfg, self.storage, self.buffer)
        self.assertIsNotNone(c.start_race(1000.0, name="Race-T"))
        c.stop()

    def test_timing_only_mode_starts_without_calibration(self):
        # image_mode="off": timing-only, no camera, no calibration needed — even
        # in water viewing mode, and with an empty buffer (stream off).
        cfg = self.config_factory(
            timing={"viewing_mode": "water", "image_mode": "off"})
        c = CaptureController(cfg, self.storage, self.buffer,
                              scheduler=FakeScheduler())
        rows = []
        try:
            race_id = c.start_race(1000.0, name="Timing-Only")
            self.assertIsNotNone(race_id)
            self.assertEqual(c.delta, 0.0)
            cap = c.record_crossing(1020.0)
            self.assertIsNone(cap)  # fast path; committed off-thread
            c._queue.join()
            rows = self.storage.captures_for_race(race_id)
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
        cfg = self.config_factory(timing={"viewing_mode": "water"})
        c = CaptureController(cfg, self.storage, self.buffer)
        with self.assertRaises(CalibrationError):
            c.start_race(1000.0, name="Race-T")
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
        cfg = self.config_factory(timing={"viewing_mode": "water"})
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
