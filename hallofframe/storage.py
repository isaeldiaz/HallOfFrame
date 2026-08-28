"""Persistence (spec §6.7).

SQLite with WAL + synchronous=FULL so a crash cannot lose a committed result
(N4). Foreign keys are enabled on EVERY connection. Commits happen on a single
writer thread, never on the trigger path (§6.5). Deletion is soft
(``deleted=1``); sequences are never reused.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS race (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    race_no           TEXT,
    heat_no           TEXT,
    boot_id           TEXT NOT NULL,
    t0_monotonic      REAL NOT NULL,
    t0_wall           REAL NOT NULL,
    t0_reconstructed  INTEGER NOT NULL DEFAULT 0,
    start_mode        TEXT NOT NULL,
    radio_delay_ms    REAL NOT NULL DEFAULT 0,
    delta_used        REAL NOT NULL,
    viewing_mode      TEXT NOT NULL,
    fps_nominal       REAL,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    ended_at          TEXT,
    t_end_monotonic   REAL,
    image_off         INTEGER NOT NULL DEFAULT 0,  -- timing-only race (§6.5)
    reviewed          INTEGER NOT NULL DEFAULT 0,  -- operator closed review (§6.8)
    CHECK (start_mode   IN ('direct','radio','external')),
    CHECK (viewing_mode IN ('water','screen'))
);

CREATE TABLE IF NOT EXISTS capture (
    id              INTEGER PRIMARY KEY,
    race_id         INTEGER NOT NULL REFERENCES race(id),
    sequence        INTEGER NOT NULL,
    t_press         REAL NOT NULL,
    t_press_wall    REAL NOT NULL,
    elapsed_s       REAL NOT NULL,
    delta_used      REAL NOT NULL,
    bow_number      TEXT,
    primary_image   TEXT,
    image_flag      TEXT,
    debounce_suspect INTEGER NOT NULL DEFAULT 0,
    deleted         INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    UNIQUE (race_id, sequence),
    CHECK (image_flag IS NULL OR image_flag IN ('approximate','missing'))
);

CREATE TABLE IF NOT EXISTS capture_frame (
    id              INTEGER PRIMARY KEY,
    capture_id      INTEGER NOT NULL REFERENCES capture(id),
    t_recv          REAL NOT NULL,
    offset_ms       REAL NOT NULL,
    path            TEXT NOT NULL,
    is_primary      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_capture_race ON capture(race_id, sequence);
CREATE INDEX IF NOT EXISTS idx_frame_capture ON capture_frame(capture_id, t_recv);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_primary ON capture_frame(capture_id)
    WHERE is_primary = 1;
"""


def current_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


class Storage:
    def __init__(self, data_root: Path, event_name: str = "event"):
        self.data_root = Path(data_root)
        self.event_name = event_name or "event"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_root / f"{self.event_name}.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns added after the initial schema (spec §6.7 N4: survive an
        upgrade with an existing on-disk database)."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(race)")}
        if "ended_at" not in cols:
            self._conn.execute("ALTER TABLE race ADD COLUMN ended_at TEXT")
        if "t_end_monotonic" not in cols:
            self._conn.execute("ALTER TABLE race ADD COLUMN t_end_monotonic REAL")
        if "image_off" not in cols:
            self._conn.execute(
                "ALTER TABLE race ADD COLUMN image_off INTEGER NOT NULL DEFAULT 0")
        if "race_no" not in cols:
            self._conn.execute("ALTER TABLE race ADD COLUMN race_no TEXT")
        if "heat_no" not in cols:
            self._conn.execute("ALTER TABLE race ADD COLUMN heat_no TEXT")
        if "reviewed" not in cols:
            self._conn.execute(
                "ALTER TABLE race ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")

    # --- race -------------------------------------------------------------
    def create_race(self, name, t0_monotonic, t0_wall, start_mode, radio_delay_ms,
                    delta_used, viewing_mode, fps_nominal=None, boot_id=None,
                    notes=None, image_off=0, race_no=None, heat_no=None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO race (name, race_no, heat_no, boot_id, t0_monotonic, "
                "t0_wall, start_mode, radio_delay_ms, delta_used, viewing_mode, "
                "fps_nominal, notes, created_at, image_off) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, race_no, heat_no, boot_id or current_boot_id(),
                 t0_monotonic, t0_wall, start_mode, radio_delay_ms, delta_used,
                 viewing_mode, fps_nominal, notes, _utcnow(), int(image_off)))
            self._conn.commit()
            return cur.lastrowid

    def get_race(self, race_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM race WHERE id=?", (race_id,)).fetchone()

    def list_races(self, reviewed_only: bool = False):
        """Every race, newest first. With *reviewed_only*, only races whose
        operator closed review (``reviewed=1``) — the ones the web index shows."""
        where = " WHERE reviewed=1" if reviewed_only else ""
        with self._lock:
            return self._conn.execute(
                "SELECT id, name, created_at FROM race" + where
                + " ORDER BY id DESC").fetchall()

    def all_races(self):
        """Every race row, oldest first (for a whole-database export)."""
        with self._lock:
            return self._conn.execute("SELECT * FROM race ORDER BY id").fetchall()

    def race_keys(self) -> set:
        """Distinct normalised keys already stored, so the UI can gray out races
        that have already been run (still overwritable). Identity is the
        ``(race_no, heat_no)`` pair (BEHAVIOUR §1); the name is not part of it.
        The SQL is unchanged and keys are built in Python via ``race_key()`` so
        this can never drift from ``RaceInfo.key``."""
        from .races import race_key
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT race_no, heat_no, name FROM race").fetchall()
            return {race_key(r["race_no"], r["heat_no"], r["name"]) for r in rows}

    def rename_races(self, key, new_name: str) -> int:
        """Update ``race.name`` for every recorded race matching the normalised
        key (the explicit *Also update recorded race* action). Returns the
        number of rows changed. Never touches the roster."""
        from .races import race_key
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, race_no, heat_no, name FROM race").fetchall()
            ids = [r["id"] for r in rows
                   if race_key(r["race_no"], r["heat_no"], r["name"]) == key]
            for rid in ids:
                self._conn.execute(
                    "UPDATE race SET name=? WHERE id=?", (new_name, rid))
            self._conn.commit()
            return len(ids)

    def identify_race(self, race_id: int, race_no, heat_no, name) -> bool:
        """WP7: set number/heat/name on an unlisted race — permitted exactly
        once, only when the race has no number yet (no roster row was involved).
        Returns True if applied."""
        with self._lock:
            row = self._conn.execute(
                "SELECT race_no, heat_no FROM race WHERE id=?", (race_id,)).fetchone()
            if row is None or (row["race_no"] or ""):
                return False
            self._conn.execute(
                "UPDATE race SET race_no=?, heat_no=?, name=? WHERE id=?",
                (race_no or None, heat_no or None, name, race_id))
            self._conn.commit()
            return True

    def repoint_race(self, race_id: int, race_no, heat_no, name=None) -> None:
        """WP6: restyle a recorded race's key to a duplicate row's literal
        formatting (``0102`` -> ``102``). Refuses a normalised-key change — merge
        may only restyle, never move a race. Optionally updates ``name``."""
        from .races import race_key
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM race WHERE id=?", (race_id,)).fetchone()
            if row is None:
                return
            old_key = race_key(row["race_no"], row["heat_no"], row["name"])
            new_key = race_key(race_no, heat_no, row["name"])
            if old_key != new_key:
                raise ValueError("repoint may only restyle a key, never move a race")
            if name is None:
                name = row["name"]
            self._conn.execute(
                "UPDATE race SET race_no=?, heat_no=?, name=? WHERE id=?",
                (race_no or None, heat_no or None, name, race_id))
            self._conn.commit()

    def mark_race_ended(self, race_id: int, t_end_mono: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE race SET ended_at=?, t_end_monotonic=? WHERE id=?",
                (_utcnow(), t_end_mono, race_id))
            self._conn.commit()

    def mark_race_reviewed(self, race_id: int) -> None:
        """Flag a race as reviewed once the operator closes its review screen,
        so the web index publishes only reviewed races (§6.8)."""
        with self._lock:
            self._conn.execute(
                "UPDATE race SET reviewed=1 WHERE id=?", (race_id,))
            self._conn.commit()

    def set_start_time(self, race_id: int, new_t0_wall: float) -> bool:
        """Set the race's wall-clock start time and shift every crossing's
        wall-clock time by the same delta. Relative (elapsed) times and the
        attached images are untouched. Returns False if the race is missing or
        has no recorded start time."""
        with self._lock:
            row = self._conn.execute(
                "SELECT t0_wall FROM race WHERE id=?", (race_id,)).fetchone()
            if row is None or row["t0_wall"] is None:
                return False
            delta = new_t0_wall - row["t0_wall"]
            self._conn.execute(
                "UPDATE race SET t0_wall=? WHERE id=?", (new_t0_wall, race_id))
            self._conn.execute(
                "UPDATE capture SET t_press_wall = t_press_wall + ? WHERE race_id=?",
                (delta, race_id))
            self._conn.commit()
            return True

    def mark_race_reconstructed(self, race_id: int, t0_reconstructed_mono: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE race SET t0_reconstructed=1, t0_monotonic=? WHERE id=?",
                (t0_reconstructed_mono, race_id))
            self._conn.commit()

    # --- capture ----------------------------------------------------------
    def next_sequence(self, race_id: int) -> int:
        """Including soft-deleted rows, so numbers are never reused (§6.7)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM capture WHERE race_id=?",
                (race_id,)).fetchone()
            return int(row["n"])

    def insert_capture(self, race_id, sequence, t_press, t_press_wall, elapsed_s,
                       delta_used, image_flag=None, debounce_suspect=0,
                       bow_number=None, notes=None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO capture (race_id, sequence, t_press, t_press_wall, "
                "elapsed_s, delta_used, image_flag, debounce_suspect, bow_number, "
                "notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (race_id, sequence, t_press, t_press_wall, elapsed_s, delta_used,
                 image_flag, debounce_suspect, bow_number, notes))
            self._conn.commit()
            return cur.lastrowid

    def update_capture(self, capture_id, **fields) -> None:
        allowed = {"bow_number", "primary_image", "image_flag", "deleted", "notes"}
        sets = []
        vals = []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"cannot update column {k}")
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return
        vals.append(capture_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE capture SET {', '.join(sets)} WHERE id=?", vals)
            self._conn.commit()

    def capture(self, capture_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM capture WHERE id=?", (capture_id,)).fetchone()

    def captures_for_race(self, race_id: int, include_deleted: bool = False):
        q = "SELECT * FROM capture WHERE race_id=?"
        if not include_deleted:
            q += " AND deleted=0"
        q += " ORDER BY sequence"
        with self._lock:
            return self._conn.execute(q, (race_id,)).fetchall()

    # --- capture_frame ----------------------------------------------------
    def insert_frame(self, capture_id, t_recv, offset_ms, path, is_primary=0) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO capture_frame (capture_id, t_recv, offset_ms, path, "
                "is_primary) VALUES (?,?,?,?,?)",
                (capture_id, t_recv, offset_ms, path, is_primary))
            self._conn.commit()
            return cur.lastrowid

    def frames_for_capture(self, capture_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM capture_frame WHERE capture_id=? ORDER BY t_recv",
                (capture_id,)).fetchall()

    def set_primary(self, capture_id: int, frame_id: int) -> None:
        """Promote *frame_id* to primary; demote others (unique index enforces)."""
        with self._lock:
            self._conn.execute(
                "UPDATE capture_frame SET is_primary=0 WHERE capture_id=?",
                (capture_id,))
            self._conn.execute(
                "UPDATE capture_frame SET is_primary=1 WHERE id=? AND capture_id=?",
                (frame_id, capture_id))
            row = self._conn.execute(
                "SELECT path FROM capture_frame WHERE id=?", (frame_id,)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE capture SET primary_image=? WHERE id=?",
                    (row["path"], capture_id))
            self._conn.commit()

    def integrity_ok(self) -> bool:
        with self._lock:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row and row[0] == "ok")

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()


def _utcnow() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
