"""Scratchpad bow-number assignment through the controller (sheet 03, §13.3).

The controller owns all scratchpad I/O: commands run on the GUI thread and
enqueue their writes onto the real persistence writer thread, so every test
drains it with ``controller._queue.join()``.
"""
from __future__ import annotations

import unittest

import pytest

from fakes import FakeScheduler
from hallofframe.controller import CaptureController


@pytest.fixture
def scratch_env(request, data_root, config, storage, buffer, seeded_buffer):
    inst = request.instance
    inst.data_root = data_root
    inst.config_factory = config
    inst.config = config(scratchpad={"enabled": True},
                         timing={"image_mode": "off"})
    inst.storage = storage
    inst.buffer = buffer
    inst.scheduler = FakeScheduler()
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer,
                                        scheduler=inst.scheduler)
    inst.events = []
    inst.controller.events = lambda k, p: inst.events.append((k, p))
    inst.seed_buffer = seeded_buffer
    yield
    inst.controller.stop()


@pytest.mark.usefixtures("scratch_env")
class TestControllerScratch(unittest.TestCase):
    def commit(self):
        self.controller._queue.join()

    def start(self):
        return self.controller.start_race(1000.0, name="R")

    def bows(self, rid):
        return {r["sequence"]: (r["bow_number"], r["bow_source"])
                for r in self.storage.captures_for_race(rid)}

    def scratch_events(self):
        return [p for k, p in self.events if k == "scratch_changed"]

    def new_controller(self, enabled: bool):
        cfg = self.config_factory(scratchpad={"enabled": enabled},
                                  timing={"image_mode": "off"})
        return CaptureController(cfg, self.storage, self.buffer,
                                 scheduler=FakeScheduler())

    def test_add_scratch_requires_race(self):
        self.assertFalse(self.controller.add_scratch("14"))
        self.assertEqual(self.storage.scratch_entries(99999), [])

    def test_add_scratch_rejects_bad_text(self):
        rid = self.start()
        self.assertFalse(self.controller.add_scratch(""))
        self.assertFalse(self.controller.add_scratch("1a"))
        self.commit()
        self.assertEqual(self.storage.scratch_entries(rid), [])

    def test_worked_example(self):
        rid = self.start()
        self.controller.record_crossing(1000.0 + 1)
        self.assertTrue(self.controller.add_scratch("14"))
        self.controller.record_crossing(1000.0 + 2)
        self.assertTrue(self.controller.add_scratch("7"))
        self.controller.record_crossing(1000.0 + 3)
        self.controller.record_crossing(1000.0 + 4)
        self.assertTrue(self.controller.add_scratch("3"))
        self.commit()
        self.assertEqual(self.bows(rid),
                         {1: ("14", "live"), 2: ("7", "live"),
                          3: ("3", "live"), 4: (None, None)})
        last = self.scratch_events()[-1]
        self.assertEqual(last["typed"], 3)
        self.assertEqual(last["crossings"], 4)

    def test_typed_before_press_waits(self):
        rid = self.start()
        self.assertTrue(self.controller.add_scratch("14"))
        self.commit()
        self.controller.record_crossing(1000.0 + 1)
        self.commit()
        self.assertEqual(self.bows(rid)[1], ("14", "live"))

    def test_manual_wins_and_keeps_position(self):
        rid = self.start()
        for i in range(3):
            self.controller.record_crossing(1000.0 + 1 + i)
        self.commit()
        cap2 = self.storage.captures_for_race(rid)[1]["id"]
        self.controller.set_bow_number(cap2, "99")
        for text in ("14", "7", "21"):
            self.assertTrue(self.controller.add_scratch(text))
        self.commit()
        self.assertEqual(self.bows(rid),
                         {1: ("14", "live"), 2: ("99", "manual"),
                          3: ("21", "live")})

    def test_undo_scratch_shifts(self):
        rid = self.start()
        for i in range(3):
            self.controller.record_crossing(1000.0 + 1 + i)
        for text in ("14", "7", "21"):
            self.controller.add_scratch(text)
        self.controller.undo_scratch()
        self.commit()
        self.assertEqual(self.bows(rid),
                         {1: ("14", "live"), 2: ("7", "live"),
                          3: (None, None)})
        entries = self.storage.scratch_entries(rid, include_deleted=True)
        self.assertEqual(entries[-1]["deleted"], 1)

    def test_undo_last_capture_recomputes(self):
        rid = self.start()
        for i in range(2):
            self.controller.record_crossing(1000.0 + 1 + i)
        for text in ("14", "7"):
            self.controller.add_scratch(text)
        self.commit()
        self.controller.undo_last()
        self.commit()
        self.assertEqual(self.bows(rid), {1: ("14", "live")})
        last = self.scratch_events()[-1]
        self.assertEqual(last["crossings"], 1)
        self.assertEqual(last["typed"], 2)

    def test_end_race_recomputes_pending(self):
        rid = self.start()
        self.controller.add_scratch("14")
        self.controller.record_crossing(1000.0 + 1)
        self.controller.end_race()
        self.assertEqual(self.bows(rid), {1: ("14", "live")})
        self.assertIsNotNone(self.storage.get_race(rid)["ended_at"])

    def test_resume_continues_ordinals(self):
        rid = self.start()
        self.controller.add_scratch("14")
        self.controller.add_scratch("7")
        self.commit()
        self.controller.stop()
        events2 = []
        ctl2 = self.new_controller(enabled=True)
        ctl2.events = lambda k, p: events2.append((k, p))
        try:
            ctl2.resume_race(rid)
            self.assertTrue(ctl2.add_scratch("3"))
            ctl2._queue.join()
            self.assertEqual(
                [r["text"] for r in self.storage.scratch_entries(rid)],
                ["14", "7", "3"])
            self.assertTrue(any(k == "scratch_changed" for k, _ in events2))
        finally:
            ctl2.stop()

    def test_disabled_is_inert(self):
        events = []
        ctl = self.new_controller(enabled=False)
        ctl.events = lambda k, p: events.append((k, p))
        try:
            rid = ctl.start_race(1000.0, name="R")
            self.assertFalse(ctl.add_scratch("14"))
            ctl._queue.join()
            self.assertEqual(len(self.storage.scratch_entries(rid)), 0)
            self.assertFalse(any(k == "scratch_changed" for k, _ in events))
            ctl.record_crossing(1000.0 + 1)
            ctl._queue.join()
            bows = {r["sequence"]: (r["bow_number"], r["bow_source"])
                    for r in self.storage.captures_for_race(rid)}
            self.assertEqual(bows, {1: (None, None)})
        finally:
            ctl.stop()

    def test_set_bow_number_default_source_manual(self):
        rid = self.start()
        self.controller.record_crossing(1000.0 + 1)
        self.commit()
        cap_id = self.storage.captures_for_race(rid)[0]["id"]
        self.controller.set_bow_number(cap_id, "5")
        row = self.storage.capture(cap_id)
        self.assertEqual((row["bow_number"], row["bow_source"]),
                         ("5", "manual"))
        self.controller.set_bow_number(cap_id, None)
        row = self.storage.capture(cap_id)
        self.assertEqual((row["bow_number"], row["bow_source"]),
                         (None, "manual"))

    def test_call_job_failure_does_not_kill_writer(self):
        rid = self.start()
        self.controller.enqueue(lambda: 1 / 0)
        self.commit()
        self.controller.record_crossing(1000.0 + 1)
        self.commit()
        self.assertEqual(len(self.storage.captures_for_race(rid)), 1)
        warnings = [p for k, p in self.events if k == "warning"]
        self.assertTrue(any("write failed" in p["message"] for p in warnings))
