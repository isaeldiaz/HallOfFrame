"""T9 — Export (spec §6.8, F6)."""
import csv
import tempfile
import unittest
from pathlib import Path

from regatta_timer.config import Config
from regatta_timer.export import export_csv, format_elapsed, utc_iso
from regatta_timer.storage import Storage


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
            "Race-1", 1000.0, 1000.0, "direct", 0.0, 0.0, "water", 30)

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
        self.assertEqual(reader[0], ["sequence", "bow_number", "elapsed_seconds",
                                     "elapsed_formatted", "wall_clock_utc",
                                     "image_file", "image_flag", "notes"])
        # bow number 07 not mangled as a number
        self.assertEqual(reader[1][1], "07")
        self.assertEqual(reader[1][0], "1")
        self.assertEqual(reader[1][3], "0:01.000")

    def test_soft_deleted_excluded(self):
        cap = self.storage.insert_capture(self.race_id, 1, 2000.0, 2000.0, 1000.0, 0.0)
        self.storage.insert_capture(self.race_id, 2, 3000.0, 3000.0, 2000.0, 0.0)
        self.storage.update_capture(cap, deleted=1)
        out = self.data_root / "export.csv"
        export_csv(self.storage, self.race_id, out)
        with open(out, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))[1:]
        self.assertEqual([r[0] for r in rows], ["2"])

    def test_utc_iso(self):
        self.assertRegex(utc_iso(0.0), r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


if __name__ == "__main__":
    unittest.main()
