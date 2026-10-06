"""T— RaceReviewDialog: cycles captures and persists bow edits (spec §7.3/F4).

Qt UI test, run headless (offscreen). Skips cleanly if PySide6/display is
unavailable so the rest of the suite stays runnable.
"""
import time
import unittest

import pytest

from hallofframe.controller import CaptureController
from hallofframe.ui.review_dialog import RaceReviewDialog

pytestmark = pytest.mark.qt


@pytest.fixture
def review_dialog_env(request, data_root, config, storage, buffer, qapp):
    inst = request.instance
    inst.data_root = data_root
    inst.config = config()
    inst.storage = storage
    inst.buffer = buffer
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer)
    inst.controller.start_race(1000.0, name="Race-test")
    for seq in (1, 2, 3):
        inst.controller.record_crossing(1000.0 + seq)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and len(
            inst.storage.captures_for_race(inst.controller.race_id)) < 3:
        time.sleep(0.02)
    assert len(inst.storage.captures_for_race(inst.controller.race_id)) == 3
    yield
    inst.controller.stop()


@pytest.mark.usefixtures("review_dialog_env")
class TestReviewDialog(unittest.TestCase):
    def test_lists_all_captures_and_edits_bow(self):
        dlg = RaceReviewDialog(self.controller, self.controller.race_id,
                               self.data_root)
        self.assertEqual(len(dlg._captures), 3)
        self.assertEqual(len(dlg._edits), 3)

        first = self.storage.captures_for_race(self.controller.race_id)[0]

        def persist(sequence, value):
            cap = next(c for c in self.storage.captures_for_race(
                self.controller.race_id) if c["sequence"] == sequence)
            self.controller.set_bow_number(cap["id"], value or None)

        dlg.bow_edited.connect(persist)
        seq1 = first["sequence"]
        dlg._edits[seq1].setText("07")
        dlg._edits[seq1].editingFinished.emit()
        self.assertEqual(self.storage.capture(first["id"])["bow_number"], "07")

        dlg.close()

    def test_delete_removes_row(self):
        dlg = RaceReviewDialog(self.controller, self.controller.race_id,
                               self.data_root)
        self.assertEqual(len(dlg._captures), 3)
        first = self.storage.captures_for_race(self.controller.race_id)[0]
        seq = first["sequence"]
        dlg._delete(seq)
        self.assertEqual(len(dlg._captures), 2)
        self.assertNotIn(seq, dlg._edits)
        self.assertEqual(len(dlg._edits), 2)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
