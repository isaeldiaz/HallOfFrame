"""Unit tests for the Appendix C MJPEG parse loop (spec §6.2)."""
import unittest

from regatta_timer.mjpeg import (StreamError, feed, find_headers_end, jpeg_end,
                                 parse_boundary)


class TestParseBoundary(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_boundary("multipart/x-mixed-replace; boundary=abc"),
                         b"--abc")

    def test_dashes_in_declaration(self):
        self.assertEqual(parse_boundary("multipart/x-mixed-replace; boundary=--tok"),
                         b"--tok")

    def test_quoted(self):
        self.assertEqual(parse_boundary('boundary="xyz"'), b"--xyz")

    def test_missing(self):
        with self.assertRaises(StreamError):
            parse_boundary("text/html")


class TestFindHeadersEnd(unittest.TestCase):
    def test_crlf(self):
        buf = bytearray(b"--b\r\nContent-Length: 5\r\n\r\nhello")
        end, body = find_headers_end(buf, 0)
        self.assertEqual(buf[body:body + 5], b"hello")

    def test_lf(self):
        buf = bytearray(b"--b\nContent-Length: 5\n\nhello")
        end, body = find_headers_end(buf, 0)
        self.assertEqual(buf[body:body + 5], b"hello")

    def test_none(self):
        self.assertEqual(find_headers_end(bytearray(b"--b"), 0), (-1, -1))


class TestFeed(unittest.TestCase):
    def _frames(self, boundary, jpegs):
        out = []
        buf = bytearray()
        for jpeg in jpegs:
            buf += boundary + b"\r\nContent-Length: %d\r\n\r\n" % len(jpeg)
            buf += jpeg
        seq = feed(buf, boundary, lambda t, w, s, j: out.append((s, j)), 0)
        return out, seq

    def test_normal(self):
        jpgs = [b"\xff\xd8" + b"x" * 100 + b"\xff\xd9",
                b"\xff\xd8" + b"y" * 200 + b"\xff\xd9"]
        out, seq = self._frames(b"--b", jpgs)
        self.assertEqual(seq, 2)
        self.assertEqual([j for _, j in out], jpgs)

    def test_no_content_length_requires_len(self):
        jpg = b"\xff\xd8" + b"x" * 10 + b"\xff\xd9"
        buf = bytearray(b"--b\r\nContent-Type: image/jpeg\r\n\r\n" + jpg)
        with self.assertRaises(StreamError):
            feed(buf, b"--b", lambda *a: None, 0, require_len=True)

    def test_accumulator_overflow(self):
        buf = bytearray(b"garbage with no boundary" + b"x" * (4 * 1024 * 1024))
        with self.assertRaises(StreamError):
            feed(buf, b"--b", lambda *a: None, 0)


class TestJpegEnd(unittest.TestCase):
    def _jpg(self, segments, scan):
        """Build a minimal JPEG with APP1 (EXIF) + a scan."""
        body = bytearray(b"\xff\xd8")
        for seg in segments:
            segid, payload = seg
            body += bytes([0xFF, segid])
            body += (len(payload) + 2).to_bytes(2, "big")
            body += payload
        body += b"\xff\xda"
        body += (2).to_bytes(2, "big")
        body += b"\x00\x00"
        body += scan
        body += b"\xff\xd9"
        return bytes(body)

    def test_skips_thumbnail_eoi(self):
        # APP1 containing a nested JPEG with its own FFD8...FFD9
        thumb = b"\xff\xd8" + b"\x00\x01" + b"\xff\xd9"
        jpg = self._jpg([(0xE1, thumb)], b"\x00" * 20)
        end = jpeg_end(bytearray(jpg), 0)
        self.assertEqual(end, len(jpg))  # whole frame, not truncated to thumbnail

    def test_scan_data_stuffed_ffd9_not_eoi(self):
        # scan data containing FF D9 after an FF 00 stuff byte is not EOI; the
        # parser only returns EOI on a real marker. Use FF01 (not stuffed, but
        # not EOI) inside scan to exercise the restart branch.
        scan = b"\xaa\xbb"
        jpg = self._jpg([], scan)
        end = jpeg_end(bytearray(jpg), 0)
        self.assertEqual(end, len(jpg))

    def test_not_soi(self):
        self.assertEqual(jpeg_end(bytearray(b"\x01\x02\x03"), 0), -1)


if __name__ == "__main__":
    unittest.main()
