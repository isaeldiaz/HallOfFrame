"""Roster CSV reading/writing (spec: race_no, heat_no, name per row)."""
import csv
import tempfile
import unittest
from pathlib import Path

from hallofframe.races import (RaceInfo, format_display, load_races, near_misses,
                               parse_key, race_key, write_example)


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
        self.assertEqual(load_races(p).races, races)

    def test_skips_header_and_blank_rows(self):
        p = self._write([
            ["race_no", "heat_no", "name"],
            ["101", "1", "Final A"],
            [],
            ["102", "1", "Final B"],
        ])
        self.assertEqual([r.display for r in load_races(p).races],
                         ["101-H1 - Final A", "102-H1 - Final B"])

    def test_race_key_normalisation(self):
        # Leading zeros and the H-heat prefix are equal under normalisation.
        self.assertEqual(race_key("0102", "1", "X"), race_key("102", "1", "Y"))
        self.assertEqual(race_key("102", "H1", "X"), race_key("102", "1", "Y"))
        self.assertEqual(race_key("007", "H2", "X"), race_key("7", "2", "Y"))
        self.assertEqual(race_key("102", "", "X"), race_key("102", " ", "Y"))
        self.assertEqual(race_key(" 102 ", "1", "X"), race_key("102", "1", "Y"))
        self.assertEqual(race_key("102", "1", "X"), race_key("102", "1", "Y"))
        # Empty heat is a valid, distinct key component.
        self.assertNotEqual(race_key("102", "", "X"), race_key("102", "1", "X"))
        # Case is folded.
        self.assertEqual(race_key("A", "1", "X"), race_key("a", "1", "Y"))
        # A legacy name-only row never equals a numbered row.
        self.assertNotEqual(race_key("", "", "Heat 1"), race_key("102", "1", "Heat 1"))
        self.assertEqual(race_key("", "", "Heat 1"), race_key("", "", "heat 1"))

    def test_twocolumn_race_heat_no_name_is_malformed_not_rekeyed(self):
        # A row with a heat number but no name must not be re-keyed by treating
        # column A as the name (that would drop the race number from identity).
        p = self._write([["race_no", "heat_no", "name"], ["101", "2", ""]])
        r = load_races(p)
        self.assertEqual(r.errors, [(2, "expected race_no, heat_no, name")])
        self.assertFalse(r.ok)

    def test_legacy_one_column_roster(self):
        p = self._write([["Heat 1"], ["Heat 2"]])
        self.assertEqual([r.display for r in load_races(p).races],
                         ["Heat 1", "Heat 2"])
        # race/heat fields stay empty so the legacy row keys on its name
        self.assertEqual(load_races(p).races[0].key, ("name", "heat 1", ""))

    def test_missing_file_reports_missing(self):
        r = load_races(self.root / "nope.csv")
        self.assertTrue(r.missing)
        self.assertFalse(r.ok)
        self.assertEqual(r.races, [])
        self.assertFalse((self.root / "nope.csv").exists())

    def test_malformed_row_reports_line_no_no_roster(self):
        p = self._write([
            ["race_no", "heat_no", "name"],
            ["101", "1", "Final A"],
            ["", "1", ""],           # no usable name
            ["102", "1", "Final B"],
        ])
        r = load_races(p)
        self.assertEqual(r.errors, [(3, "expected race_no, heat_no, name")])
        self.assertFalse(r.ok)
        # no roster loads at all on a malformed row (BEHAVIOUR §4)
        self.assertEqual(r.races, [])

    def test_duplicate_normalised_keys_reported_first_wins(self):
        p = self._write([
            ["race_no", "heat_no", "name"],
            ["0102", "1", "First"],
            ["102", "1", "Second"],   # same normalised key as line 2
            ["103", "1", "Heat"],
        ])
        r = load_races(p)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.races), 2)
        self.assertEqual(r.races[0].display, "0102-H1 - First")
        self.assertEqual(len(r.duplicates), 1)
        key, l_a, l_b = r.duplicates[0]
        self.assertEqual(key, ("num", "102", "1"))
        self.assertEqual((l_a, l_b), (2, 3))

    def test_parse_key(self):
        self.assertEqual(parse_key("217"), ("217", ""))
        self.assertEqual(parse_key("217-H3"), ("217", "3"))
        self.assertEqual(parse_key("217h3"), ("217", "3"))
        self.assertEqual(parse_key("217 h3"), ("217", "3"))
        # Guards: never a race key.
        self.assertIsNone(parse_key(""))
        self.assertIsNone(parse_key("2"))      # one character
        self.assertIsNone(parse_key("senior")) # not a number
        self.assertIsNone(parse_key("271abc"))

    def test_near_misses_transposition_and_adjacent(self):
        races = [RaceInfo("217", "1", "H U15 1x"),
                 RaceInfo("227", "1", "D U15 2x"),
                 RaceInfo("300", "1", "M U19 4x")]
        # 271 is a transposition of 217 (distance 2).
        sugg = [r.race_no for r in near_misses(races, "271")]
        self.assertIn("217", sugg)
        # 218 is a ±1 digit edit of 217 (distance 1, beats 227's distance 2).
        sugg = [r.race_no for r in near_misses(races, "218")]
        self.assertEqual(sugg[0], "217")

    def test_near_misses_same_name_different_number(self):
        races = [RaceInfo("217", "1", "senior final"),
                 RaceInfo("300", "1", "M U19 4x")]
        sugg = [r.race_no for r in near_misses(races, "SENIOR")]
        self.assertEqual(sugg, ["217"])

    def test_format_display(self):
        self.assertEqual(format_display("101", "1", "Final A"), "101-H1 - Final A")
        self.assertEqual(format_display("", "", "Legacy"), "Legacy")
        self.assertEqual(format_display("101", "", ""), "101")


if __name__ == "__main__":
    unittest.main()
