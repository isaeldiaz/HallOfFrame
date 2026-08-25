"""CSV export and Excel-clipboard copy (spec §6.8, requirement F6).

Columns, in order: sequence, bow_number, elapsed_seconds, elapsed_formatted,
wall_clock_utc, image_file, image_flag, notes. Elapsed renders as M:SS.mmm with
three decimals. Soft-deleted rows are excluded.

``export_csv`` writes a file; ``clipboard_data`` returns a tab-separated string
plus an HTML table so pasting into Excel keeps column formatting.
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


_COLUMNS = ["sequence", "bow_number", "elapsed_seconds", "elapsed_formatted",
            "wall_clock_utc", "image_file", "image_flag", "notes"]


def data_rows(storage: Storage, race_id: int):
    """Yield the exported table as a header row followed by data rows."""
    yield list(_COLUMNS)
    captures = storage.captures_for_race(race_id, include_deleted=False)
    for c in captures:
        yield [
            c["sequence"],
            c["bow_number"] or "",
            f"{c['elapsed_s']:.6f}",
            format_elapsed(c["elapsed_s"]),
            utc_iso(c["t_press_wall"]),
            c["primary_image"] or "",
            c["image_flag"] or "",
            c["notes"] or "",
        ]


_data_rows = data_rows  # backward-compatible alias


def export_csv(storage: Storage, race_id: int, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for row in data_rows(storage, race_id):
            writer.writerow(row)
    return out_path


def clipboard_data(storage: Storage, race_id: int) -> tuple[str, str]:
    """Return (tab_separated, html_table) for pasting into Excel with formatting.

    A first row holds the race name (which includes the heat, e.g.
    ``Heat 1 - Men's Single``) spanning all columns, followed by the column
    header and data rows.
    """
    race = storage.get_race(race_id)
    title = (race["name"] if race and race["name"] else "") or ""
    rows = [[title] + [""] * (len(_COLUMNS) - 1)] + list(data_rows(storage, race_id))
    tsv = "\r\n".join("\t".join(str(cell) for cell in row) for row in rows) + "\r\n"
    title_cell = f'<th colspan="{len(_COLUMNS)}" style="font-weight:bold">{html.escape(title)}</th>'
    head = "".join(f"<th>{html.escape(str(cell))}</th>" for cell in rows[1])
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows[2:]
    )
    markup = (
        '<html xmlns:x="urn:schemas-microsoft-com:office:excel">'
        "<head><meta charset='utf-8'></head>"
        f"<body><table><tr>{title_cell}</tr><tr>{head}</tr>{body}</table></body></html>"
    )
    return tsv, markup
