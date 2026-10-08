"""Flat CSV exporters (spec §6.8, requirement F6)."""
from __future__ import annotations

import csv
from pathlib import Path

from ..storage import Storage
from . import format_elapsed, local_hms, utc_iso
from .html import _all_race_blocks


_COLUMNS = ["race_no", "heat_no", "name", "sequence", "bow_number",
            "elapsed_seconds", "elapsed_formatted", "wall_clock_utc",
            "image_file", "image_flag", "notes"]


def _safe(value) -> str:
    """Neutralize spreadsheet formula injection (OWASP CSV injection).

    A cell starting with ``=``, ``+``, ``-`` or ``@`` is prefixed with a single
    quote so Excel/LibreOffice treat it as text instead of evaluating it."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _data_rows(storage: Storage, race_id: int):
    """Yield the exported table as a header row followed by data rows. Each
    data row is prefixed with the race's three identifying fields."""
    race = storage.get_race(race_id)
    race_no = (race["race_no"] or "") if race else ""
    heat_no = (race["heat_no"] or "") if race else ""
    name = (race["name"] or "") if race else ""
    yield list(_COLUMNS)
    captures = storage.captures_for_race(race_id, include_deleted=False)
    for c in captures:
        yield [
            race_no,
            heat_no,
            name,
            c["sequence"],
            c["bow_number"] or "",
            f"{c['elapsed_s']:.6f}",
            format_elapsed(c["elapsed_s"]),
            utc_iso(c["t_press_wall"]),
            c["primary_image"] or "",
            c["image_flag"] or "",
            c["notes"] or "",
        ]


def export_csv(storage: Storage, race_id: int, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for row in _data_rows(storage, race_id):
            writer.writerow([_safe(cell) for cell in row])
    return out_path


_ALL_COLUMNS = ["race_id", "race_no", "heat_no", "name", "gun_start",
                "sequence", "bow_number", "elapsed_seconds",
                "elapsed_formatted", "wall_clock_utc", "captured_frame_link",
                "image_flag", "notes"]


def export_all_csv(storage: Storage, out_path: str | Path) -> Path:
    """Dump the entire database to a flat CSV: one row per crossing, grouped by
    race (oldest first) and fastest-to-slowest within a race.

    Every race is listed — a race with no crossings still appears once, with
    empty capture columns. ``race_id`` disambiguates races that share the same
    race_no/heat_no/name (overwrites). Soft-deleted crossings are excluded.
    """
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([_safe(cell) for cell in _ALL_COLUMNS])
        for race, captures in _all_race_blocks(storage):
            t0_wall = race["t0_wall"] if race["t0_wall"] is not None else None
            base = [
                race["id"],
                race["race_no"] or "",
                race["heat_no"] or "",
                race["name"] or "",
                local_hms(t0_wall) if t0_wall is not None else "",
            ]
            if not captures:
                writer.writerow([_safe(cell)
                                 for cell in base + [""] * (len(_ALL_COLUMNS)
                                                            - len(base))])
                continue
            for c in captures:
                writer.writerow([_safe(cell) for cell in base + [
                    c["sequence"],
                    c["bow_number"] or "",
                    f"{c['elapsed_s']:.6f}",
                    format_elapsed(c["elapsed_s"]),
                    utc_iso(c["t_press_wall"]),
                    c["primary_image"] or "",
                    c["image_flag"] or "",
                    c["notes"] or "",
                ]])
    return out_path
