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


def recorded_keys(storage) -> set:
    """Distinct normalised keys already stored, so the UI can gray out races
    that have already been run (still overwritable). Identity is the
    ``(race_no, heat_no)`` pair (BEHAVIOUR §1); the name is not part of it.

    Step 1.4f: the key logic lives here, not in ``storage.py`` — storage only
    exposes raw ``race_identity_rows()`` so it never imports this module."""
    return {race_key(r["race_no"], r["heat_no"], r["name"])
            for r in storage.race_identity_rows()}


def rename_races(storage, key, new_name: str) -> int:
    """Update ``race.name`` for every recorded race matching the normalised key
    (the explicit *Also update recorded race* action). Returns the number of
    rows changed. Never touches the roster. The key comparison lives here
    (step 1.4f); ``storage.rename_race_ids`` performs the write."""
    ids = [r["id"] for r in storage.race_identity_rows()
           if race_key(r["race_no"], r["heat_no"], r["name"]) == key]
    return storage.rename_race_ids(ids, new_name)


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


def add_row(csv_path, race_no, heat_no, name, after_key=None, expected=None,
            source: str = "added") -> tuple[RosterLoad, str]:
    """Insert a new race/heat row after *after_key*, else append (BEHAVIOUR §3).

    The display order is file order (spec: no sorting); *after_key* is the key
    of the row the new one follows. Returns ``(result, outcome)`` where
    ``outcome`` is ``"ok"`` or ``"collision"`` (the key already exists; nothing
    was written)."""
    new_key = race_key(race_no, heat_no, name)

    class _KeyExists(Exception):
        """Internal: the mutation detected an existing key."""

    def _mutate(rows):
        if _find_row(rows, new_key) >= 0:
            raise _KeyExists()
        new_row = [race_no, heat_no, name, source, ""]
        data = rows[1:]
        pos = len(data)
        if after_key is not None:
            i = _find_row(rows, after_key)
            if i >= 0:
                pos = i
        return [rows[0]] + data[:pos] + [new_row] + data[pos:]
    try:
        return mutate_roster(csv_path, _mutate, expected), "ok"
    except _KeyExists:
        return load_races(csv_path), "collision"


class Roster:
    """The roster CSV and the five race-day operations (plan step 2.3).

    One Qt-free owner of the file: the main window only forwards. ``rows`` is
    the raw cell list last loaded and is passed as ``expected`` to every write,
    so a mutation whose file has changed underneath the operator is refused with
    ``RosterWriteError`` (BEHAVIOUR §2). Display order is file order — nothing
    here sorts. ``races`` drops duplicate keys (first wins) exactly as
    ``load_races`` reports them.
    """

    def __init__(self, path: str | None):
        self.path: str | None = path
        self.result: RosterLoad = RosterLoad(path=str(path) if path else "")
        self.races: list[RaceInfo] = []
        self.rows: list[list[str]] | None = None
        if path:
            self.load(path)

    def load(self, path: str | None = None) -> RosterLoad:
        """Re-read the CSV (optionally switching to *path*) and remember the raw
        rows for the next optimistic-concurrency check (BEHAVIOUR §2, §4)."""
        if path is not None:
            self.path = path
        self.result = load_races(self.path)
        self.races = self.result.races
        self.rows = read_rows(self.path) if self.path else None
        return self.result

    def skipped_keys(self) -> set:
        """Normalised keys of rows whose ``status`` column is ``skipped`` (F11).

        A legacy 3-column file has no status column, so nothing is skipped."""
        skipped: set = set()
        for row in (self.rows or [])[1:]:
            if len(row) > 4 and _cell(row[4]).lower() == "skipped":
                race = RaceInfo(race_no=_cell(row[0]),
                                heat_no=_cell(row[1]) if len(row) > 1 else "",
                                name=_cell(row[2]) if len(row) > 2 else "")
                skipped.add(race.key)
        return skipped

    def skip(self, key, skip: bool) -> RosterLoad:
        """Mark a row skipped (or un-skip it) via the ``status`` column (F11)."""
        skip_race(self.path, key, skip=skip, expected=self.rows)
        return self.load()

    def move(self, key, delta: int) -> RosterLoad:
        """Move a data row one place: *delta* -1 up, +1 down (plan step 2.4).

        Swaps the row with its neighbour in file order and refuses at either end
        with ``RosterWriteError``; the file is never re-sorted."""
        def _mutate(rows):
            i = _find_row(rows, key)
            if i < 0:
                raise RosterWriteError("race not in roster — reload first")
            j = i + delta
            if j < 1 or j >= len(rows):
                raise RosterWriteError("cannot move past the end of the roster")
            rows = [list(r) for r in rows]
            rows[i], rows[j] = rows[j], rows[i]
            return rows
        mutate_roster(self.path, _mutate, self.rows)
        return self.load()

    def add(self, race_no, heat_no, name,
            after_key=None) -> tuple[RosterLoad, str]:
        """Add a race row after *after_key* (else append). ``outcome`` is
        ``"ok"`` or ``"collision"`` when the key already exists (BEHAVIOUR §3)."""
        _result, outcome = add_row(self.path, race_no, heat_no, name,
                                   after_key=after_key, expected=self.rows)
        return self.load(), outcome

    def rename(self, key, new_name) -> RosterLoad:
        """Rename a race's label (F6); the numbers are the key, not editable."""
        rename_race(self.path, key, new_name, expected=self.rows)
        return self.load()

    def write_example(self) -> None:
        """Write the starter roster if the operator asks for one (WP4)."""
        write_example(self.path)
        self.load()
