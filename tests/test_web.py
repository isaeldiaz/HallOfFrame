"""T-Web — live results HTTP server (spec §6.8)."""
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import hallofframe.storage as storage_mod
from hallofframe.storage import Storage
from hallofframe.web import (WebServer, build_index, build_race_page,
                             _excel_filename, resolve_image_file)


def _updated_string(page: str) -> str:
    return re.search(r"Results updated (\d{2}:\d{2}:\d{2}|—)", page).group(1)


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
        # Deferred photos: no image is sent by default (spec §13.3); the card
        # carries the /thumb/ and /img/ URLs for the JS viewer.
        self.assertNotIn("<img", page)
        self.assertIn('data-thumb="/thumb/races/101%20H1/c.jpg"', page)
        self.assertIn('data-full="/img/races/101%20H1/c.jpg"', page)
        self.assertIn('id="show-photos"', page)
        self.assertIn('id="viewer"', page)
        self.assertNotIn(str(self.data_root), page)
        # fastest first: bow 04 before bow 09
        self.assertLess(page.index("0:03.00"), page.index("0:10.00"))
        # Copy as Excel is a clipboard-copy button wired to the JSON payload
        self.assertIn(f'data-excel="{self.race_id}"', page)
        self.assertIn("Copy as Excel", page)
        self.assertIn("navigator.clipboard", page)
        self.assertIn("fetch('/excel/' + id)", page)
        self.assertNotIn(f'/excel/{self.race_id}.xls', page)

    def test_race_page_defers_photos_and_has_viewer(self):
        self._add_captures()
        page = build_race_page(self.storage, self.race_id)
        self.assertIn('data-thumb="/thumb/', page)
        self.assertIn('id="viewer"', page)
        self.assertIn('id="show-photos"', page)
        self.assertIn("Photos load on request to save data.", page)

    def test_offline_export_keeps_inline_images(self):
        from hallofframe.render.html import build_all_html
        self._add_captures()
        page = build_all_html(self.storage)
        self.assertIn('<img src="races/', page)
        self.assertNotIn('data-thumb', page)

    def test_race_page_has_player(self):
        self._add_captures()
        page = build_race_page(self.storage, self.race_id)
        self.assertIn('id="play-seq"', page)
        self.assertIn('id="viewer-interval"', page)
        self.assertIn("Loop", page)
        self.assertIn('id="play-all"', page)

    def test_race_page_card_positions_are_finish_order(self):
        # Both crossings have a photo so both cards carry data-pos.
        cap_slow = self.storage.insert_capture(
            self.race_id, 1, 3000.0, 3000.0, 10.0, 0.0, bow_number="09")
        self.storage.update_capture(cap_slow, primary_image="races/101 H1/a.jpg")
        cap_fast = self.storage.insert_capture(
            self.race_id, 2, 2000.0, 2000.0, 3.0, 0.0, bow_number="04")
        self.storage.update_capture(cap_fast, primary_image="races/101 H1/b.jpg")
        page = build_race_page(self.storage, self.race_id)
        pairs = re.findall(r'data-pos="(\d+)" data-caption="([^"]*)"', page)
        positions = [int(p) for p, _ in pairs]
        # Playback order is the page's card order: 1..n, fastest first.
        self.assertEqual(positions, list(range(1, len(positions) + 1)))
        self.assertTrue(pairs[0][1].startswith("0:03.00"), pairs)
        self.assertTrue(pairs[1][1].startswith("0:10.00"), pairs)

    def test_race_page_unknown_id_is_none(self):
        self.assertIsNone(build_race_page(self.storage, 9999))

    def test_last_updated_text_appears(self):
        self._add_captures()
        page = build_index(self.storage)
        self.assertRegex(page, r"Results updated \d{2}:\d{2}:\d{2}")
        self.assertRegex(page, r"Updated \d{2}:\d{2}:\d{2}")  # per-race row
        race_page = build_race_page(self.storage, self.race_id)
        self.assertRegex(race_page, r"Results updated \d{2}:\d{2}:\d{2}")
        self.assertRegex(race_page, r"Updated \d{2}:\d{2}:\d{2}")

    def test_last_updated_changes_after_bow_edit(self):
        cap = self.storage.insert_capture(self.race_id, 1, 2000.0, 2000.0, 3.0,
                                          0.0, bow_number="04")
        before = _updated_string(build_index(self.storage))
        with mock.patch.object(storage_mod, "_utcnow",
                               return_value="2030-06-07T08:09:10+00:00"):
            self.storage.update_capture(cap, bow_number="99")
        after = _updated_string(build_index(self.storage))
        self.assertNotEqual(before, after)

    def test_index_note_points_to_race_pages(self):
        self.assertIn("Photos are on each race page.", build_index(self.storage))

    def test_race_page_has_deferred_photo_viewer(self):
        self._add_captures()
        page = build_race_page(self.storage, self.race_id)
        self.assertIn('id="show-photos"', page)
        self.assertIn('id="viewer"', page)
        self.assertIn('data-thumb="/thumb/', page)
        # The viewer overlay carries a 50 % finish-line marker for judging.
        self.assertIn(
            '<div id="viewer-img" class="viewer-img">'
            '<span class="finish-line"></span></div>', page)

    def test_excel_filename_sanitizes(self):
        self.assertIn(".xls", _excel_filename(self.storage, self.race_id))
        self.assertIn("101", _excel_filename(self.storage, self.race_id))
        self.assertNotIn("/", _excel_filename(self.storage, self.race_id))
        self.assertNotIn("\\", _excel_filename(self.storage, self.race_id))


class TestConditionalRequests(unittest.TestCase):
    """Step 6.4 — conditional requests and cache headers over real HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.storage = Storage(self.data_root, event_name="TEST_EVENT")
        self.race_id = self.storage.create_race(
            "Men under 18, single, final", 1000.0, 1000.0, "direct", 0.0, 0.0,
            "water", 30, race_no="101", heat_no="1")
        self.storage.mark_race_reviewed(self.race_id)
        self.cap_id = self.storage.insert_capture(
            self.race_id, 1, 2000.0, 2000.0, 3.0, 0.0, bow_number="04")
        img = self.data_root / "races" / "101 H1" / "c.jpg"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"fakejpeg")
        self.storage.update_capture(self.cap_id,
                                    primary_image="races/101 H1/c.jpg")
        self.server = WebServer(("127.0.0.1", 0), self.storage, self.data_root)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.storage.close()
        self.tmp.cleanup()

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_index_conditional_304(self):
        status, headers, _ = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("ETag", headers)
        self.assertIn("Last-Modified", headers)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        status2, _, body2 = self._get("/", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status2, 304)
        self.assertEqual(body2, b"")

    def test_race_conditional_304(self):
        status, headers, _ = self._get(f"/race/{self.race_id}")
        self.assertEqual(status, 200)
        status2, _, _ = self._get(
            f"/race/{self.race_id}",
            {"If-Modified-Since": headers["Last-Modified"]})
        self.assertEqual(status2, 304)

    def test_edit_changes_etag(self):
        _, headers, _ = self._get("/")
        first = headers["ETag"]
        with mock.patch.object(storage_mod, "_utcnow",
                               return_value="2030-06-07T08:09:10+00:00"):
            self.storage.update_capture(self.cap_id, bow_number="99")
        _, headers2, _ = self._get("/")
        self.assertNotEqual(first, headers2["ETag"])

    def test_if_none_match_wins_over_if_modified_since(self):
        # RFC 7232 §6: a changed ETag must not be overridden by a coarse,
        # still-matching Last-Modified (same-second edit).
        _, headers, _ = self._get("/")
        status, _, _ = self._get(
            "/", {"If-None-Match": '"stale"',
                  "If-Modified-Since": headers["Last-Modified"]})
        self.assertEqual(status, 200)

    def test_unreviewed_race_is_not_served(self):
        rid = self.storage.create_race("Not reviewed", 7000.0, 7000.0, "direct",
                                       0.0, 0.0, "water", 30, race_no="999",
                                       heat_no="1")
        self.assertEqual(self._get(f"/race/{rid}")[0], 404)
        self.assertEqual(self._get(f"/excel/{rid}")[0], 404)

    def test_image_immutable_cache_headers(self):
        status, headers, body = self._get("/img/races/101%20H1/c.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"fakejpeg")
        self.assertEqual(headers["Cache-Control"],
                         "public, max-age=31536000, immutable")
        self.assertIn("ETag", headers)
        status2, _, _ = self._get("/img/races/101%20H1/c.jpg",
                                  {"If-None-Match": headers["ETag"]})
        self.assertEqual(status2, 304)

    def test_race_page_defers_images(self):
        _, _, body = self._get(f"/race/{self.race_id}")
        self.assertNotIn(b'<img', body)
        self.assertIn(b'data-thumb="/thumb/', body)

    def test_index_note(self):
        _, _, body = self._get("/")
        self.assertIn(b"Photos are on each race page.", body)


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

    def test_rejects_non_frame_files(self):
        # Only captured frames under races/ are servable; config.toml, the DB
        # and other data-root files must never be exposed by /img/.
        (self.data_root / "config.toml").write_text("secret", encoding="utf-8")
        (self.data_root / "event.db").write_bytes(b"db")
        (self.data_root / "races" / "notes.txt").write_text("x", encoding="utf-8")
        self.assertIsNone(resolve_image_file(self.data_root, "config.toml"))
        self.assertIsNone(resolve_image_file(self.data_root, "event.db"))
        self.assertIsNone(resolve_image_file(self.data_root, "races/notes.txt"))
        self.assertIsNone(resolve_image_file(self.data_root, "races/\x00evil.jpg"))


if __name__ == "__main__":
    unittest.main()