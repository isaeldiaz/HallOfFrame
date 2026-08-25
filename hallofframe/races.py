"""Race roster loaded from a CSV file (spec: one race per row).

The operator maintains a .csv where each row holds three fields — the race
number, the heat number, and the race name (e.g. ``Men under 18, single,
final``). The main window shows a single combined string in the dropdown but
stores and exports the three fields separately. Reads via the stdlib ``csv``
module; a missing file degrades to an empty list (the UI falls back to a
timestamp name). ``write_example`` creates a starter file if none exists.
"""
from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

HEADER = ("race_no", "heat_no", "name")

_EXAMPLE = [
    # (race_no, heat_no, name)
    ("101", "1", "Men under 18, single, final"),
    ("102", "1", "Women under 18, single, final"),
    ("103", "1", "Men under 16, double, heat"),
    ("104", "2", "Men under 16, double, heat"),
    ("105", "1", "Women senior, quad, final"),
]


@dataclasses.dataclass
class RaceInfo:
    """The three identifying fields of a race, kept separate for DB/export.

    ``race_no`` and ``heat_no`` are stored as text so leading zeros and the
    operator's formatting survive round-tripping. ``display`` is the single
    string shown in the dropdown.
    """
    race_no: str = ""
    heat_no: str = ""
    name: str = ""

    @property
    def display(self) -> str:
        return format_display(self.race_no, self.heat_no, self.name)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.race_no, self.heat_no, self.name)


def format_display(race_no: str, heat_no: str, name: str) -> str:
    """Compose the single dropdown string, e.g. ``101-H1 - Men under 18...``.

    Rows without a race/heat number (legacy single-name rosters) fall back to
    just the name so existing data keeps displaying unchanged.
    """
    name = (name or "").strip()
    prefix = []
    if race_no not in (None, ""):
        prefix.append(str(race_no).strip())
    if heat_no not in (None, ""):
        prefix.append(f"H{heat_no}".strip())
    if prefix:
        joined = "-".join(prefix)
        return f"{joined} - {name}".rstrip() if name else joined
    return name


def _cell(v) -> str:
    return "" if v is None else str(v).strip()


def _races_from_csv(csv_path) -> list[RaceInfo]:
    races: list[RaceInfo] = []
    seen: set[tuple[str, str, str]] = set()
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row or not any(cell not in (None, "") for cell in row):
                continue
            first = (row[0] or "").strip()
            if first.lower() == HEADER[0]:
                continue
            race_no = _cell(row[0])
            heat_no = _cell(row[1]) if len(row) > 1 else ""
            name = _cell(row[2]) if len(row) > 2 else ""
            if not name:
                # A one-column legacy roster: treat column A as the name.
                name = race_no
                race_no = ""
            key = (race_no, heat_no, name)
            if key in seen:
                continue
            seen.add(key)
            races.append(RaceInfo(race_no=race_no, heat_no=heat_no, name=name))
    return races


def load_races(csv_path) -> list[RaceInfo]:
    """Return the race rows of the roster CSV, in order."""
    if not Path(csv_path).exists():
        return []
    try:
        return _races_from_csv(csv_path)
    except Exception:
        return []


def write_example(csv_path, races: list[RaceInfo] | None = None) -> Path:
    """Write a starter CSV with a header row and example race rows."""
    races = list(races) if races is not None else [RaceInfo(*e) for e in _EXAMPLE]
    p = Path(csv_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(HEADER))
        for r in races:
            writer.writerow([r.race_no, r.heat_no, r.name])
    return p
