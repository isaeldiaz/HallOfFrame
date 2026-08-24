"""CSV export (spec §6.8, requirement F6).

Columns, in order: sequence, bow_number, elapsed_seconds, elapsed_formatted,
wall_clock_utc, image_file, image_flag, notes. Elapsed renders as M:SS.mmm with
three decimals. Soft-deleted rows are excluded.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

from .storage import Storage


def format_elapsed(elapsed_s: float) -> str:
    """M:SS.mmm e.g. 6:12.483."""
    ms = round(elapsed_s * 1000.0)
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def utc_iso(wall_ts: float) -> str:
    dt = datetime.datetime.fromtimestamp(wall_ts, datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def export_csv(storage: Storage, race_id: int, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    races = storage.get_race(race_id)
    captures = storage.captures_for_race(race_id, include_deleted=False)
    header = ["sequence", "bow_number", "elapsed_seconds", "elapsed_formatted",
              "wall_clock_utc", "image_file", "image_flag", "notes"]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for c in captures:
            writer.writerow([
                c["sequence"],
                c["bow_number"] or "",
                f"{c['elapsed_s']:.6f}",
                format_elapsed(c["elapsed_s"]),
                utc_iso(c["t_press_wall"]),
                c["primary_image"] or "",
                c["image_flag"] or "",
                c["notes"] or "",
            ])
    return out_path
