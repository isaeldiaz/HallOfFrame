"""Deferred-photo thumbnails (spec §13.3, package B.2)."""
from __future__ import annotations

import io
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

from hallofframe.storage import Storage
from hallofframe.web import ThumbCache, WebServer, thumbnail_bytes


class TestThumbnailBytes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "frame.jpg"
        Image.new("RGB", (1920, 1080), (200, 20, 20)).save(self.path, "JPEG")

    def tearDown(self):
        self.tmp.cleanup()

    def test_downsizes_to_width_keeping_aspect(self):
        data = thumbnail_bytes(self.path, 480, 75)
        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.size, (480, 270))
        self.assertEqual(img.format, "JPEG")


class TestThumbRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.rel = "races/101 H1/frames/00001000.jpg"
        self.src = self.data_root / self.rel
        self.src.parent.mkdir(parents=True)
        Image.new("RGB", (1920, 1080), (200, 20, 20)).save(self.src, "JPEG")
        self.storage = Storage(self.data_root, event_name="TEST_EVENT")
        self.cache_root = self.data_root / "web-cache"
        thumbs = ThumbCache(self.cache_root, 480, 75)
        self.server = WebServer(("127.0.0.1", 0), self.storage, self.data_root,
                                thumbs=thumbs)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.quoted = urllib.parse.quote(self.rel)

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

    def test_thumb_served_and_cached(self):
        status, headers, body = self._get(f"/thumb/{self.quoted}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(headers["Cache-Control"],
                         "public, max-age=31536000, immutable")
        img = Image.open(io.BytesIO(body))
        self.assertEqual(img.size, (480, 270))
        cache_file = (self.cache_root / "thumbs" / "480" / self.rel)
        self.assertTrue(cache_file.is_file())

    def test_thumb_conditional_304(self):
        _, headers, _ = self._get(f"/thumb/{self.quoted}")
        etag = headers["ETag"]
        self.assertIn("-480", etag)  # width is folded into the ETag
        status, _, body = self._get(f"/thumb/{self.quoted}",
                                    {"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_traversal_is_404(self):
        rel = urllib.parse.quote("../config.toml", safe="")
        self.assertEqual(self._get(f"/thumb/{rel}")[0], 404)

    def test_non_image_is_404(self):
        (self.data_root / "races" / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self._get("/thumb/races/notes.txt")[0], 404)

    def test_corrupt_image_is_404(self):
        bad = self.data_root / "races" / "101 H1" / "frames" / "bad.jpg"
        bad.write_bytes(b"not a jpeg")
        self.assertEqual(self._get("/thumb/races/101%20H1/frames/bad.jpg")[0], 404)

    def test_absolute_rel_cannot_serve_the_original(self):
        # A crafted absolute rel still resolves to a real frame, but the cache
        # key must come from the canonical source path: the response is the
        # thumbnail, never the full-resolution original.
        abs_rel = urllib.parse.quote(str(self.src), safe="")
        status, headers, body = self._get(f"/thumb/{abs_rel}")
        self.assertEqual(status, 200)
        img = Image.open(io.BytesIO(body))
        self.assertEqual(img.size, (480, 270))
        # The original frame on disk is untouched (1920x1080).
        self.assertEqual(Image.open(self.src).size, (1920, 1080))

    def test_traversal_write_stays_inside_cache_root(self):
        cache = ThumbCache(self.cache_root, 480, 75)
        # A rel that resolves to the real source but whose raw path escapes
        # cache_root. The cache must not write at the escaped location.
        crafted = "../" * 5 + str(self.src).lstrip("/")
        result = cache.get(self.data_root, crafted)
        if result is None:
            self.skipTest("crafted rel does not resolve to a frame")
        # Every cache file the process wrote lives under cache_root/thumbs/480.
        written = list((self.cache_root / "thumbs" / "480").rglob("*.jpg"))
        self.assertTrue(written, "expected a cached thumbnail")
        for path in written:
            self.assertTrue(
                path.resolve().is_relative_to(
                    (self.cache_root / "thumbs").resolve()),
                f"cache file escaped: {path}")

    def test_unwritable_cache_still_serves(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_root, 0o500)
        try:
            status, headers, body = self._get(f"/thumb/{self.quoted}")
            self.assertEqual(status, 200)
            img = Image.open(io.BytesIO(body))
            self.assertEqual(img.size, (480, 270))
        finally:
            os.chmod(self.cache_root, 0o700)


if __name__ == "__main__":
    unittest.main()
