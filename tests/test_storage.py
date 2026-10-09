"""Storage write bookkeeping and bundles (plan step 1.4/1.6; spec §6.7).

Every method that writes a race or a capture stamps ``race.updated_at`` and the
database-wide ``meta['db_updated_at']`` via ``Storage._touch``. ``last_updated``
exposes both, and ``race_bundle``/``all_bundles`` are the single ordering path
shared by the CSV/HTML exporters and the web index.

``_utcnow`` is patched to a strictly increasing fake so "the timestamp changed"
is deterministic without sleeping.
"""
from __future__ import annotations

import sqlite3

import pytest

from hallofframe import storage as storage_mod
from hallofframe.roster import recorded_keys, rename_races


class _Clock:
    """Strictly increasing stand-in for ``storage._utcnow`` (one tick/call)."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"2024-01-01T00:00:{self.n:02d}.000000+00:00"


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(storage_mod, "_utcnow", c)
    return c


def test_read_only_connection_reads_but_never_writes(data_root):
    writer = storage_mod.Storage(data_root, event_name="event")
    rid = _make_race(writer)
    writer.close()

    ro = storage_mod.Storage(data_root, event_name="event", read_only=True)
    try:
        assert ro.get_race(rid)["name"] == "R"
        with pytest.raises(sqlite3.OperationalError):
            ro._conn.execute("INSERT INTO race (name, boot_id, t0_monotonic, "
                             "t0_wall, start_mode, delta_used, viewing_mode, "
                             "created_at) VALUES ('x','b',0,0,'direct',0,"
                             "'screen','now')")
    finally:
        ro.close()


def test_read_only_does_not_create_a_missing_database(data_root):
    # The web process must not create the DB/schema; a missing file is an error.
    with pytest.raises(sqlite3.OperationalError):
        storage_mod.Storage(data_root, event_name="absent", read_only=True)


def _make_race(storage, name="R", race_no="101", heat_no="1",
               window_before_ms=None, window_after_ms=None) -> int:
    return storage.create_race(
        name, t0_monotonic=1000.0, t0_wall=1000.0, start_mode="direct",
        radio_delay_ms=0.0, delta_used=0.0, viewing_mode="screen",
        race_no=race_no, heat_no=heat_no,
        window_before_ms=window_before_ms, window_after_ms=window_after_ms)


def test_last_updated_without_race_id(storage, clock):
    # Nothing written yet: the database-wide stamp is absent.
    assert storage.last_updated() is None
    rid = _make_race(storage)
    # create_race touches both the race row and the global meta row, with the
    # same timestamp.
    assert storage.last_updated(rid) is not None
    assert storage.last_updated() == storage.last_updated(rid)
    # An unknown race id yields None, not the global stamp.
    assert storage.last_updated(999) is None


def test_every_write_method_advances_updated_at(storage, clock):
    rid = _make_race(storage)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 0.0, 0.0, target_ms=0)
    frames = storage.insert_frames([(rid, 0, 1000.0, "races/r/a.jpg")])
    frame_id = frames[0]["id"]
    assert storage.last_updated(rid) == storage.last_updated()

    steps = [
        ("rename_races",
         lambda: rename_races(storage, ("num", "101", "1"), "Renamed")),
        ("mark_race_ended", lambda: storage.mark_race_ended(rid, 2000.0)),
        ("mark_race_reviewed", lambda: storage.mark_race_reviewed(rid)),
        ("set_start_time", lambda: storage.set_start_time(rid, 2000.0)),
        ("mark_race_reconstructed",
         lambda: storage.mark_race_reconstructed(rid, 3000.0)),
        ("insert_capture",
         lambda: storage.insert_capture(rid, 2, 1000.0, 1000.0, 1.0, 0.0)),
        ("update_capture", lambda: storage.update_capture(cap, bow_number="07")),
        ("set_crossing_time", lambda: storage.set_crossing_time(cap, 2.0)),
        ("insert_frames",
         lambda: storage.insert_frames([(rid, 1, 1001.0, "races/r/b.jpg")])),
        ("set_primary", lambda: storage.set_primary(cap, frame_id)),
        ("set_race_audio",
         lambda: storage.set_race_audio(rid, "races/r/audio.wav", 1.5)),
    ]
    for label, op in steps:
        before = storage.last_updated(rid)
        op()
        after = storage.last_updated(rid)
        assert after != before, f"{label} did not bump race.updated_at"
        assert storage.last_updated() == after, \
            f"{label} did not bump meta['db_updated_at']"


def test_identify_race_touches_only_when_applied(storage, clock):
    uid = _make_race(storage, race_no=None, heat_no=None)  # unlisted
    before = storage.last_updated(uid)
    assert storage.identify_race(uid, "124", "1", "D U17 2x") is True
    assert storage.last_updated(uid) != before
    # A second identify is refused and writes nothing.
    stamp = storage.last_updated(uid)
    assert storage.identify_race(uid, "999", "2", "X") is False
    assert storage.last_updated(uid) == stamp


def test_update_capture_sets_capture_updated_at(storage, clock):
    rid = _make_race(storage)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 0.0, 0.0)
    first = storage.capture(cap)["updated_at"]
    assert first is not None
    storage.update_capture(cap, bow_number="07")
    assert storage.capture(cap)["updated_at"] != first


def test_race_bundle_orders_by_elapsed_and_excludes_deleted(storage, clock):
    rid = _make_race(storage)
    c_slow = storage.insert_capture(rid, 1, 1000.0, 1000.0, 9.0, 0.0)
    c_fast = storage.insert_capture(rid, 2, 1000.0, 1000.0, 3.0, 0.0)
    c_mid = storage.insert_capture(rid, 3, 1000.0, 1000.0, 6.0, 0.0)
    storage.update_capture(c_mid, deleted=1)  # soft-deleted, must not appear

    race, captures = storage.race_bundle(rid)
    assert race["id"] == rid
    assert [c["id"] for c in captures] == [c_fast, c_slow]

    assert storage.race_bundle(999) == (None, [])


def test_set_primary_bind_time_shifts_elapsed_and_leaves_press(storage, clock):
    rid = _make_race(storage)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 5.0, 0.0,
                                 target_ms=5000)
    rows = storage.insert_frames([
        (rid, 4900, 1004.9, "races/r/a.jpg"),
        (rid, 4966, 1004.966, "races/r/b.jpg"),
    ])
    a, b = rows
    # The automatic/revert path only moves the primary.
    assert storage.set_primary(cap, a["id"], bind_time=False) == 5.0
    # The operator path shifts elapsed_s by exactly the frame delta.
    new = storage.set_primary(cap, b["id"])
    assert new == pytest.approx(5.0 + (b["t_ms"] - a["t_ms"]) / 1000.0)
    row = storage.capture(cap)
    assert row["elapsed_s"] == pytest.approx(5.066)
    assert row["elapsed_source"] == "frame"
    assert row["t_press"] == 1000.0          # never rewritten
    assert row["t_press_wall"] == 1000.0
    # Re-affirming the same frame does not move the time.
    assert storage.set_primary(cap, b["id"]) == pytest.approx(5.066)


def test_set_primary_refuses_frame_outside_window(storage, clock):
    rid = _make_race(storage, window_before_ms=500, window_after_ms=500)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 5.0, 0.0,
                                 target_ms=5000)
    inside, outside = storage.insert_frames([
        (rid, 4900, 1004.9, "races/r/a.jpg"),
        (rid, 9000, 1009.0, "races/r/b.jpg"),
    ])
    assert storage.set_primary(cap, inside["id"], bind_time=False) == 5.0
    before = storage.capture(cap)

    # A same-race frame outside [4500, 5500] is refused with no write at all.
    assert storage.set_primary(cap, outside["id"]) is None
    after = storage.capture(cap)
    for col in ("primary_frame_id", "primary_image", "elapsed_s",
                "elapsed_source", "updated_at"):
        assert after[col] == before[col], col


def test_set_primary_accepts_frame_on_window_edge(storage, clock):
    rid = _make_race(storage, window_before_ms=500, window_after_ms=500)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 5.0, 0.0,
                                 target_ms=5000)
    before_edge, after_edge, past_after = storage.insert_frames([
        (rid, 4500, 1004.5, "races/r/before.jpg"),
        (rid, 5500, 1005.5, "races/r/after.jpg"),
        (rid, 5501, 1005.501, "races/r/past.jpg"),
    ])
    # Both window edges are inclusive; one ms past the after edge is refused.
    assert storage.set_primary(cap, before_edge["id"], bind_time=False) == 5.0
    assert storage.set_primary(cap, after_edge["id"], bind_time=False) == 5.0
    assert storage.capture(cap)["primary_frame_id"] == after_edge["id"]
    assert storage.set_primary(cap, past_after["id"], bind_time=False) is None
    assert storage.capture(cap)["primary_frame_id"] == after_edge["id"]


def test_set_primary_null_target_skips_window_check(storage, clock):
    # A pre-frame-store row has no target_ms, so no window is known and only
    # the same-race check applies (old data stays editable).
    rid = _make_race(storage, window_before_ms=500, window_after_ms=500)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 5.0, 0.0)
    far = storage.insert_frames([(rid, 9000, 1009.0, "races/r/far.jpg")])[0]
    assert storage.set_primary(cap, far["id"], bind_time=False) == 5.0
    assert storage.capture(cap)["primary_frame_id"] == far["id"]


def test_set_crossing_time_marks_manual_and_leaves_press(storage, clock):
    rid = _make_race(storage)
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 5.0, 0.0)
    assert storage.set_crossing_time(cap, 12.345) is True
    row = storage.capture(cap)
    assert row["elapsed_s"] == pytest.approx(12.345)
    assert row["elapsed_source"] == "manual"
    assert row["t_press"] == 1000.0
    assert row["t_press_wall"] == 1000.0


def test_read_only_export_tolerates_a_pre_migration_database(data_root):
    # The web process opens read-only and never migrates (spec §8), so a DB
    # written before `elapsed_source` existed must still export without raising.
    st = storage_mod.Storage(data_root)
    rid = _make_race(st)
    st.insert_capture(rid, 1, 1000.0, 1000.0, 3.0, 0.0)
    st.close()
    conn = sqlite3.connect(str(data_root / "event.db"))
    conn.execute("ALTER TABLE capture DROP COLUMN elapsed_source")
    conn.commit()
    conn.close()

    from hallofframe.render.clipboard import clipboard_data
    from hallofframe.render.csv import export_csv

    ro = storage_mod.Storage(data_root, event_name="event", read_only=True)
    try:
        tsv, markup = clipboard_data(ro, rid)
        assert "press" in tsv
        out = data_root / "export.csv"
        export_csv(ro, rid, out)
        text = out.read_text(encoding="utf-8")
        assert "elapsed_source" in text.splitlines()[0]
        assert ",press," in text
    finally:
        ro.close()


def test_open_race_returns_newest_unended(storage, clock):
    # No race at all.
    assert storage.open_race() is None
    first = _make_race(storage, name="A", race_no="101", heat_no="1")
    assert storage.open_race()["id"] == first
    second = _make_race(storage, name="B", race_no="102", heat_no="1")
    assert storage.open_race()["id"] == second
    # Ending the newest exposes the older one again.
    storage.mark_race_ended(second, 2000.0)
    assert storage.open_race()["id"] == first
    # Ending everything yields None.
    storage.mark_race_ended(first, 3000.0)
    assert storage.open_race() is None


def test_all_bundles_yields_oldest_first(storage, clock):
    first = _make_race(storage, name="A", race_no="101", heat_no="1")
    second = _make_race(storage, name="B", race_no="102", heat_no="1")
    bundles = list(storage.all_bundles())
    assert [race["id"] for race, _ in bundles] == [first, second]
    # recorded_keys shares the same identity rows (step 1.4f).
    assert recorded_keys(storage) == {("num", "101", "1"), ("num", "102", "1")}


# The schema as it shipped before phase 5, kept here so the migration is tested
# against a database created by the OLD code, not by the new SCHEMA string.
OLD_SCHEMA = """
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
    updated_at        TEXT,
    ended_at          TEXT,
    t_end_monotonic   REAL,
    image_off         INTEGER NOT NULL DEFAULT 0,
    reviewed          INTEGER NOT NULL DEFAULT 0,
    audio_path        TEXT,
    audio_t0_offset_s REAL,
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
    updated_at      TEXT,
    bow_suggested   TEXT,
    bow_source      TEXT,
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

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_capture_race ON capture(race_id, sequence);
CREATE INDEX IF NOT EXISTS idx_frame_capture ON capture_frame(capture_id, t_recv);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_primary ON capture_frame(capture_id)
    WHERE is_primary = 1;
"""


def _tables(conn) -> set:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


# --- UI settings in the meta table (spec §13.3, package A.1) ---------------
def test_setting_round_trip_and_delete(storage):
    assert storage.get_setting("ui.roi") is None
    storage.set_setting("ui.roi", "0.25,0.25,0.5,0.5")
    assert storage.get_setting("ui.roi") == "0.25,0.25,0.5,0.5"
    storage.set_setting("ui.finish_line_x", "0.5")
    assert storage.get_setting("ui.finish_line_x") == "0.5"
    # None deletes the key.
    storage.set_setting("ui.roi", None)
    assert storage.get_setting("ui.roi") is None
    assert storage.get_setting("ui.finish_line_x") == "0.5"


def test_setting_does_not_touch_updated_at(storage, clock):
    _make_race(storage)
    before = storage.last_updated()
    storage.set_setting("ui.roi", "0,0,1,1")
    storage.set_setting("ui.roi", None)
    # A UI setting is not a results change: no ETag churn for viewers.
    assert storage.last_updated() == before


def test_setting_rejects_unprefixed_key(storage):
    with pytest.raises(ValueError):
        storage.set_setting("db_updated_at", "2030-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):
        storage.get_setting("db_updated_at")


def test_setting_read_only_reads_but_cannot_write(data_root):
    writer = storage_mod.Storage(data_root, event_name="event")
    writer.set_setting("ui.roi", "0,0,1,1")
    writer.close()

    ro = storage_mod.Storage(data_root, event_name="event", read_only=True)
    try:
        assert ro.get_setting("ui.roi") == "0,0,1,1"
        with pytest.raises(sqlite3.OperationalError):
            ro.set_setting("ui.roi", "0.5,0.5,0.5,0.5")
    finally:
        ro.close()


def test_migration_uses_the_configured_windows(data_root):
    db = data_root / "event.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO race (name, boot_id, t0_monotonic, t0_wall, start_mode, "
        "radio_delay_ms, delta_used, viewing_mode, created_at) "
        "VALUES ('R','boot',1000.0,1000.0,'direct',0.0,0.0,'screen',"
        "'2024-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()

    st = storage_mod.Storage(data_root, window_before_ms=300,
                             window_after_ms=700)
    try:
        race = st._conn.execute(
            "SELECT window_before_ms, window_after_ms FROM race").fetchone()
        assert (race[0], race[1]) == (300, 700)
    finally:
        st.close()


def test_migration_from_old_schema(data_root):
    db = data_root / "event.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO race (name, boot_id, t0_monotonic, t0_wall, start_mode, "
        "radio_delay_ms, delta_used, viewing_mode, created_at) "
        "VALUES ('R','boot',1000.0,1000.0,'direct',0.0,0.05,'screen',"
        "'2024-01-01T00:00:00+00:00')")
    race_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # t_press 1002.0, delta_used 0.05, t0 1000.0 => target_ms = 1950
    conn.execute(
        "INSERT INTO capture (race_id, sequence, t_press, t_press_wall, "
        "elapsed_s, delta_used) VALUES (?,1,1002.0,1002.0,2.0,0.05)", (race_id,))
    cap_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO capture_frame (capture_id, t_recv, offset_ms, path, "
        "is_primary) VALUES (?,?,?,?,?)",
        (cap_id, 1001.9, -100.0, "races/r/captures/001_w-0100.jpg", 0))
    conn.execute(
        "INSERT INTO capture_frame (capture_id, t_recv, offset_ms, path, "
        "is_primary) VALUES (?,?,?,?,?)",
        (cap_id, 1002.0, 0.0, "races/r/captures/001_w+0000.jpg", 1))
    conn.commit()
    conn.close()

    st = storage_mod.Storage(data_root)
    try:
        assert "frame" in _tables(st._conn)
        assert "capture_frame" not in _tables(st._conn)

        frames = st._conn.execute("SELECT * FROM frame ORDER BY t_ms").fetchall()
        assert [f["t_ms"] for f in frames] == [1900, 2000]
        assert frames[0]["path"] == "races/r/captures/001_w-0100.jpg"
        assert frames[1]["path"] == "races/r/captures/001_w+0000.jpg"

        cap = st.capture(cap_id)
        assert cap["target_ms"] == 1950
        assert cap["elapsed_source"] == "press"  # migration backfill
        assert cap["primary_frame_id"] == frames[1]["id"]
        # primary_image is the denormalised copy of the chosen frame's path (§6.7)
        assert cap["primary_image"] == frames[1]["path"]

        race = st.get_race(race_id)
        assert race["window_before_ms"] == 500
        assert race["window_after_ms"] == 500

        # The new window query sees both migrated frames.
        assert [f["t_ms"] for f in st.frames_for_capture(cap_id)] == [1900, 2000]
    finally:
        st.close()
