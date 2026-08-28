"""T-Web — live results HTTP server (spec §6.8)."""
import tempfile
import unittest
from pathlib import Path

from hallofframe.storage import Storage
from hallofframe.web import (build_index, build_race_page, _excel_filename,
                             resolve_image_file)


class TestWebPages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.storage = Storage(self.data_root, event_name="TEST_EVENT")
        self.race_id = self.storage.create_race(
            "Men under 18, single, final", 1000.0, 1000.0, "direct", 0.0, 0.0,
            "water", 30, race_no="101", heat_no="1")
        self.storage.mark_race_reviewed(self.race_id)

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def _add_captures(self):
        self.storage.insert_capture(self.race_id, 1, 3000.0, 3000.0, 10.0, 0.0,
                                    bow_number="09")
        cap = self.storage.insert_capture(self.race_id, 2, 2000.0, 2000.0, 3.0,
                                          0.0, bow_number="04")
        self.storage.update_capture(cap, primary_image="races/101 H1/c.jpg")

    def test_index_lists_race_and_links(self):
        self._add_captures()
        page = build_index(self.storage)
        self.assertIn("RACE 101", page)
        self.assertIn("Men under 18, single, final", page)
        self.assertIn("TEST_EVENT", page)  # event name visible in the header
        self.assertIn(f'href="/race/{self.race_id}"', page)
        # Excel copy is a clipboard-copy button labelled "Copy table"
        self.assertIn(f'data-excel="{self.race_id}"', page)
        self.assertIn("Copy table", page)
        self.assertNotIn(f'/excel/{self.race_id}.xls', page)

    def test_index_newest_first(self):
        r2 = self.storage.create_race("Heat 2", 5000.0, 5000.0, "direct", 0.0,
                                      0.0, "water", 30, race_no="102",
                                      heat_no="1")
        self.storage.mark_race_reviewed(r2)
        page = build_index(self.storage)
        self.assertLess(page.index("RACE 102"), page.index("RACE 101"))

    def test_index_hides_unreviewed_races(self):
        page = build_index(self.storage)
        self.assertIn("RACE 101", page)
        unreviewed = self.storage.create_race(
            "Unreviewed heat", 6000.0, 6000.0, "direct", 0.0, 0.0, "water", 30,
            race_no="103", heat_no="1")
        page = build_index(self.storage)
        self.assertNotIn("RACE 103", page)
        self.assertNotIn("Unreviewed heat", page)
        self.storage.mark_race_reviewed(unreviewed)
        self.assertIn("RACE 103", build_index(self.storage))

    def test_race_page_renders_cards_images_and_excel(self):
        self._add_captures()
        page = build_race_page(self.storage, self.race_id)
        self.assertIn("RACE 101", page)
        self.assertIn("TEST_EVENT", page)  # event name visible in the header
        # images served through the /img/ base, URL-quoted
        self.assertIn('src="/img/races/101%20H1/c.jpg"', page)
        self.assertNotIn(str(self.data_root), page)
        # fastest first: bow 04 before bow 09
        self.assertLess(page.index("0:03.000"), page.index("0:10.000"))
        # Copy as Excel is a clipboard-copy button wired to the JSON payload
        self.assertIn(f'data-excel="{self.race_id}"', page)
        self.assertIn("Copy as Excel", page)
        self.assertIn("navigator.clipboard", page)
        self.assertIn("fetch('/excel/' + id)", page)
        self.assertNotIn(f'/excel/{self.race_id}.xls', page)

    def test_race_page_unknown_id_is_none(self):
        self.assertIsNone(build_race_page(self.storage, 9999))

    def test_excel_filename_sanitizes(self):
        self.assertIn(".xls", _excel_filename(self.storage, self.race_id))
        self.assertIn("101", _excel_filename(self.storage, self.race_id))
        self.assertNotIn("/", _excel_filename(self.storage, self.race_id))
        self.assertNotIn("\\", _excel_filename(self.storage, self.race_id))


class TestImageResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.img = self.data_root / "races" / "101 H1" / "c.jpg"
        self.img.parent.mkdir(parents=True)
        self.img.write_bytes(b"fakejpeg")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_inside_root(self):
        self.assertEqual(resolve_image_file(self.data_root, "races/101 H1/c.jpg"),
                         self.img.resolve())

    def test_traversal_returns_none(self):
        self.assertIsNone(resolve_image_file(self.data_root, "../secret"))
        self.assertIsNone(resolve_image_file(self.data_root, "races/../../secret"))

    def test_missing_returns_none(self):
        self.assertIsNone(resolve_image_file(self.data_root, "races/nope.jpg"))


if __name__ == "__main__":
    unittest.main()