"""T9 — Export (spec §6.8, F6)."""
import csv
import tempfile
import unittest
from pathlib import Path

from hallofframe.config import Config
from hallofframe.export import (clipboard_data, export_csv, format_elapsed,
                                utc_iso)
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
                         "Elapsed Time\tBow number\tcaptured_frame_link\tnotes")
        # data rows sorted fastest -> slowest by elapsed
        self.assertEqual(lines[5].split("\t")[1], "04")
        self.assertEqual(lines[6].split("\t")[1], "09")
        # captured_frame_link is the primary image path relative to data root
        self.assertEqual(lines[5].split("\t")[2], "races/001/c.jpg")
        # html form also produced
        self.assertIn("<table>", markup)


if __name__ == "__main__":
    unittest.main()
