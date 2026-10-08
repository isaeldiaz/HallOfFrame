"""Tests for the Kjerbo -> fake-camera feed builder (tools/build_feed.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hallofframe.tools.build_feed import (
    FrameRef, build, collect_from_db, collect_from_dir, select)


def _make_db(root: Path) -> Path:
    """A minimal event DB with two races and sparse, gun-indexed frames."""
    (root / "races" / "r1").mkdir(parents=True)
    (root / "races" / "r2").mkdir(parents=True)
    for i in range(6):
        (root / "races" / "r1" / f"f{i}.jpg").write_bytes(b"a")
    for i in range(4):
        (root / "races" / "r2" / f"g{i}.jpg").write_bytes(b"b")

    db = root / "event.db"
    con = sqlite3.connect(str(db))
    con.execute("create table race (id integer primary key, race_no text)")
    con.execute("create table frame (race_id integer, t_ms integer, path text)")
    con.executemany("insert into race values (?,?)",
                    [(1, "100"), (2, "101")])
    rows = [
        (1, 1000, "races/r1/f0.jpg"),
        (1, 1033, "races/r1/f1.jpg"),
        (1, 1066, "races/r1/f2.jpg"),
        (1, 5000, "races/r1/f3.jpg"),   # new run (gap > 200 ms)
        (1, 5033, "races/r1/f4.jpg"),
        (1, 5066, "races/r1/f5.jpg"),
        (2, 100, "races/r2/g0.jpg"),
        (2, 133, "races/r2/g1.jpg"),
        (2, 166, "races/r2/g2.jpg"),
        (2, 199, "races/r2/g3.jpg"),
    ]
    con.executemany("insert into frame values (?,?,?)", rows)
    con.commit()
    con.close()
    return db


def test_collect_from_db_orders_by_gun_time(tmp_path):
    db = _make_db(tmp_path)
    refs = collect_from_db(db, tmp_path)
    assert [(r.race_id, r.t_ms) for r in refs] == sorted(
        (r.race_id, r.t_ms) for r in refs)
    assert refs[0].path == tmp_path / "races/r1/f0.jpg"
    assert all(r.path.is_file() for r in refs)


def test_collect_from_db_filters_by_id_and_race_no(tmp_path):
    db = _make_db(tmp_path)
    assert {r.race_id for r in collect_from_db(db, tmp_path, ["1"])} == {1}
    assert {r.race_id for r in collect_from_db(db, tmp_path, ["101"])} == {2}
    with pytest.raises(ValueError):
        collect_from_db(db, tmp_path, ["nope"])


def test_select_crossings_every_and_cap(tmp_path):
    db = _make_db(tmp_path)
    refs = collect_from_db(db, tmp_path, ["1"])
    assert [r.t_ms for r in select(refs, crossings=1)] == [1000, 1033, 1066]
    assert [r.t_ms for r in select(refs, crossings=2)] == \
        [1000, 1033, 1066, 5000, 5033, 5066]
    assert [r.t_ms for r in select(refs, every=2)] == [1000, 1066, 5033]
    assert len(select(refs, max_frames=2)) == 2


def test_build_copies_renames_and_manifest(tmp_path):
    db = _make_db(tmp_path)
    refs = collect_from_db(db, tmp_path, ["1"])
    out = tmp_path / "feed"
    n = build(refs, out)
    assert n == 6
    assert sorted(p.name for p in out.glob("*.jpg")) == [
        f"{i:06d}.jpg" for i in range(1, 7)]
    manifest = (out / "manifest.tsv").read_text().splitlines()
    assert manifest[0] == "index\trace_id\tt_ms\tsource"
    assert manifest[1].startswith("000001\t1\t1000\t")
    assert len(manifest) == 7


def test_build_skips_missing_and_needs_force(tmp_path):
    refs = [FrameRef(1, 1, tmp_path / "gone.jpg"),
            FrameRef(1, 2, tmp_path / "here.jpg")]
    (tmp_path / "here.jpg").write_bytes(b"x")
    out = tmp_path / "feed"
    assert build(refs, out) == 1
    with pytest.raises(FileExistsError):
        build(refs, out)
    assert build(refs, out, force=True) == 1


def test_collect_from_dir_orders_by_name(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ["b.jpg", "a.jpg", "c.JPG"]:
        (src / name).write_bytes(b"x")
    (src / "note.txt").write_text("ignore")
    refs = collect_from_dir(src)
    assert [r.path.name for r in refs] == ["a.jpg", "b.jpg", "c.JPG"]
