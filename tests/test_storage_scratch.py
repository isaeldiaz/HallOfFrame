"""Scratchpad table, bow writes, and the [scratchpad] config section.

The live bow-number scratchpad (spec §13.3) stores typed entries in their own
table with never-reused ordinals; ``apply_bows`` is the one-transaction path the
live writer uses to push scratch text onto crossings.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hallofframe import storage as storage_mod
from hallofframe.config import Config


class _Clock:
    """Strictly increasing stand-in for ``storage._utcnow`` (one tick/call)."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"2024-01-01T00:00:{self.n:02d}.000000+00:00"


def _race(storage):
    return storage.create_race("R", 0.0, 0.0, "direct", 0.0, 0.0, "screen")


def _cap(storage, race_id, seq):
    return storage.insert_capture(
        race_id, seq, float(seq), float(seq), float(seq), 0.0)


def test_section_default():
    assert Config(data={}, path=Path("x")).section("scratchpad") == {"enabled": False}


def test_insert_scratch_ordinals(storage):
    rid = _race(storage)
    a = storage.insert_scratch(rid, "14", 1000.0)
    b = storage.insert_scratch(rid, "7", 1001.0)
    rows = storage.scratch_entries(rid, include_deleted=True)
    assert [r["ordinal"] for r in rows] == [1, 2]
    assert [r["text"] for r in rows] == ["14", "7"]
    assert [r["deleted"] for r in rows] == [0, 0]
    assert rows[0]["t_typed_wall"] == 1000.0
    assert rows[1]["t_typed_wall"] == 1001.0
    assert a != b


def test_ordinal_never_reused(storage):
    rid = _race(storage)
    storage.insert_scratch(rid, "7", 1.0)
    storage.insert_scratch(rid, "8", 2.0)
    deleted = storage.delete_last_scratch(rid)
    assert deleted is not None
    storage.insert_scratch(rid, "9", 3.0)
    rows = storage.scratch_entries(rid, include_deleted=True)
    assert [r["ordinal"] for r in rows] == [1, 2, 3]
    assert [r["deleted"] for r in rows] == [0, 1, 0]
    assert [r["ordinal"] for r in storage.scratch_entries(rid)] == [1, 3]


def test_delete_last_none(storage):
    rid = _race(storage)
    assert storage.delete_last_scratch(rid) is None


def test_scratch_counts(storage):
    rid = _race(storage)
    storage.insert_scratch(rid, "1", 1.0)
    storage.insert_scratch(rid, "2", 2.0)
    a = _cap(storage, rid, 1)
    _cap(storage, rid, 2)
    _cap(storage, rid, 3)
    storage.update_capture(a, deleted=1)
    assert storage.scratch_counts(rid) == (2, 2)


def test_set_bow(storage):
    rid = _race(storage)
    cap = _cap(storage, rid, 1)
    storage.set_bow(cap, "14", "manual")
    row = storage.capture(cap)
    assert row["bow_number"] == "14"
    assert row["bow_source"] == "manual"
    assert row["t_press"] == 1.0
    assert row["elapsed_s"] == 1.0


def test_apply_bows(storage):
    rid = _race(storage)
    a = _cap(storage, rid, 1)
    b = _cap(storage, rid, 2)
    c = _cap(storage, rid, 3)
    assert storage.apply_bows([(a, "14"), (b, None), (c, "3")]) == 3
    ra, rb, rc = (storage.capture(x) for x in (a, b, c))
    assert (ra["bow_number"], ra["bow_source"]) == ("14", "live")
    assert (rb["bow_number"], rb["bow_source"]) == (None, None)
    assert (rc["bow_number"], rc["bow_source"]) == ("3", "live")


def test_apply_bows_empty(storage):
    assert storage.apply_bows([]) == 0


def test_apply_bows_touches_race(storage, monkeypatch):
    monkeypatch.setattr(storage_mod, "_utcnow", _Clock())
    rid = _race(storage)
    cap = _cap(storage, rid, 1)
    before = storage.last_updated(rid)
    storage.apply_bows([(cap, "14")])
    assert storage.last_updated(rid) != before


def test_update_capture_allows_bow_source(storage):
    rid = _race(storage)
    cap = _cap(storage, rid, 1)
    storage.update_capture(cap, bow_source="manual")
    assert storage.capture(cap)["bow_source"] == "manual"
    with pytest.raises(ValueError):
        storage.update_capture(cap, bow_suggested="9")


def test_schema_on_existing_db(data_root):
    storage_mod.Storage(data_root).close()
    st = storage_mod.Storage(data_root)
    try:
        assert st.scratch_entries(1) == []
    finally:
        st.close()
