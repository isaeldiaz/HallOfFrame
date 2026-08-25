"""CSV export and Excel-clipboard copy (spec §6.8, requirement F6).

``export_csv`` writes a flat table (one row per crossing): race_no, heat_no,
name, sequence, bow_number, elapsed_seconds, elapsed_formatted,
wall_clock_utc, image_file, image_flag, notes. Elapsed renders as M:SS.mmm with
three decimals. Soft-deleted rows are excluded.

``clipboard_data`` returns a tab-separated string plus an HTML table so pasting
into Excel keeps column formatting, using the label/value + sorted layout
described in the function's docstring.
"""
from __future__ import annotations

import csv
import datetime
import html
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


def local_hms(wall_ts: float) -> str:
    """Local wall-clock time HH:MM:SS (system timezone) for the gun start."""
    dt = datetime.datetime.fromtimestamp(wall_ts)
    return dt.strftime("%H:%M:%S")


_COLUMNS = ["race_no", "heat_no", "name", "sequence", "bow_number",
            "elapsed_seconds", "elapsed_formatted", "wall_clock_utc",
            "image_file", "image_flag", "notes"]


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
            writer.writerow(row)
    return out_path


def clipboard_data(storage: Storage, race_id: int) -> tuple[str, str]:
    """Return (tab_separated, html_table) for pasting into Excel with formatting.

    Layout (one field per cell):
      Race ID, race_no
      Heat no, heat_no
      Category, name
      Gun start, HH:MM:SS (local wall-clock time of the gun)
      Elapsed Time, Bow number, captured_frame_link, notes
      <one row per crossing, fastest to slowest>

    ``captured_frame_link`` is the primary image path relative to the data root.
    """
    race = storage.get_race(race_id)
    race_no = (race["race_no"] or "") if race else ""
    heat_no = (race["heat_no"] or "") if race else ""
    name = (race["name"] or "") if race else ""
    t0_wall = (race["t0_wall"] if race and race["t0_wall"] is not None else None)

    header = ["Elapsed Time", "Bow number", "captured_frame_link", "notes"]
    rows: list[list[str]] = [
        ["Race ID", race_no],
        ["Heat no", heat_no],
        ["Category", name],
        ["Gun start", local_hms(t0_wall) if t0_wall is not None else ""],
        header,
    ]
    captures = storage.captures_for_race(race_id, include_deleted=False)
    captures = sorted(captures, key=lambda c: c["elapsed_s"])
    for c in captures:
        rows.append([
            format_elapsed(c["elapsed_s"]),
            c["bow_number"] or "",
            c["primary_image"] or "",
            c["notes"] or "",
        ])

    tsv = "\r\n".join("\t".join(str(cell) for cell in row) for row in rows) + "\r\n"

    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    markup = (
        '<html xmlns:x="urn:schemas-microsoft-com:office:excel">'
        "<head><meta charset='utf-8'></head>"
        f"<body><table>{body}</table></body></html>"
    )
    return tsv, markup
