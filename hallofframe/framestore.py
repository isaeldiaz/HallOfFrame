"""Gun-indexed frame store (plan step 5.2).

Frames are named by their time since the gun and stored once per race under
``<race_dir>/frames/{t_ms:08d}.jpg``. A crossing refers to frames by time range,
so a frame can belong to several crossings and re-selecting a window never
rewrites a file. Only the deferred-selection timer thread calls ``save``; it
blocks on nothing but disk.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .storage import Storage


class FrameStore:
    def __init__(self, storage: Storage, race_id: int, race_dir: Path, t0: float):
        self.storage = storage
        self.race_id = race_id
        self.race_dir = Path(race_dir)
        self.t0 = t0

    def t_ms(self, frame) -> int:
        """Integer milliseconds from the gun to *frame*'s arrival."""
        return round((frame.t_recv - self.t0) * 1000)

    def save(self, frames: list) -> list[sqlite3.Row]:
        """Persist every frame at ``t_ms >= 0`` not already stored for the race.

        Writes each new JPEG to ``frames/{t_ms:08d}.jpg`` via a ``.tmp`` name and
        ``os.replace`` so a crash leaves no half-written file. Returns the frame
        rows for ALL given frames (new and pre-existing), ordered by ``t_ms``."""
        # One frame per t_ms per race: keep the first frame seen for each.
        by_ms: dict[int, object] = {}
        for frame in frames:
            ms = self.t_ms(frame)
            if ms < 0:
                continue
            by_ms.setdefault(ms, frame)
        if not by_ms:
            return []

        frames_dir = self.race_dir / "frames"
        new = [(ms, f) for ms, f in by_ms.items()
               if not self.storage.frame_exists(self.race_id, ms)]
        if new:
            frames_dir.mkdir(parents=True, exist_ok=True)
            for ms, frame in new:
                dest = frames_dir / f"{ms:08d}.jpg"
                tmp = dest.with_name(dest.name + ".tmp")
                tmp.write_bytes(frame.jpeg)
                os.replace(tmp, dest)

        rows = []
        for ms, frame in by_ms.items():
            path = (frames_dir / f"{ms:08d}.jpg").relative_to(self.storage.data_root)
            rows.append((self.race_id, ms, frame.t_recv, str(path)))
        return self.storage.insert_frames(rows)


def nearest(rows, target_ms: int) -> sqlite3.Row | None:
    """The row whose ``t_ms`` is closest to *target_ms*, or None.

    Ties pick the earlier frame: *rows* are expected in ascending ``t_ms`` and a
    strict ``<`` keeps the first one seen."""
    best = None
    best_d = None
    for row in rows:
        d = abs(row["t_ms"] - target_ms)
        if best_d is None or d < best_d:
            best, best_d = row, d
    return best
