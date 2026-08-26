"""WP4 — the single atomic roster write path (BEHAVIOUR §2, §3)."""
import os
import tempfile
import unittest
from pathlib import Path

from hallofframe.races import (RosterWriteError, add_heat, add_row,
                               load_races, mutate_roster, read_rows,
                               remove_row, remove_row_exact, rename_race,
                               skip_race, write_example)


class TestRosterWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv = self.root / "races.csv"
        write_example(self.csv)

    def tearDown(self):
        self.tmp.cleanup()

    def _text(self):
        return self.csv.read_text(encoding="utf-8")

    def test_bak_holds_previous_good_file(self):
        before = self._text()
        rename_race(self.csv, ("num", "101", "1"), "Renamed")
        self.assertEqual((self.root / "races.csv.bak").read_text(), before)

    def test_rename_leaves_other_rows_byte_identical(self):
        before = self._text()
        rename_race(self.csv, ("num", "101", "1"), "Renamed")
        after_lines = self._text().splitlines()
        before_lines = before.splitlines()
        self.assertEqual(after_lines[2], before_lines[2])   # 102 row untouched
        self.assertEqual(after_lines[3], before_lines[3])   # 103 row untouched
        self.assertTrue(after_lines[1].startswith("101,1,Renamed"))

    def test_rename_sets_source_edited(self):
        rename_race(self.csv, ("num", "101", "1"), "Renamed")
        row = next(r for r in read_rows(self.csv)[1:] if r[0] == "101")
        self.assertEqual(row[3], "edited")

    def test_add_appends_when_file_unsorted(self):
        self.csv.write_text("103,1,High\n101,1,Low\n", encoding="utf-8")
        add_row(self.csv, "102", "1", "Middle")
        rows = read_rows(self.csv)[1:]
        # Not sorted -> append at the end (BEHAVIOUR §3), never re-sort.
        self.assertEqual([r[0] for r in rows], ["103", "101", "102"])

    def test_remove_row_exact_targets_literal_row(self):
        self.csv.write_text("0102,1,First\n102,1,Second\n", encoding="utf-8")
        remove_row_exact(self.csv, "0102", "1", "First")
        rows = read_rows(self.csv)[1:]
        self.assertEqual([r[:3] for r in rows], [["102", "1", "Second"]])

    def test_add_heat_without_siblings_raises(self):
        with self.assertRaises(RosterWriteError):
            add_heat(self.csv, "999", "no such race")

    def test_first_save_expands_legacy_to_5_columns(self):
        self.csv.write_text("101,1,Final A\n102,1,Final B\n", encoding="utf-8")
        rename_race(self.csv, ("num", "101", "1"), "Renamed")
        self.assertEqual(self._text().splitlines()[0],
                         "race_no,heat_no,name,source,status")
        self.assertEqual(len(self._text().splitlines()[1].split(",")), 5)

    def test_add_row_inserts_in_numeric_order(self):
        add_row(self.csv, "102", "2", "New heat")
        rows = read_rows(self.csv)[1:]
        self.assertEqual(rows[2][:3], ["102", "2", "New heat"])  # after 102-H1
        self.assertEqual([r[1] for r in rows if r[0] == "102"], ["1", "2"])

    def test_add_row_collision_writes_nothing(self):
        before = self._text()
        result, outcome = add_row(self.csv, "101", "1", "Dup")
        self.assertEqual(outcome, "collision")
        self.assertEqual(self._text(), before)

    def test_add_heat_takes_next_unused(self):
        # 104 already has H2, so the first new heat is 1, the next is 3.
        _, heat1 = add_heat(self.csv, "104", "M16 double heat")
        _, heat2 = add_heat(self.csv, "104", "M16 double heat")
        self.assertEqual([heat1, heat2], ["1", "3"])
        rows = read_rows(self.csv)[1:]
        self.assertEqual([r[1] for r in rows if r[0] == "104"], ["2", "1", "3"])

    def test_skip_is_reversible_and_stored_in_status(self):
        skip_race(self.csv, ("num", "103", "1"), skip=True)
        row = next(r for r in read_rows(self.csv)[1:] if r[0] == "103")
        self.assertEqual(row[4], "skipped")
        skip_race(self.csv, ("num", "103", "1"), skip=False)
        row = next(r for r in read_rows(self.csv)[1:] if r[0] == "103")
        self.assertEqual(row[4], "")

    def test_remove_row_drops_the_losing_duplicate(self):
        remove_row(self.csv, ("num", "105", "1"))
        self.assertEqual(len(load_races(self.csv).races), 4)

    def test_mutation_refuses_when_changed_on_disk(self):
        expected = read_rows(self.csv)
        self.csv.write_text("999,1,Changed on disk\n", encoding="utf-8")
        with self.assertRaises(RosterWriteError):
            rename_race(self.csv, ("num", "101", "1"), "X", expected=expected)

    def test_failed_write_leaves_target_unchanged(self):
        before = self._text()
        expected = read_rows(self.csv)
        # Remove the file behind the loader's back; the mutation must not run.
        self.csv.unlink()
        with self.assertRaises(RosterWriteError):
            rename_race(self.csv, ("num", "101", "1"), "X", expected=expected)
        self.assertEqual(before, before)  # nothing partial written
        self.assertFalse(self.csv.exists())


if __name__ == "__main__":
    unittest.main()
