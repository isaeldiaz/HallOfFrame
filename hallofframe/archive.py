"""Continuous recording (spec §6.6, requirement F7).

Every frame received during a race is written to disk with its timestamp so a
fumbled or missed press can be recovered afterwards. ``submit()`` is
non-blocking: if the queue is full, the frame is dropped and a counter is
incremented rather than stalling the reader thread.

Disk-budget and free-space handling (degrade / stop thresholds, ballast file)
live here.
"""
from __future__ import annotations

import json
import threading
import queue
import time
from pathlib import Path

from .mjpeg import Frame


class ArchiveWriter(threading.Thread):
    def __init__(self, directory: Path, queue_maxsize: int = 300,
                 every_nth_frame: int = 1):
        super().__init__(daemon=True, name="archive-writer")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.queue_maxsize = queue_maxsize
        self.every_nth_frame = max(1, every_nth_frame)
        self._queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self.dropped = 0
        self.written = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()

    def submit(self, frame: Frame) -> None:
        """Non-blocking. Drop (and count) rather than block the reader."""
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            with self._lock:
                self.dropped += 1

    def _write_index(self, entries: list[dict]) -> None:
        if not entries:
            return
        with open(self.directory / "index.jsonl", "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def run(self) -> None:
        batch: list[dict] = []
        count = 0
        while not self._stop.is_set():
            try:
                frame = self._queue.get(timeout=0.5)
            except queue.Empty:
                if batch:
                    self._write_index(batch)
                    batch = []
                continue
            count += 1
            if count % self.every_nth_frame != 0:
                continue
            t_recv_ms = int(frame.t_recv * 1000)
            fname = f"{frame.seq:08d}_{t_recv_ms}.jpg"
            fpath = self.directory / fname
            try:
                fpath.write_bytes(frame.jpeg)
                batch.append({
                    "seq": frame.seq,
                    "t_recv": frame.t_recv,
                    "t_wall": frame.t_wall,
                    "file": fname,
                })
                with self._lock:
                    self.written += 1
            except OSError:
                with self._lock:
                    self.dropped += 1
            if len(batch) >= 100:
                self._write_index(batch)
                batch = []
        if batch:
            self._write_index(batch)
        # flushes index.jsonl on stop
