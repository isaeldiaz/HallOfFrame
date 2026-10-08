"""Excel-clipboard payload builder (spec §6.8, requirement F6)."""
from __future__ import annotations

import html

from ..storage import Storage
from . import format_elapsed, local_hms, row_value


def clipboard_data(storage: Storage, race_id: int,
                   include_heading: bool = True) -> tuple[str, str]:
    """Return (tab_separated, html_table) for pasting into Excel with formatting.

    Layout (one field per cell), with the heading block first:
      Race ID, race_no
      Heat no, heat_no
      Category, name
      Gun start, HH:MM:SS (local wall-clock time of the gun)
      Position, Elapsed Time, Source, Bow number, notes
      <one row per crossing, fastest to slowest>

    When *include_heading* is False the entire heading is omitted — the four
    metadata rows (Race ID, Heat no, Category, Gun start) AND the column header
    (Position, Elapsed Time, Source, Bow number, notes) — leaving only the
    crossing values (configurable via ``[web] copy_heading``).
    """
    race = storage.get_race(race_id)
    race_no = (race["race_no"] or "") if race else ""
    heat_no = (race["heat_no"] or "") if race else ""
    name = (race["name"] or "") if race else ""
    t0_wall = (race["t0_wall"] if race and race["t0_wall"] is not None else None)

    header = ["Position", "Elapsed Time", "Source", "Bow number", "notes"]
    rows: list[list[str]] = []
    if include_heading:
        rows = [
            ["Race ID", race_no],
            ["Heat no", heat_no],
            ["Category", name],
            ["Gun start", local_hms(t0_wall) if t0_wall is not None else ""],
            header,
        ]
    captures = storage.captures_for_race(race_id, include_deleted=False)
    captures = sorted(captures, key=lambda c: c["elapsed_s"])
    for pos, c in enumerate(captures, start=1):
        rows.append([
            str(pos),
            format_elapsed(c["elapsed_s"]),
            row_value(c, "elapsed_source", "press"),
            c["bow_number"] or "",
            c["notes"] or "",
        ])

    # Tabs/newlines inside a value (e.g. a multi-line note) would shift columns
    # when pasted into a spreadsheet; the HTML variant is escaped, so flatten
    # the plain-text cells too.
    def _cell(value) -> str:
        return (str(value).replace("\t", " ").replace("\r", " ")
                .replace("\n", " "))

    tsv = "\r\n".join("\t".join(_cell(c) for c in row) for row in rows) + "\r\n"

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
