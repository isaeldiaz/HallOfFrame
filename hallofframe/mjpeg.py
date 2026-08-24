"""MJPEG stream reader (spec §6.2) with the corrected Appendix C parser.

Parsing and timestamp discipline are the most critical code in the system:

* ``t_recv`` is taken with ``time.monotonic()`` the instant a complete JPEG is
  known present, before any slicing/copying/dispatch (spec §5.2, §6.2).
* ``seq`` is instance state, never reset across reconnects (§6.2) so the
  archive index stays monotonic.
* The EOI slow path walks JPEG marker segments so an EXIF thumbnail's own EOI
  is not mistaken for the frame's (Appendix C, F8 case).
* Every exception — including those raised while reading response headers —
  reaches the reconnect backoff (§6.2 step 8).
* Accumulator is capped at 4 MB to avoid unbounded growth.
"""
from __future__ import annotations

import re
import time
import threading
from typing import Callable

try:
    import requests
except Exception:  # pragma: no cover - dependency optional for unit tests
    requests = None

MAX_BUF = 4 * 1024 * 1024
_LEN_RE = re.compile(rb"content-length:\s*(\d+)", re.I)
CHUNK = 8192


class StreamError(Exception):
    pass


def parse_boundary(ctype: str) -> bytes:
    if "boundary=" not in ctype:
        raise StreamError(f"no boundary in Content-Type: {ctype!r}")
    tok = ctype.split("boundary=", 1)[1].strip().strip('"')
    return b"--" + tok.lstrip("-").encode("latin-1")


def find_headers_end(buf: bytearray, start: int):
    """Return (end_of_headers_index, body_start). Accepts CRLFCRLF and LFLF."""
    a = buf.find(b"\r\n\r\n", start)
    b = buf.find(b"\n\n", start)
    if a < 0 and b < 0:
        return (-1, -1)
    if a < 0 or (0 <= b < a):
        return (b, b + 2)
    return (a, a + 4)


def jpeg_end(buf: bytearray, start: int) -> int:
    """Walk JPEG marker segments; return index just past EOI, or -1.

    Never mistakes an EXIF thumbnail's EOI for the frame's (Appendix C F8).
    """
    i = start
    n = len(buf)
    if n - i < 2 or buf[i] != 0xFF or buf[i + 1] != 0xD8:
        return -1  # not SOI
    i += 2
    while i + 1 < n:
        if buf[i] != 0xFF:
            i += 1
            continue
        m = buf[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m == 0xD9:
            return i + 2
        if i + 3 >= n:
            return -1
        seglen = (buf[i + 2] << 8) | buf[i + 3]
        if seglen < 2:
            return -1
        if m == 0xDA:  # start of scan
            j = i + 2 + seglen
            while j + 1 < n:
                if buf[j] == 0xFF and buf[j + 1] == 0xD9:
                    return j + 2
                if buf[j] == 0xFF and buf[j + 1] != 0x00 and not (0xD0 <= buf[j + 1] <= 0xD7):
                    i = j
                    break
                j += 1
            else:
                return -1
            continue
        i += 2 + seglen
    return -1


def feed(buf: bytearray, boundary: bytes, on_frame: Callable, seq: int,
         require_len: bool = True) -> int:
    """Consume complete parts from *buf* in place. Returns updated seq."""
    while True:
        start = buf.find(boundary)
        if start < 0:
            if len(buf) > MAX_BUF:
                raise StreamError("accumulator overflow")
            return seq
        hdr_end, body = find_headers_end(buf, start)
        if hdr_end < 0:
            if len(buf) > MAX_BUF:
                raise StreamError("accumulator overflow")
            return seq
        m = _LEN_RE.search(bytes(buf[start:hdr_end]))
        if m:
            end = body + int(m.group(1))
            if len(buf) < end:
                return seq
        else:
            if require_len:
                raise StreamError("part has no Content-Length")
            end = jpeg_end(buf, body)
            if end < 0:
                if len(buf) > MAX_BUF:
                    raise StreamError("accumulator overflow")
                return seq
        t_recv = time.monotonic()  # the critical line (§5.2, §6.2)
        t_wall = time.time()
        jpeg = bytes(buf[body:end])
        del buf[:end]
        seq += 1
        on_frame(t_recv, t_wall, seq, jpeg)
    # unreachable


class Frame:
    __slots__ = ("t_recv", "t_wall", "seq", "jpeg")

    def __init__(self, t_recv: float, t_wall: float, seq: int, jpeg: bytes):
        self.t_recv = t_recv
        self.t_wall = t_wall
        self.seq = seq
        self.jpeg = jpeg


class MJPEGReader(threading.Thread):
    def __init__(self, url: str, on_frame: Callable[[Frame], None],
                 auth: tuple[str, str] | None = None,
                 require_content_length: bool = True,
                 stop_event: threading.Event | None = None):
        super().__init__(daemon=True, name="mjpeg-reader")
        self.url = url
        self.on_frame = on_frame
        self.auth = auth
        self.require_content_length = require_content_length
        self._stop = stop_event if stop_event is not None else threading.Event()
        self.seq = 0  # instance state, never reset across reconnects (§6.2)
        self._fps_times: list[float] = []
        self._fps_lock = threading.Lock()
        self.reconnect_count = 0

    def stop(self) -> None:
        self._stop.set()

    @property
    def fps(self) -> float:
        """Rolling average over the last 30 frames."""
        with self._fps_lock:
            times = list(self._fps_times)
        if len(times) < 2:
            return 0.0
        window = times[-min(30, len(times)):]
        return (len(window) - 1) / (window[-1] - window[0]) if window[-1] > window[0] else 0.0

    def _record_arrival(self) -> None:
        now = time.monotonic()
        with self._fps_lock:
            self._fps_times.append(now)
            cutoff = now - 10.0
            while self._fps_times and self._fps_times[0] < cutoff:
                self._fps_times.pop(0)

    def run(self) -> None:
        if requests is None:
            raise RuntimeError("requests is required for MJPEGReader")
        backoff = 0.5
        while not self._stop.is_set():
            try:
                with requests.get(self.url, auth=self.auth, stream=True,
                                  timeout=(5, 10)) as r:
                    r.raise_for_status()
                    boundary = parse_boundary(r.headers.get("Content-Type", ""))
                    buf = bytearray()
                    raw = r.raw
                    backoff = 0.5
                    while not self._stop.is_set():
                        chunk = raw.read(CHUNK)
                        if not chunk:
                            break
                        buf += chunk
                        self.seq = feed(
                            buf, boundary,
                            lambda t, w, s, jpeg: self._on_frame(t, w, s, jpeg),
                            self.seq, require_len=self.require_content_length)
            except Exception:
                # Every exception reaches the backoff, including those raised
                # while reading response headers (§6.2 step 8).
                self.reconnect_count += 1
            if not self._stop.is_set():
                time.sleep(backoff)
                backoff = min(backoff * 2, 2.0)

    def _on_frame(self, t_recv: float, t_wall: float, seq: int, jpeg: bytes) -> None:
        self._record_arrival()
        try:
            self.on_frame(Frame(t_recv, t_wall, seq, jpeg))
        except Exception:
            # Callback must not kill the read loop.
            pass
