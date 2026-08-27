"""Structured JSON-lines logging (spec §6.9).

Every event is written to <data_root>/logs/hallofframe-<race_id>.jsonl with
``t_mono``, ``t_wall``, ``level``, ``component``, ``event`` plus event-specific
fields.

Critically, logging MUST NOT touch the trigger path. We use a
``logging.QueueHandler`` + background ``QueueListener`` so a stalled disk can
never delay a capture. Per-frame lines during a race are forbidden.
"""
from __future__ import annotations

import json
import logging
import queue
import time
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path

_NOW = time.time
_MONO = time.monotonic


class JsonlFormatter(logging.Formatter):
    """Format a record as a single JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "t_wall": round(_NOW(), 6),
            "t_mono": round(_MONO(), 6),
            "level": record.levelname.lower(),
            "component": getattr(record, "component", None),
            "event": getattr(record, "event", record.getMessage()),
        }
        extra = getattr(record, "event_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


class HallOfFrameLogger:
    """Owns the file target and the queue/listener pair for one log file."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.logger = logging.getLogger(f"hallofframe:{log_path.stem}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(JsonlFormatter())

        # QueueListener needs a queue.Queue with .get(); QueueHandler is the
        # producer side that wraps the same queue. Passing the handler to
        # QueueListener would break the listener's dequeue (spec §6.9).
        self.queue: queue.Queue = queue.Queue()
        self.listener = QueueListener(self.queue, handler, respect_handler_level=True)
        self.queue_handler = QueueHandler(self.queue)

        self.logger.addHandler(self.queue_handler)

    def start(self) -> None:
        self.listener.start()

    def stop(self) -> None:
        self.listener.stop()
        for h in self.logger.handlers:
            self.logger.removeHandler(h)
            h.close()

    def emit(self, component: str, event: str, level: int = logging.INFO, **fields) -> None:
        self.logger.log(level, event, extra={
            "component": component,
            "event": event,
            "event_fields": fields,
        })

    def info(self, component: str, event: str, **fields) -> None:
        self.emit(component, event, logging.INFO, **fields)

    def warning(self, component: str, event: str, **fields) -> None:
        self.emit(component, event, logging.WARNING, **fields)

    def error(self, component: str, event: str, **fields) -> None:
        self.emit(component, event, logging.ERROR, **fields)


def start_logging(log_dir: Path, race_id: int | None, event_name: str = "event") -> HallOfFrameLogger:
    log_dir.mkdir(parents=True, exist_ok=True)
    event_name = (event_name or "event").strip() or "event"
    stem = f"{event_name}-{race_id}" if race_id is not None else f"{event_name}-app"
    logger = HallOfFrameLogger(log_dir / f"{stem}.jsonl")
    logger.start()
    return logger
