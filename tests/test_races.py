"""Roster CSV reading/writing (spec: race_no, heat_no, name per row)."""
import csv
import tempfile
import unittest
from pathlib import Path

from hallofframe.races import RaceInfo, format_display, load_races, write_example


class TestRaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows: list[list[str]], name="races.csv"):
        p = self.root / name
        with open(p, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        return p

    def test_write_and_read_roundtrip(self):
        p = self.root / "races.csv"
        races = [RaceInfo("101", "1", "Men under 18, single, final"),
                 RaceInfo("104", "2", "Men under 16, double, heat")]
        write_example(p, races)
        self.assertEqual(load_races(p), races)

    def test_skips_header_and_blank_rows(self):
        p = self._write([
            ["race_no", "heat_no", "name"],
            ["101", "1", "Final A"],
            [],
            ["102", "1", "Final B"],
        ])
        self.assertEqual([r.display for r in load_races(p)],
                         ["101-H1 - Final A", "102-H1 - Final B"])

    def test_legacy_one_column_roster(self):
        p = self._write([["Heat 1"], ["Heat 2"]])
        self.assertEqual([r.display for r in load_races(p)], ["Heat 1", "Heat 2"])
        # race/heat fields stay empty so the triple keys cleanly
        self.assertEqual(load_races(p)[0].key, ("", "", "Heat 1"))

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_races(self.root / "nope.csv"), [])

    def test_format_display(self):
        self.assertEqual(format_display("101", "1", "Final A"), "101-H1 - Final A")
        self.assertEqual(format_display("", "", "Legacy"), "Legacy")
        self.assertEqual(format_display("101", "", ""), "101")


if __name__ == "__main__":
    unittest.main()
