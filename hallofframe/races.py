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
import os
import shutil
import time
from dataclasses import dataclass, field
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
    def key(self) -> tuple:
        return race_key(self.race_no, self.heat_no, self.name)


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


def _fold(s) -> str:
    """Case- and diacritic-insensitive form for filter matching."""
    import unicodedata
    s = _cell(s).casefold()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if not unicodedata.combining(c))


def parse_key(text) -> tuple[str, str] | None:
    """Parse filter text into ``(race_no, heat_no)`` or ``None``.

    The create row (WP5) appears only when the filter parses as a race number
    with an optional heat — ``"217"`` -> ``("217", "")``, ``"217-H3"`` ->
    ``("217", "3")``. Never for an empty or one-character filter, and never for
    text that is not a numeric race key, so ``"senior"`` can never become a race.
    """
    t = _cell(text)
    if len(t) < 2:
        return None
    import re
    m = re.fullmatch(r"([0-9a-z]+?)(?:[-\s]*[h]([0-9]+))?", t.casefold())
    if not m:
        return None
    rn, hn = m.group(1), m.group(2) or ""
    if not rn.isdigit():
        return None
    return (rn, hn)


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def near_misses(races: list, query: str) -> list[RaceInfo]:
    """Closest roster matches to *query*, best first (WP5).

    Near misses: digit transpositions (distance 2), ±1 digit edits (distance 1),
    and same-name-different-number. Used to show suggestions above the quieter
    create row instead of an empty result with nothing beneath it.
    """
    q = _norm(query, drop_h=True)
    scored: list[tuple[int, RaceInfo]] = []
    for r in races:
        rn = _norm(r.race_no, drop_h=True)
        if rn.isdigit() and q.isdigit():
            d = _edit_distance(q, rn)
            if d <= 2:
                scored.append((d, r))
                continue
        if r.name and _fold(q) in _fold(r.name):
            scored.append((1, r))
    scored.sort(key=lambda t: t[0])
    seen: set = set()
    out = []
    for _, r in scored:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out[:3]


def _norm(s, drop_h: bool = False) -> str:
    """Normalise one key field (BEHAVIOUR §1): trim, casefold, drop a leading
    ``h`` heat prefix (*drop_h*, for number fields only — never for a name),
    drop leading zeros on a purely numeric part. The operator's own formatting
    is what is stored and displayed; this is used only for comparison."""
    s = _cell(s).casefold()
    if drop_h and len(s) >= 1 and s[0] == "h":
        s = s[1:]
    if s and s.isdigit():
        stripped = s.lstrip("0")
        s = stripped if stripped else s
    return s


def race_key(race_no, heat_no, name) -> tuple:
    """The discriminated identity of a race (BEHAVIOUR §1).

    Identity is the pair ``(race_no, heat_no)`` under normalisation — the name
    is a mutable label and is NOT part of the key. A row with neither a race
    number nor a heat number (legacy one-column roster) keys on its normalised
    name instead, so the two domains can never collide:

        ("num",  norm_race_no, norm_heat_no)   # has a race and/or heat number
        ("name", norm_name, "")                 # legacy, name-only
    """
    has_num = (race_no not in (None, "")) or (heat_no not in (None, ""))
    if has_num:
        return ("num", _norm(race_no, drop_h=True), _norm(heat_no, drop_h=True))
    return ("name", _norm(name), "")


@dataclass
class RosterLoad:
    """The result of loading a roster CSV (BEHAVIOUR §4).

    Failures are loud, not swallowed: a missing file, an OS/IO error and every
    malformed row are reported here so the UI can surface them as banners.
    ``errors`` and ``file_error`` are mutually exclusive file-level outcomes;
    ``duplicates`` are reported but the roster still loads (first wins).
    """
    races: list[RaceInfo] = field(default_factory=list)
    path: str = ""
    loaded_at: str = ""  # "HH:MM" clock time of this load
    missing: bool = False          # configured path does not exist
    file_error: str = ""           # unreadable (permissions / IO) — OS message
    errors: list[tuple[int, str]] = field(default_factory=list)  # (line_no, msg)
    duplicates: list[tuple] = field(default_factory=list)        # (key, l_a, l_b)

    @property
    def ok(self) -> bool:
        """True when a usable roster was produced (no file-level failure)."""
        return not self.missing and not self.file_error and not self.errors


def load_races(csv_path) -> RosterLoad:
    """Parse the roster CSV into a ``RosterLoad``.

    One row per race: ``race_no, heat_no, name`` (anything past column three is
    ignored). A one-column legacy roster treats column A as the name. A missing
    file, an unreadable file, and any malformed row are reported on the result
    rather than swallowed; a malformed row means no roster loads (BEHAVIOUR §4).
    """
    path = Path(csv_path)
    result = RosterLoad(path=str(path), loaded_at=time.strftime("%H:%M"))
    if not path.exists():
        result.missing = True
        return result

    try:
        fh = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        result.file_error = str(exc)
        return result

    races: list[RaceInfo] = []
    first_line: dict[tuple, int] = {}
    with fh:
        reader = csv.reader(fh)
        for lineno, row in enumerate(reader, 1):
            if not row or not any(_cell(c) for c in row):
                continue
            if _cell(row[0]).lower() == HEADER[0]:
                continue
            race_no = _cell(row[0])
            heat_no = _cell(row[1]) if len(row) > 1 else ""
            name = _cell(row[2]) if len(row) > 2 else ""
            if not name and not heat_no:
                # A one-column legacy roster (no heat either): column A is the
                # name (BEHAVIOUR §1). A row with a heat number but no name is
                # malformed below, not silently re-keyed.
                name = race_no
                race_no = ""
            if not name:
                # Nothing usable to identify this row with.
                result.errors.append((lineno, "expected race_no, heat_no, name"))
                continue
            race = RaceInfo(race_no=race_no, heat_no=heat_no, name=name)
            key = race.key
            if key in first_line:
                result.duplicates.append((key, first_line[key], lineno))
                continue  # first occurrence wins
            first_line[key] = lineno
            races.append(race)

    if result.errors:
        # BEHAVIOUR §4: any malformed row means no roster loads.
        races = []
    result.races = races
    return result


def write_example(csv_path, races: list[RaceInfo] | None = None) -> Path:
    """Write a starter CSV with a header row and example race rows (WP4 adds the
    ``source``/``status`` columns; the loader ignores anything past column 3, so
    older builds still read it)."""
    races = list(races) if races is not None else [RaceInfo(*e) for e in _EXAMPLE]
    p = Path(csv_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(HEADER_5))
        for r in races:
            writer.writerow([r.race_no, r.heat_no, r.name, "sheet", ""])
    return p


# --- WP4: the single atomic write path ------------------------------------

HEADER_5 = ("race_no", "heat_no", "name", "source", "status")


def read_rows(csv_path) -> list[list[str]] | None:
    """Read the roster fresh as raw cell lists (header first). ``None`` if the
    file does not exist. Every mutation re-reads this way (BEHAVIOUR §2) so it
    operates on what is on disk, not a stale in-memory copy."""
    path = Path(csv_path)
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.reader(fh)]


def _pad5(row: list[str]) -> list[str]:
    """Ensure a row carries the 5 standard columns. A legacy 3-column file is
    padded (source='sheet'? no — empty, meaning 'scheduled' for status and the
    provenance is resolved on the row's own write); cells beyond column 5 are
    preserved so extra columns round-trip."""
    out = list(row)
    while len(out) < 5:
        out.append("")
    return out


def _atomic_write(csv_path, rows: list[list[str]]) -> None:
    """Atomic replace: copy the current file to ``<name>.bak`` (one generation),
    write ``<name>.tmp``, fsync, then ``os.replace`` over the target and fsync
    the directory. A crash mid-write leaves either the old file or the new one,
    never a torn write (BEHAVIOUR §2)."""
    target = Path(csv_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    bak = target.with_suffix(target.suffix + ".bak")
    tmp = target.with_suffix(target.suffix + ".tmp")
    if target.exists():
        shutil.copy2(target, bak)
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    try:
        fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class RosterWriteError(Exception):
    """Raised when a roster write cannot proceed (file changed on disk, IO)."""


def mutate_roster(csv_path, mutate, expected: list[list[str]] | None = None) -> RosterLoad:
    """Apply *mutate* to the roster on disk and reload it.

    Reads the file fresh; if it differs from *expected* (the rows last shown to
    the operator) the mutation is refused — never merge blind (BEHAVIOUR §2).
    *mutate* is ``callable(rows) -> rows`` over the raw cell lists (header
    first). Returns the fresh ``RosterLoad``. A failed write changes nothing.
    """
    current = read_rows(csv_path)
    if current is None:
        raise RosterWriteError("roster missing on disk — reload first")
    if expected is not None and current != expected:
        raise RosterWriteError("roster changed on disk — reload before editing")
    if not current or not _cell(current[0][0]).lower() == HEADER[0]:
        # A header row is always written; synthesise one if the file lacked it.
        current = [list(HEADER_5)] + current
    new_rows = mutate(current)
    if not new_rows:
        raise RosterWriteError("mutation produced an empty roster")
    new_rows = [list(HEADER_5)] + [_pad5(r) for r in new_rows[1:]]
    _atomic_write(csv_path, new_rows)
    return load_races(csv_path)


def _find_row(rows, key) -> int:
    for i, row in enumerate(rows[1:], 1):
        race = RaceInfo(race_no=_cell(row[0]), heat_no=_cell(row[1]),
                        name=_cell(row[2]) if len(row) > 2 else "")
        if race.key == key:
            return i
    return -1


def rename_race(csv_path, key, new_name, expected=None) -> tuple[RosterLoad, str]:
    """Rename a race's label in the roster (F6). Numbers are the key and are not
    editable here. Returns ``(result, source)`` where ``source`` is the value
    written to the ``source`` column (``"edited"``, or ``"sheet"`` for a legacy
    file that had no column)."""
    def _mutate(rows):
        i = _find_row(rows, key)
        if i < 0:
            raise RosterWriteError("race not in roster — reload first")
        rows[i] = _pad5(rows[i])
        rows[i][2] = new_name
        rows[i][3] = "edited"
        return rows
    return mutate_roster(csv_path, _mutate, expected), "edited"


def skip_race(csv_path, key, skip: bool = True, expected=None) -> RosterLoad:
    """Mark a row skipped (or uns-skipped) via the ``status`` column (F11)."""
    def _mutate(rows):
        i = _find_row(rows, key)
        if i < 0:
            raise RosterWriteError("race not in roster — reload first")
        rows[i] = _pad5(rows[i])
        rows[i][4] = "skipped" if skip else ""
        return rows
    return mutate_roster(csv_path, _mutate, expected)


def remove_row(csv_path, key, expected=None) -> RosterLoad:
    """Remove the first roster row matching *key*. For merge (two rows sharing
    one key) use :func:`remove_row_exact` so the correct row is removed."""
    def _mutate(rows):
        i = _find_row(rows, key)
        if i < 0:
            raise RosterWriteError("race not in roster — reload first")
        return [r for j, r in enumerate(rows) if j != i]
    return mutate_roster(csv_path, _mutate, expected)


def remove_row_exact(csv_path, race_no, heat_no, name, expected=None) -> RosterLoad:
    """Remove the specific roster row whose *literal* fields match (merge needs
    to drop one of two rows that share a normalised key, so matching by key is
    ambiguous)."""
    def _mutate(rows):
        for i, row in enumerate(rows):
            if (_cell(row[0]) == _cell(race_no) and _cell(row[1]) == _cell(heat_no)
                    and _cell(row[2]) == _cell(name)):
                return [r for j, r in enumerate(rows) if j != i]
        raise RosterWriteError("race not in roster — reload first")
    return mutate_roster(csv_path, _mutate, expected)


def add_row(csv_path, race_no, heat_no, name, expected=None,
            source: str = "added") -> tuple[RosterLoad, str]:
    """Insert a new race/heat row in numeric order by normalised
    ``(race_no, heat_no)`` (BEHAVIOUR §3); append if the file is not sorted.
    Returns ``(result, outcome)`` where ``outcome`` is ``"ok"`` or
    ``"collision"`` (the key already exists; nothing was written)."""
    new_key = race_key(race_no, heat_no, name)
    def _mutate(rows):
        if _find_row(rows, new_key) >= 0:
            raise _Collision()
        new_row = [race_no, heat_no, name, source, ""]
        data = rows[1:]
        try:
            pos = _sorted_insert_pos(data, new_key)
        except _Unsorted:
            pos = len(data)
        return [rows[0]] + data[:pos] + [new_row] + data[pos:]
    try:
        return mutate_roster(csv_path, _mutate, expected), "ok"
    except _Collision:
        return load_races(csv_path), "collision"


class _Collision(Exception):
    """Internal: the mutation detected an existing key (handled as a result)."""


class _Unsorted(Exception):
    """Internal: the file is not in sorted order, so insert at the end."""


def _numkey(s: str):
    n = _norm(s, drop_h=True)
    return (0, int(n)) if n.isdigit() else (1, n)


def _sort_key(k) -> tuple:
    if k[0] != "num":
        return (1, _numkey(k[1]))
    return (0, _numkey(k[1]), _numkey(k[2]))


def _sorted_insert_pos(data: list[list[str]], new_key) -> int:
    import bisect
    keys = []
    for row in data:
        r = RaceInfo(race_no=_cell(row[0]), heat_no=_cell(row[1]),
                     name=_cell(row[2]) if len(row) > 2 else "")
        keys.append(r.key)
    # Verify the file is genuinely sorted by normalised (race_no, heat_no)
    # (BEHAVIOUR §3); if not, the caller appends instead of sorting the file.
    for a, b in zip(keys, keys[1:]):
        if _sort_key(a) > _sort_key(b):
            raise _Unsorted()
    return bisect.bisect_left([_sort_key(k) for k in keys], _sort_key(new_key))


def add_heat(csv_path, race_no, name, expected=None) -> tuple[RosterLoad, str]:
    """Add the next unused heat of *race_no* directly after its existing heats
    (F2, BEHAVIOUR §3). Returns ``(result, new_heat)``."""
    current = read_rows(csv_path)
    if current is None:
        raise RosterWriteError("roster missing on disk — reload first")
    used = set()
    base = _norm(race_no, drop_h=True)
    for row in current[1:]:
        if _norm(_cell(row[0]), drop_h=True) == base:
            used.add(_norm(_cell(row[1]), drop_h=True))
    heat = 1
    while str(heat) in used:
        heat += 1
    new_key = race_key(race_no, str(heat), name)

    def _mutate(rows):
        data = rows[1:]
        last = -1
        for j, row in enumerate(data):
            if _norm(_cell(row[0]), drop_h=True) == base:
                last = j
        if last < 0:
            raise RosterWriteError("race has no heats in the roster — reload first")
        return [rows[0]] + data[:last + 1] + \
            [[race_no, str(heat), name, "added", ""]] + data[last + 1:]
    result = mutate_roster(csv_path, _mutate, expected)
    return result, str(heat)
