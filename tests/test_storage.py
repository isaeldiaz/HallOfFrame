"""Storage write bookkeeping and bundles (plan step 1.4/1.6; spec §6.7).

Every method that writes a race or a capture stamps ``race.updated_at`` and the
database-wide ``meta['db_updated_at']`` via ``Storage._touch``. ``last_updated``
exposes both, and ``race_bundle``/``all_bundles`` are the single ordering path
shared by the CSV/HTML exporters and the web index.

``_utcnow`` is patched to a strictly increasing fake so "the timestamp changed"
is deterministic without sleeping.
"""
from __future__ import annotations

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


def _make_race(storage, name="R", race_no="101", heat_no="1") -> int:
    return storage.create_race(
        name, t0_monotonic=1000.0, t0_wall=1000.0, start_mode="direct",
        radio_delay_ms=0.0, delta_used=0.0, viewing_mode="screen",
        race_no=race_no, heat_no=heat_no)


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
    cap = storage.insert_capture(rid, 1, 1000.0, 1000.0, 0.0, 0.0)
    frame = storage.insert_frame(cap, 1000.0, 0.0, "races/r/a.jpg", is_primary=1)
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
        ("insert_frame",
         lambda: storage.insert_frame(cap, 1000.0, 1.0, "races/r/b.jpg")),
        ("set_primary", lambda: storage.set_primary(cap, frame)),
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


def test_all_bundles_yields_oldest_first(storage, clock):
    first = _make_race(storage, name="A", race_no="101", heat_no="1")
    second = _make_race(storage, name="B", race_no="102", heat_no="1")
    bundles = list(storage.all_bundles())
    assert [race["id"] for race, _ in bundles] == [first, second]
    # recorded_keys shares the same identity rows (step 1.4f).
    assert recorded_keys(storage) == {("num", "101", "1"), ("num", "102", "1")}
