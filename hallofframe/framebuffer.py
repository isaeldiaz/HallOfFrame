"""Timestamped ring buffer (spec §6.3).

Frames flow in continuously; captures are *selected* afterwards by timestamp
proximity. Backed by a bounded ``deque`` of ``maxlen = int(seconds * fps * 1.5)``
frames, guarded by a ``threading.Lock``.

``window()`` is bounded by a TIME SPAN, not a frame count, so the covered
interval does not change when fps does (§6.5).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from .mjpeg import Frame


class FrameBuffer:
    def __init__(self, seconds: float = 10.0, assumed_fps: int = 30,
                 clock=time.monotonic):
        self.seconds = seconds
        self.assumed_fps = assumed_fps
        self._clock = clock
        self.maxlen = int(seconds * assumed_fps * 1.5)  # note int() — maxlen rejects float
        self._buf: deque[Frame] = deque(maxlen=self.maxlen)
        self._lock = threading.Lock()
        # Real-clock time of the last append. This is the liveness signal: a
        # stream is "down" when no frame has arrived for a while, even though the
        # ring still holds seconds of now-stale frames (their t_recv is in the
        # capture-clock domain and cannot double as a wall-clock liveness check).
        self._last_append_mono: float | None = None

    def append(self, frame: Frame) -> None:
        """Thread-safe. O(1)."""
        with self._lock:
            self._buf.append(frame)
            self._last_append_mono = self._clock()

    def recent(self, n: int) -> list[Frame]:
        """The last *n* frames, oldest→newest, under the lock (spec §6.3).

        Used by the calibration/format readers so no caller reaches into the
        private deque. ``n <= 0`` returns an empty list; asking for more than
        the ring holds returns everything."""
        if n <= 0:
            return []
        with self._lock:
            frames = list(self._buf)
        return frames[-n:]

    def live_format(self) -> tuple[str, int, float]:
        """(resolution, mean_frame_bytes, fps) from the last 30 frames.

        The pipeline properties that determine latency and that calibration is
        validated against at race start (spec §8). Raises ``ValueError`` when
        the ring is empty (stream down). fps is 0.0 when the sampled window
        spans less than 0.5 s: at startup frames arrive in a burst so a short
        window reads an inflated instantaneous rate and would spuriously flag
        the calibration as stale (spec §8)."""
        frames = self.recent(30)
        if not frames:
            raise ValueError("no frames in buffer — is the stream up?")
        mean = int(sum(len(f.jpeg) for f in frames) / len(frames))
        # resolution from the newest frame
        w = h = 0
        try:
            from PIL import Image
            import io as _io
            im = Image.open(_io.BytesIO(frames[-1].jpeg))
            im.load()
            w, h = im.size
        except Exception:
            pass
        res = f"{w}x{h}" if w and h else ""
        if len(frames) >= 2:
            span = frames[-1].t_recv - frames[0].t_recv
            fps = (len(frames) - 1) / span if span >= 0.5 else 0.0
        else:
            fps = 0.0
        return res, mean, fps

    def newest(self) -> Optional[Frame]:
        """Most recently appended frame. O(1) — for preview rendering only.

        The capture path keeps every frame (it needs the full window); this is
        for the live preview, which wants the latest frame under the lock with
        none of the O(n) walk that ``nearest(1e30)`` does (§2.4)."""
        with self._lock:
            if not self._buf:
                return None
            return self._buf[-1]

    def nearest(self, target_t: float) -> Optional[Frame]:
        """Frame whose t_recv is closest to target_t."""
        with self._lock:
            frames = list(self._buf)
        if not frames:
            return None
        best = frames[0]
        best_d = abs(best.t_recv - target_t)
        for f in frames[1:]:
            d = abs(f.t_recv - target_t)
            if d < best_d:
                best, best_d = f, d
        return best

    def window(self, target_t: float, before_s: float, after_s: float) -> list[Frame]:
        """All frames with t_recv in [target_t-before_s, target_t+after_s],
        in time order."""
        with self._lock:
            frames = list(self._buf)
        lo = target_t - before_s
        hi = target_t + after_s
        return [f for f in frames if lo <= f.t_recv <= hi]

    def span(self):
        """(oldest t_recv, newest t_recv), or None if empty."""
        with self._lock:
            frames = list(self._buf)
        if not frames:
            return None
        return (frames[0].t_recv, frames[-1].t_recv)

    def health(self, stale_after_s: float = 1.5):
        """Stream health: (alive: bool, fps: float, newest_age_s: float | None).

        ``alive`` is False when no frame has arrived within *stale_after_s*
        (stream never started / stalled — even if the ring still holds stale
        frames from before it went down). Liveness is measured against the
        real-clock append time, independent of the capture-clock t_recv. fps is
        derived from the frame timestamps. Returns (False, 0.0, None) when no
        frame has ever been appended.
        """
        with self._lock:
            frames = list(self._buf)
        if self._last_append_mono is None:
            return False, 0.0, None
        age = self._clock() - self._last_append_mono
        if len(frames) >= 2:
            fps = (len(frames) - 1) / (frames[-1].t_recv - frames[0].t_recv)
        else:
            fps = 0.0
        return (age <= stale_after_s), fps, age
