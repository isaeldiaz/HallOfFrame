"""T9 — Export (spec §6.8, F6)."""
import csv
import tempfile
import unittest
from pathlib import Path

from hallofframe.config import Config
from hallofframe.export import (clipboard_data, export_all_csv, export_all_html,
                                export_csv, format_elapsed, parse_elapsed, utc_iso)
from hallofframe.storage import Storage


def make_config(data_root):
    data = {"paths": {"data_root": str(data_root)},
            "stream": {"assumed_fps": 30},
            "timing": {"viewing_mode": "water"}}
    return Config(data=data, path=Path(data_root) / "config.toml")


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.storage = Storage(self.data_root)
        self.race_id = self.storage.create_race(
            "Men under 18, single, final", 1000.0, 1000.0, "direct", 0.0, 0.0,
            "water", 30, race_no="101", heat_no="1")

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def test_format_elapsed(self):
        self.assertEqual(format_elapsed(0.0), "0:00.000")
        self.assertEqual(format_elapsed(372.483), "6:12.483")
        self.assertEqual(format_elapsed(60.0), "1:00.000")

    def test_parse_elapsed(self):
        self.assertAlmostEqual(parse_elapsed("6:12.483"), 372.483, places=3)
        self.assertAlmostEqual(parse_elapsed("12.5"), 12.5)
        self.assertAlmostEqual(parse_elapsed("99.500"), 99.5)
        self.assertAlmostEqual(parse_elapsed(":45"), 45.0)
        self.assertAlmostEqual(parse_elapsed("1:00"), 60.0)
        self.assertAlmostEqual(parse_elapsed("-1:00.500"), -60.5)
        for bad in ("", "abc", "1:99", "6:12:x", "1:2:3"):
            self.assertIsNone(parse_elapsed(bad), f"{bad!r} must be rejected")

    def test_columns_and_order(self):
        self.storage.insert_capture(self.race_id, 1, 2000.0, 2000.0, 1.0, 0.0,
                                    bow_number="07")
        self.storage.insert_capture(self.race_id, 2, 3000.0, 3000.0, 2.0, 0.0)
        out = self.data_root / "export.csv"
        export_csv(self.storage, self.race_id, out)
        with open(out, newline="", encoding="utf-8") as fh:
            reader = list(csv.reader(fh))
        self.assertEqual(reader[0], ["race_no", "heat_no", "name", "sequence",
                                     "bow_number", "elapsed_seconds",
                                     "elapsed_formatted", "wall_clock_utc",
                                     "image_file", "image_flag", "notes"])
        # race fields are stored separately
        self.assertEqual(reader[1][0], "101")
        self.assertEqual(reader[1][1], "1")
        self.assertEqual(reader[1][2], "Men under 18, single, final")
        # bow number 07 not mangled as a number
        self.assertEqual(reader[1][4], "07")
        self.assertEqual(reader[1][3], "1")
        self.assertEqual(reader[1][6], "0:01.000")

    def test_soft_deleted_excluded(self):
        cap = self.storage.insert_capture(self.race_id, 1, 2000.0, 2000.0, 1000.0, 0.0)
        self.storage.insert_capture(self.race_id, 2, 3000.0, 3000.0, 2000.0, 0.0)
        self.storage.update_capture(cap, deleted=1)
        out = self.data_root / "export.csv"
        export_csv(self.storage, self.race_id, out)
        with open(out, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))[1:]
        self.assertEqual([r[3] for r in rows], ["2"])

    def test_utc_iso(self):
        self.assertRegex(utc_iso(0.0), r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")

    def test_export_all_csv(self):
        # Race 1: two crossings (2nd press fastest), one soft-deleted.
        self.storage.insert_capture(self.race_id, 1, 3000.0, 3000.0, 10.0, 0.0,
                                    bow_number="09")
        slow = self.storage.insert_capture(self.race_id, 2, 2000.0, 2000.0, 3.0,
                                           0.0, bow_number="04")
        del_cap = self.storage.insert_capture(self.race_id, 3, 4000.0, 4000.0,
                                              12.0, 0.0)
        self.storage.update_capture(del_cap, deleted=1)
        self.storage.update_capture(slow, primary_image="races/001/c.jpg")
        # Race 2: no crossings.
        r2 = self.storage.create_race("Heat", 5000.0, 5000.0, "direct", 0.0, 0.0,
                                      "water", 30, race_no="102", heat_no="1")

        out = self.data_root / "export_all.csv"
        export_all_csv(self.storage, out)
        with open(out, newline="", encoding="utf-8") as fh:
            reader = list(csv.reader(fh))

        self.assertEqual(reader[0][0], "race_id")
        # every race listed even with zero crossings
        self.assertEqual(len(reader), 4)  # header + 2 race-1 rows + 1 empty race-2
        # fastest-first within race 1: bow 04 (3s) before 09 (10s)
        self.assertEqual(reader[1][1], "101")
        self.assertEqual(reader[1][6], "04")
        self.assertEqual(reader[2][6], "09")
        # soft-deleted crossing excluded
        self.assertNotIn("12", [r[6] for r in reader])
        # empty race row present with id and empty capture columns
        self.assertEqual(reader[3][0], str(r2))
        self.assertEqual(reader[3][6], "")


    def test_clipboard_label_value_layout_sorted(self):
        # Insert out of order: 3rd press fastest, 1st press slowest.
        self.storage.insert_capture(self.race_id, 1, 3000.0, 3000.0, 10.0, 0.0,
                                    bow_number="09")
        cap = self.storage.insert_capture(self.race_id, 2, 2000.0, 2000.0, 3.0,
                                          0.0, bow_number="04")
        self.storage.update_capture(cap, primary_image="races/001/c.jpg")
        tsv, markup = clipboard_data(self.storage, self.race_id)
        lines = tsv.strip("\r\n").split("\r\n")
        self.assertEqual(lines[0], "Race ID\t101")
        self.assertEqual(lines[1], "Heat no\t1")
        self.assertEqual(lines[2], "Category\tMen under 18, single, final")
        self.assertRegex(lines[3], r"^Gun start\t\d{2}:\d{2}:\d{2}$")
        self.assertEqual(lines[4],
                         "Position\tElapsed Time\tBow number\tnotes")
        # data rows sorted fastest -> slowest by elapsed, with position 1..n
        self.assertEqual(lines[5].split("\t")[0], "1")
        self.assertEqual(lines[5].split("\t")[2], "04")
        self.assertEqual(lines[6].split("\t")[0], "2")
        self.assertEqual(lines[6].split("\t")[2], "09")
        # html form also produced
        self.assertIn("<table>", markup)

    def _write_html(self):
        self.storage.insert_capture(self.race_id, 1, 3000.0, 3000.0, 10.0, 0.0,
                                    bow_number="09")
        cap = self.storage.insert_capture(self.race_id, 2, 2000.0, 2000.0, 3.0,
                                          0.0, bow_number="04")
        self.storage.update_capture(cap, primary_image="races/101 H1/c.jpg")
        out = self.data_root / "export_all.html"
        export_all_html(self.storage, out)
        return out.read_text(encoding="utf-8")

    def test_html_links_stored_relative_path(self):
        markup = self._write_html()
        # src is the stored path, URL-quoted, never absolute
        self.assertIn('src="races/101%20H1/c.jpg"', markup)
        self.assertNotIn(str(self.data_root), markup)
        # fastest first: bow 04's card precedes bow 09's
        self.assertLess(markup.index("0:03.000"), markup.index("0:10.000"))

    def test_html_lists_race_without_crossings(self):
        r2 = self.storage.create_race("Heat", 5000.0, 5000.0, "direct", 0.0,
                                      0.0, "water", 30, race_no="102",
                                      heat_no="1")
        markup = self._write_html()
        self.assertIn(f"race_id {r2}", markup)
        self.assertIn("No crossings recorded.", markup)

    def test_html_excludes_soft_deleted(self):
        gone = self.storage.insert_capture(self.race_id, 3, 4000.0, 4000.0,
                                           99.0, 0.0, bow_number="77")
        self.storage.update_capture(gone, deleted=1)
        markup = self._write_html()
        self.assertNotIn("1:39.000", markup)
        self.assertNotIn(">77<", markup)

    def test_html_escapes_and_flags(self):
        self.storage.insert_capture(self.race_id, 4, 6000.0, 6000.0,
                                    20.0, 0.0)
        self.storage.insert_capture(self.race_id, 5, 6000.0, 6000.0,
                                    21.0, 0.0, image_flag="approximate")
        self.storage.insert_capture(self.race_id, 6, 6000.0, 6000.0,
                                    22.0, 0.0, image_flag="missing")
        r2 = self.storage.create_race("Men <18> & over", 5000.0, 5000.0,
                                      "direct", 0.0, 0.0, "water", 30,
                                      race_no="103", heat_no="1")
        markup = self._write_html()
        self.assertIn("Men &lt;18&gt; &amp; over", markup)
        self.assertNotIn("<18>", markup)
        self.assertIn("APPROX", markup)
        self.assertIn("NO IMAGE", markup)   # the no-image capture placeholder


if __name__ == "__main__":
    unittest.main()
