"""Roster dialogs: Add inserts after the selected row, Rename edits the label
(plan step 2.7). Identify/Merge dialogs were deleted in step 2.2."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from hallofframe.roster import RaceInfo, load_races, read_rows, write_example

pytestmark = pytest.mark.qt


@pytest.mark.usefixtures("qapp")
class TestRosterDialogs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv = self.root / "races.csv"
        write_example(self.csv)
        self.expected = read_rows(self.csv)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_race_inserts_after_selected(self):
        from hallofframe.ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(str(self.csv), "102", "2", "New heat",
                            expected=self.expected,
                            after_key=("num", "102", "1"))
        dlg._add()
        rows = read_rows(self.csv)[1:]
        self.assertEqual(rows[2][:3], ["102", "2", "New heat"])

    def test_add_race_collision_writes_nothing(self):
        from hallofframe.ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(str(self.csv), "101", "1", "Dup",
                            expected=self.expected)
        before = self.csv.read_text()
        with mock.patch("hallofframe.ui.roster_dialog.QMessageBox.warning"):
            dlg._add()
        self.assertEqual(self.csv.read_text(), before)

    def test_add_race_enter_in_name_field_commits(self):
        # Buttons are Qt.NoFocus, so the dialog has no default button: Enter
        # while typing must trigger the primary action explicitly (§4).
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from hallofframe.ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(str(self.csv), "999", "1", "New Boat",
                            expected=self.expected)
        dlg.name.setFocus()
        QTest.keyClick(dlg.name, Qt.Key_Return)
        self.assertTrue(any(r.key == RaceInfo("999", "1", "New Boat").key
                            for r in load_races(str(self.csv)).races))

    def test_rename_logs_one_line_per_mutation(self):
        # BEHAVIOUR §10: a roster mutation writes an audit-trail log line.
        from hallofframe.ui.roster_dialog import RenameDialog
        calls = []

        class FakeLogger:
            def info(self, component, event, **fields):
                calls.append((component, event, fields))

        dlg = RenameDialog(str(self.csv), "101", "1", "Old", set(), None,
                           expected=self.expected, logger=FakeLogger())
        dlg.name.setText("New")
        dlg._save()
        self.assertEqual(len(calls), 1)
        component, event, fields = calls[0]
        self.assertEqual((component, event), ("roster", "rename"))
        self.assertEqual(fields["after"], "New")
        self.assertIn("file", fields)

    def test_rename_leaves_recorded_name_alone_by_default(self):
        # A roster rename is a label change; the recorded race is only updated
        # via the explicit amber tick.
        from hallofframe.ui.roster_dialog import RenameDialog
        dlg = RenameDialog(str(self.csv), "101", "1", "Old", set(), None,
                           expected=self.expected)
        self.assertFalse(dlg.amber.isVisibleTo(dlg))

    def test_rename_enter_in_name_field_commits(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from hallofframe.ui.roster_dialog import RenameDialog
        dlg = RenameDialog(str(self.csv), "101", "1", "Old", set(), None,
                           expected=self.expected)
        dlg.name.setText("New")
        dlg.name.setFocus()
        QTest.keyClick(dlg.name, Qt.Key_Return)
        self.assertTrue(any(r.name == "New"
                            for r in load_races(str(self.csv)).races))


if __name__ == "__main__":
    unittest.main()
