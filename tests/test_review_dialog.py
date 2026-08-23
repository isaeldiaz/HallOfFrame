"""T— RaceReviewDialog: cycles captures and persists bow edits (spec §7.3/F4).

Qt UI test, run headless (offscreen). Skips cleanly if PySide6/display is
unavailable so the rest of the suite stays runnable.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from regatta_timer.config import Config
from regatta_timer.controller import CaptureController
from regatta_timer.framebuffer import FrameBuffer
from regatta_timer.mjpeg import Frame
from regatta_timer.storage import Storage
from regatta_timer.ui.review_dialog import RaceReviewDialog

try:
    from PySide6.QtWidgets import QApplication
    _QT = True
except Exception:  # pragma: no cover
    _QT = False


def _make_config(data_root):
    data = {
        "paths": {"data_root": str(data_root)},
        "stream": {"assumed_fps": 30, "buffer_seconds": 10.0},
        "timing": {"viewing_mode": "screen", "reaction_offset_ms": 0.0,
                   "debounce_ms": 20, "start_mode": "direct", "radio_delay_ms": 0.0},
        "capture": {"window_before_ms": 50, "window_after_ms": 50},
        "archive": {"enabled": False, "every_nth_frame": 1},
    }
    return Config(data=data, path=Path(data_root) / "config.toml")


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestReviewDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.config = _make_config(self.data_root)
        self.storage = Storage(self.data_root)
        self.buffer = FrameBuffer(assumed_fps=30)
        self.controller = CaptureController(self.config, self.storage, self.buffer)
        self.controller.start_race(1000.0, name="Race-test")
        for seq in (1, 2, 3):
            self.controller.record_crossing(1000.0 + seq)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(
                self.storage.captures_for_race(self.controller.race_id)) < 3:
            time.sleep(0.02)
        self.assertEqual(
            len(self.storage.captures_for_race(self.controller.race_id)), 3)

    def tearDown(self):
        self.controller.stop()
        self.storage.close()
        self.tmp.cleanup()

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
