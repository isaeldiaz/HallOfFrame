"""Timestamped ring buffer (spec §6.3).

Frames flow in continuously; captures are *selected* afterwards by timestamp
proximity. Backed by a bounded ``deque`` of ``maxlen = int(seconds * fps * 1.5)``
frames, guarded by a ``threading.Lock``.

``window()`` is bounded by a TIME SPAN, not a frame count, so the covered
interval does not change when fps does (§6.5).
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional

from .mjpeg import Frame


class FrameBuffer:
    def __init__(self, seconds: float = 10.0, assumed_fps: int = 30):
        self.seconds = seconds
        self.assumed_fps = assumed_fps
        self.maxlen = int(seconds * assumed_fps * 1.5)  # note int() — maxlen rejects float
        self._buf: deque[Frame] = deque(maxlen=self.maxlen)
        self._lock = threading.Lock()
        self._resize_warning_emitted = False
        # Real-clock time of the last append. This is the liveness signal: a
        # stream is "down" when no frame has arrived for a while, even though the
        # ring still holds seconds of now-stale frames (their t_recv is in the
        # capture-clock domain and cannot double as a wall-clock liveness check).
        self._last_append_mono: float | None = None

    def append(self, frame: Frame) -> None:
        """Thread-safe. O(1)."""
        import time
        with self._lock:
            self._buf.append(frame)
            self._last_append_mono = time.monotonic()

    def _snapshot(self) -> list[Frame]:
        return list(self._buf)

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
        import time
        with self._lock:
            frames = list(self._buf)
        if self._last_append_mono is None:
            return False, 0.0, None
        age = time.monotonic() - self._last_append_mono
        if len(frames) >= 2:
            fps = (len(frames) - 1) / (frames[-1].t_recv - frames[0].t_recv)
        else:
            fps = 0.0
        return (age <= stale_after_s), fps, age

    def check_fps(self, measured_fps: float) -> None:
        """Warn (once) if the live measured fps diverges from assumed_fps by
        more than 20%, since maxlen derives from assumed_fps (§6.3)."""
        if measured_fps <= 0:
            return
        ratio = measured_fps / self.assumed_fps
        if (ratio < 0.8 or ratio > 1.2) and not self._resize_warning_emitted:
            self._resize_warning_emitted = True
            return True
        return False
