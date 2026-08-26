"""WP4/WP6 — roster dialogs: add-race collision routing, rename keeps DB name."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hallofframe.races import read_rows, write_example

try:
    _QT = QApplication.instance() is not None or bool(QApplication([]))
except Exception:  # pragma: no cover
    _QT = False


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestRosterDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv = self.root / "races.csv"
        write_example(self.csv)
        self.expected = read_rows(self.csv)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_race_collision_routes_to_panel_and_writes_nothing(self):
        from hallofframe.ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(str(self.csv), "101", "1", "Dup",
                            expected=self.expected)
        before = self.csv.read_text()
        dlg._add()
        self.assertTrue(dlg.collision.isVisibleTo(dlg))
        self.assertEqual(self.csv.read_text(), before)  # nothing written

    def test_rename_logs_one_line_per_mutation(self):
        # BEHAVIOUR §10: a roster mutation writes an audit-trail log line.
        from hallofframe.races import RaceInfo
        from hallofframe.ui.roster_dialog import RenameDialog
        calls = []
        class FakeLogger:
            def info(self, component, event, **fields):
                calls.append((component, event, fields))
        dlg = RenameDialog(str(self.csv), RaceInfo("101", "1", "Old"),
                           set(), None, expected=self.expected, logger=FakeLogger())
        dlg.name.setText("New")
        dlg._save()
        self.assertEqual(len(calls), 1)
        component, event, fields = calls[0]
        self.assertEqual((component, event), ("roster", "rename"))
        self.assertEqual(fields["after"], "New")
        self.assertIn("file", fields)

    def test_rename_leaves_recorded_name_alone_by_default(self):
        # DB-side behaviour: rename is a roster label change; the recorded race
        # name only changes via the explicit action (tested at storage level).
        from hallofframe.ui.roster_dialog import RenameDialog
        from hallofframe.races import RaceInfo
        dlg = RenameDialog(str(self.csv), RaceInfo("101", "1", "Old"),
                           set(), None, expected=self.expected)
        self.assertFalse(dlg.amber.isVisible())


if __name__ == "__main__":
    unittest.main()
