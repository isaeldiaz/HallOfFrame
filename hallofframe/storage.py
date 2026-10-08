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

from .config import DEFAULTS

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
    updated_at        TEXT,                        -- last write (§6.7)
    ended_at          TEXT,
    t_end_monotonic   REAL,
    image_off         INTEGER NOT NULL DEFAULT 0,  -- timing-only race (§6.5)
    reviewed          INTEGER NOT NULL DEFAULT 0,  -- operator closed review (§6.8)
    audio_path        TEXT,                        -- phase 8, unused
    audio_t0_offset_s REAL,                        -- phase 8, unused
    window_before_ms  INTEGER,                     -- phase 5: copied from config
    window_after_ms   INTEGER,                     -- phase 5: copied from config
    CHECK (start_mode   IN ('direct','radio','external')),
    CHECK (viewing_mode IN ('water','screen'))
);

CREATE TABLE IF NOT EXISTS frame (
    id       INTEGER PRIMARY KEY,
    race_id  INTEGER NOT NULL REFERENCES race(id),
    t_ms     INTEGER NOT NULL,                     -- ms since the gun
    t_recv   REAL NOT NULL,
    path     TEXT NOT NULL,
    UNIQUE (race_id, t_ms)
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
    updated_at      TEXT,                          -- last write (§6.7)
    bow_suggested   TEXT,                          -- phase 8, unused
    bow_source      TEXT,                          -- phase 8, unused
    target_ms       INTEGER,                       -- phase 5: selection target
    primary_frame_id INTEGER REFERENCES frame(id), -- phase 5
    UNIQUE (race_id, sequence),
    CHECK (image_flag IS NULL OR image_flag IN ('approximate','missing'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_capture_race ON capture(race_id, sequence);
CREATE INDEX IF NOT EXISTS idx_frame_race ON frame(race_id, t_ms);
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
        if "updated_at" not in cols:
            self._conn.execute("ALTER TABLE race ADD COLUMN updated_at TEXT")
            # Backfill: an existing race's last write is when it was created.
            self._conn.execute("UPDATE race SET updated_at = created_at")
        if "audio_path" not in cols:
            self._conn.execute("ALTER TABLE race ADD COLUMN audio_path TEXT")
        if "audio_t0_offset_s" not in cols:
            self._conn.execute(
                "ALTER TABLE race ADD COLUMN audio_t0_offset_s REAL")
        added_windows = "window_before_ms" not in cols
        if added_windows:
            self._conn.execute("ALTER TABLE race ADD COLUMN window_before_ms INTEGER")
            self._conn.execute("ALTER TABLE race ADD COLUMN window_after_ms INTEGER")
            # Seed every pre-existing race from the current config defaults, so a
            # later config change cannot alter which frames belong to it (plan 5.1).
            self._conn.execute(
                "UPDATE race SET window_before_ms=?, window_after_ms=?",
                (DEFAULTS["capture"]["window_before_ms"],
                 DEFAULTS["capture"]["window_after_ms"]))

        ccols = {r[1] for r in self._conn.execute("PRAGMA table_info(capture)")}
        if "updated_at" not in ccols:
            self._conn.execute("ALTER TABLE capture ADD COLUMN updated_at TEXT")
            # Backfill from the owning race's created_at.
            self._conn.execute(
                "UPDATE capture SET updated_at = "
                "(SELECT r.created_at FROM race r WHERE r.id = capture.race_id)")
        if "bow_suggested" not in ccols:
            self._conn.execute("ALTER TABLE capture ADD COLUMN bow_suggested TEXT")
        if "bow_source" not in ccols:
            self._conn.execute("ALTER TABLE capture ADD COLUMN bow_source TEXT")
        if "target_ms" not in ccols:
            self._conn.execute("ALTER TABLE capture ADD COLUMN target_ms INTEGER")
        if "primary_frame_id" not in ccols:
            self._conn.execute(
                "ALTER TABLE capture ADD COLUMN primary_frame_id INTEGER "
                "REFERENCES frame(id)")

        self._migrate_capture_frame()

    def _migrate_capture_frame(self) -> None:
        """One-time move from the per-crossing ``capture_frame`` table to the
        race-wide ``frame`` table (plan step 5.1).

        All DML runs in one transaction (committed by ``__init__``): insert the
        gun-indexed frames, point each capture at its target and primary frame,
        then drop the old table. Old image files stay where they are — the row
        keeps its existing ``path``."""
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_frame'"
        ).fetchone()
        if exists is None:
            return

        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS frame ("
            "id INTEGER PRIMARY KEY, race_id INTEGER NOT NULL REFERENCES race(id), "
            "t_ms INTEGER NOT NULL, t_recv REAL NOT NULL, path TEXT NOT NULL, "
            "UNIQUE (race_id, t_ms))")

        old_rows = self._conn.execute(
            "SELECT cf.capture_id, cf.t_recv, cf.path, cf.is_primary, "
            "c.race_id, c.t_press, c.delta_used, r.t0_monotonic "
            "FROM capture_frame cf "
            "JOIN capture c ON c.id = cf.capture_id "
            "JOIN race r ON r.id = c.race_id").fetchall()

        for row in old_rows:
            t0 = row["t0_monotonic"]
            if t0 is None:
                continue
            t_ms = round((row["t_recv"] - t0) * 1000)
            self._conn.execute(
                "INSERT OR IGNORE INTO frame (race_id, t_ms, t_recv, path) "
                "VALUES (?,?,?,?)",
                (row["race_id"], t_ms, row["t_recv"], row["path"]))

        # target_ms is derived purely from the press, so every capture gets one.
        self._conn.execute(
            "UPDATE capture SET target_ms = CAST(ROUND((t_press - delta_used - "
            "(SELECT r.t0_monotonic FROM race r WHERE r.id = capture.race_id)) "
            "* 1000) AS INTEGER)")

        for row in old_rows:
            if not row["is_primary"] or row["t0_monotonic"] is None:
                continue
            t_ms = round((row["t_recv"] - row["t0_monotonic"]) * 1000)
            frame = self._conn.execute(
                "SELECT id, path FROM frame WHERE race_id=? AND t_ms=?",
                (row["race_id"], t_ms)).fetchone()
            if frame is not None:
                # primary_image stays a denormalised copy of the chosen frame's
                # path (§6.7). Two old windows can share a t_ms, so the frame's
                # path (the first seen) may differ from this capture's old
                # primary_image; sync it so the two never diverge.
                self._conn.execute(
                    "UPDATE capture SET primary_frame_id=?, primary_image=? "
                    "WHERE id=?",
                    (frame["id"], frame["path"], row["capture_id"]))

        self._conn.execute("DROP TABLE capture_frame")

    # --- write bookkeeping (spec §6.7) ------------------------------------
    def _touch(self, race_id: int | None) -> None:
        """Stamp a write: bump ``race.updated_at`` and the database-wide
        ``meta['db_updated_at']`` to now. The caller holds ``self._lock`` and
        commits; this never opens its own transaction."""
        now = _utcnow()
        if race_id is not None:
            self._conn.execute(
                "UPDATE race SET updated_at=? WHERE id=?", (now, race_id))
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('db_updated_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))

    def last_updated(self, race_id: int | None = None) -> str | None:
        """ISO timestamp of the last write, or None if nothing was ever written.

        With *race_id*, that race's ``updated_at``; without, the database-wide
        ``meta['db_updated_at']`` (spec §6.7)."""
        with self._lock:
            if race_id is None:
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key='db_updated_at'").fetchone()
                return row["value"] if row else None
            row = self._conn.execute(
                "SELECT updated_at FROM race WHERE id=?", (race_id,)).fetchone()
            return row["updated_at"] if row else None

    # --- race -------------------------------------------------------------
    def create_race(self, name, t0_monotonic, t0_wall, start_mode, radio_delay_ms,
                    delta_used, viewing_mode, fps_nominal=None, boot_id=None,
                    notes=None, image_off=0, race_no=None, heat_no=None,
                    window_before_ms=None, window_after_ms=None) -> int:
        with self._lock:
            now = _utcnow()
            cur = self._conn.execute(
                "INSERT INTO race (name, race_no, heat_no, boot_id, t0_monotonic, "
                "t0_wall, start_mode, radio_delay_ms, delta_used, viewing_mode, "
                "fps_nominal, notes, created_at, updated_at, image_off, "
                "window_before_ms, window_after_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, race_no, heat_no, boot_id or current_boot_id(),
                 t0_monotonic, t0_wall, start_mode, radio_delay_ms, delta_used,
                 viewing_mode, fps_nominal, notes, now, now, int(image_off),
                 window_before_ms, window_after_ms))
            self._touch(cur.lastrowid)
            self._conn.commit()
            return cur.lastrowid

    def get_race(self, race_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM race WHERE id=?", (race_id,)).fetchone()

    def open_race(self):
        """The newest race that was never ended (``ended_at IS NULL``), or None.

        Used on startup to offer resuming a race interrupted by a crash/restart
        (plan step 3.5, N4)."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM race WHERE ended_at IS NULL "
                "ORDER BY id DESC LIMIT 1").fetchone()

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

    def race_bundle(self, race_id: int):
        """One race row plus its non-deleted captures sorted by ``elapsed_s``.

        A single snapshot under the lock, so export/web can never observe a torn
        race (§6.8). Returns ``(race_row, captures)``; ``race_row`` is None for
        an unknown id."""
        with self._lock:
            race = self._conn.execute(
                "SELECT * FROM race WHERE id=?", (race_id,)).fetchone()
            captures = self._conn.execute(
                "SELECT * FROM capture WHERE race_id=? AND deleted=0 "
                "ORDER BY elapsed_s", (race_id,)).fetchall()
        return race, list(captures)

    def all_bundles(self):
        """Yield ``(race, captures)`` for every race, oldest first (§6.8).

        Every race is yielded — one with no crossings yields an empty capture
        list rather than being skipped — so the two whole-database exporters and
        the web index can never diverge on ordering or filtering."""
        for race in self.all_races():
            yield self.race_bundle(race["id"])

    def race_identity_rows(self):
        """Every race's ``(id, race_no, heat_no, name)`` identity fields.

        The normalisation/key comparison lives in ``roster.py`` (``race_key``);
        storage only exposes the raw rows so it never imports the roster module
        (spec §6.7)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, race_no, heat_no, name FROM race").fetchall()

    def rename_race_ids(self, race_ids, new_name: str) -> int:
        """Set ``race.name`` for the given ids in one commit and touch each.

        The caller (``roster.rename_races``) selects the ids by normalised key;
        storage stays free of roster knowledge. Returns the number changed."""
        with self._lock:
            for rid in race_ids:
                self._conn.execute(
                    "UPDATE race SET name=? WHERE id=?", (new_name, rid))
                self._touch(rid)
            self._conn.commit()
            return len(race_ids)

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
            self._touch(race_id)
            self._conn.commit()
            return True

    def mark_race_ended(self, race_id: int, t_end_mono: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE race SET ended_at=?, t_end_monotonic=? WHERE id=?",
                (_utcnow(), t_end_mono, race_id))
            self._touch(race_id)
            self._conn.commit()

    def mark_race_reviewed(self, race_id: int) -> None:
        """Flag a race as reviewed once the operator closes its review screen,
        so the web index publishes only reviewed races (§6.8)."""
        with self._lock:
            self._conn.execute(
                "UPDATE race SET reviewed=1 WHERE id=?", (race_id,))
            self._touch(race_id)
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
            self._touch(race_id)
            self._conn.commit()
            return True

    def mark_race_reconstructed(self, race_id: int, t0_reconstructed_mono: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE race SET t0_reconstructed=1, t0_monotonic=? WHERE id=?",
                (t0_reconstructed_mono, race_id))
            self._touch(race_id)
            self._conn.commit()

    def set_race_audio(self, race_id: int, path, offset) -> None:
        """Attach a race's recorded voice note (phase 8; unused today).

        ``path`` is the WAV's path (relative to the data root) and ``offset`` is
        the seconds from ``t0`` to its first sample."""
        with self._lock:
            self._conn.execute(
                "UPDATE race SET audio_path=?, audio_t0_offset_s=? WHERE id=?",
                (path, offset, race_id))
            self._touch(race_id)
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
                       bow_number=None, notes=None, target_ms=None) -> int:
        with self._lock:
            now = _utcnow()
            cur = self._conn.execute(
                "INSERT INTO capture (race_id, sequence, t_press, t_press_wall, "
                "elapsed_s, delta_used, image_flag, debounce_suspect, bow_number, "
                "notes, updated_at, target_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (race_id, sequence, t_press, t_press_wall, elapsed_s, delta_used,
                 image_flag, debounce_suspect, bow_number, notes, now, target_ms))
            self._touch(race_id)
            self._conn.commit()
            return cur.lastrowid

    def update_capture(self, capture_id, **fields) -> None:
        allowed = {"bow_number", "primary_image", "image_flag", "deleted", "notes",
                   "target_ms", "primary_frame_id"}
        sets = []
        vals = []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"cannot update column {k}")
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return
        with self._lock:
            row = self._conn.execute(
                "SELECT race_id FROM capture WHERE id=?", (capture_id,)).fetchone()
            race_id = row["race_id"] if row else None
            vals.append(_utcnow())      # capture.updated_at
            vals.append(capture_id)
            self._conn.execute(
                f"UPDATE capture SET {', '.join(sets)}, updated_at=? WHERE id=?",
                vals)
            self._touch(race_id)
            self._conn.commit()

    def set_crossing_time(self, capture_id: int, elapsed_s: float) -> bool:
        """Rewrite a crossing's elapsed time and the derived press timestamps.

        ``elapsed_s`` is the source of truth: ``t_press`` and ``t_press_wall``
        are recomputed from the race's ``t0_monotonic``/``t0_wall`` so all three
        stay mutually consistent. Returns False if the capture or its race's
        monotonic origin is missing (e.g. a reconstructed race without a t0).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT c.race_id, r.t0_monotonic, r.t0_wall FROM capture c "
                "JOIN race r ON r.id = c.race_id WHERE c.id = ?",
                (capture_id,)).fetchone()
            if row is None or row["t0_monotonic"] is None:
                return False
            t_press = row["t0_monotonic"] + elapsed_s
            t_press_wall = (row["t0_wall"] + elapsed_s
                            if row["t0_wall"] is not None else elapsed_s)
            self._conn.execute(
                "UPDATE capture SET elapsed_s=?, t_press=?, t_press_wall=?, "
                "updated_at=? WHERE id=?",
                (elapsed_s, t_press, t_press_wall, _utcnow(), capture_id))
            self._touch(row["race_id"])
            self._conn.commit()
            return True

    def restore_capture(self, capture_id: int) -> None:
        """Undo a soft delete: set ``deleted=0`` (plan step 7.1).

        Deletion is soft (``deleted=1``), so restoring is a plain flip; the row
        and its sequence were never removed (spec §6.7)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT race_id FROM capture WHERE id=?", (capture_id,)).fetchone()
            if row is None:
                return
            self._conn.execute(
                "UPDATE capture SET deleted=0, updated_at=? WHERE id=?",
                (_utcnow(), capture_id))
            self._touch(row["race_id"])
            self._conn.commit()

    def clone_capture(self, capture_id: int) -> int | None:
        """Duplicate a crossing as a NEW row (plan step 7.1, spec §13.2).

        Copies ``t_press``, ``t_press_wall``, ``elapsed_s``, ``delta_used``,
        ``target_ms``, ``primary_frame_id``, ``primary_image`` and ``image_flag``
        so the clone shares the parent's frames through ``target_ms``;
        ``bow_number`` and ``notes`` are left empty and ``debounce_suspect`` is
        reset. The new ``sequence`` is ``MAX(sequence)+1`` for the race including
        soft-deleted rows, so numbers are never reused (§6.7). Returns the new
        id, or None when the source is unknown."""
        with self._lock:
            src = self._conn.execute(
                "SELECT * FROM capture WHERE id=?", (capture_id,)).fetchone()
            if src is None:
                return None
            race_id = src["race_id"]
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM capture WHERE race_id=?",
                (race_id,)).fetchone()["n"]
            now = _utcnow()
            cur = self._conn.execute(
                "INSERT INTO capture (race_id, sequence, t_press, t_press_wall, "
                "elapsed_s, delta_used, target_ms, primary_frame_id, primary_image, "
                "image_flag, bow_number, notes, debounce_suspect, deleted, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (race_id, seq, src["t_press"], src["t_press_wall"], src["elapsed_s"],
                 src["delta_used"], src["target_ms"], src["primary_frame_id"],
                 src["primary_image"], src["image_flag"], None, None, 0, 0, now))
            self._touch(race_id)
            self._conn.commit()
            return cur.lastrowid

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

    # --- frame ------------------------------------------------------------
    def frame_exists(self, race_id: int, t_ms: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM frame WHERE race_id=? AND t_ms=?",
                (race_id, t_ms)).fetchone()
            return row is not None

    def insert_frames(self, rows) -> list:
        """Insert gun-indexed frames, one transaction (plan step 5.1).

        *rows* is a list of ``(race_id, t_ms, t_recv, path)`` tuples. Each is
        ``INSERT OR IGNORE``-d so a frame already stored (same race and t_ms) is
        left untouched; the frame rows for the given pairs are then returned,
        ordered by ``t_ms`` — new and pre-existing alike."""
        rows = list(rows)
        if not rows:
            return []
        pairs = sorted({(r[0], r[1]) for r in rows}, key=lambda p: (p[1], p[0]))
        with self._lock:
            for race_id, t_ms, t_recv, path in rows:
                self._conn.execute(
                    "INSERT OR IGNORE INTO frame (race_id, t_ms, t_recv, path) "
                    "VALUES (?,?,?,?)", (race_id, t_ms, t_recv, path))
            for race_id in {r[0] for r in rows}:
                self._touch(race_id)
            out = []
            for race_id, t_ms in pairs:
                row = self._conn.execute(
                    "SELECT * FROM frame WHERE race_id=? AND t_ms=?",
                    (race_id, t_ms)).fetchone()
                if row is not None:
                    out.append(row)
            self._conn.commit()
            return out

    def frames_for_capture(self, capture_id: int) -> list:
        """The race's frames within the capture's selection window, by t_ms.

        A frame can belong to several crossings; nothing is copied per capture
        (plan step 5.1)."""
        with self._lock:
            return self._conn.execute(
                "SELECT f.* FROM frame f "
                "JOIN capture c ON c.race_id = f.race_id "
                "JOIN race r ON r.id = c.race_id "
                "WHERE c.id=? AND c.target_ms IS NOT NULL "
                "AND f.t_ms BETWEEN c.target_ms - r.window_before_ms "
                "AND c.target_ms + r.window_after_ms "
                "ORDER BY f.t_ms", (capture_id,)).fetchall()

    def set_primary(self, capture_id: int, frame_id: int) -> None:
        """Promote *frame_id* to the capture's primary; ``primary_image`` stays
        a denormalised copy of that frame's path so export/web/UI are unchanged."""
        with self._lock:
            row = self._conn.execute(
                "SELECT f.path AS path, f.race_id AS race_id, c.race_id AS cap_race "
                "FROM frame f JOIN capture c ON c.id=? WHERE f.id=?",
                (capture_id, frame_id)).fetchone()
            if row is None or row["race_id"] != row["cap_race"]:
                return
            self._conn.execute(
                "UPDATE capture SET primary_frame_id=?, primary_image=?, "
                "updated_at=? WHERE id=?",
                (frame_id, row["path"], _utcnow(), capture_id))
            self._touch(row["race_id"])
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
