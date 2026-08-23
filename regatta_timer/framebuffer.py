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

    def append(self, frame: Frame) -> None:
        """Thread-safe. O(1)."""
        with self._lock:
            self._buf.append(frame)

    def _snapshot(self) -> list[Frame]:
        return list(self._buf)

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

        ``alive`` is False when the buffer is empty or the newest frame is older
        than *stale_after_s* (stream stalled / never started). fps is derived
        from the frame timestamps. Returns (False, 0.0, None) for an empty buffer.
        """
        import time
        with self._lock:
            frames = list(self._buf)
        if not frames:
            return False, 0.0, None
        newest = frames[-1].t_recv
        age = time.monotonic() - newest
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
