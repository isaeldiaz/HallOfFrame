"""Gun-indexed frame store (plan step 5.2)."""
from __future__ import annotations

from pathlib import Path

from hallofframe.framestore import FrameStore, nearest
from hallofframe.mjpeg import Frame


def _frame(t_recv: float, jpeg: bytes = b"jpeg") -> Frame:
    return Frame(t_recv, t_recv, 0, jpeg)


def _race(storage) -> int:
    return storage.create_race(
        "R", t0_monotonic=1000.0, t0_wall=1000.0, start_mode="direct",
        radio_delay_ms=0.0, delta_used=0.0, viewing_mode="screen")


def test_t_ms_rounds_to_nearest_ms(storage, data_root):
    store = FrameStore(storage, 1, data_root / "races" / "r", t0=1000.0)
    assert store.t_ms(_frame(1000.0)) == 0
    assert store.t_ms(_frame(1000.0004)) == 0
    assert store.t_ms(_frame(1000.0006)) == 1
    assert store.t_ms(_frame(1000.5)) == 500
    assert store.t_ms(_frame(1001.4994)) == 1499


def test_save_skips_negative_t_ms(storage, data_root):
    rid = _race(storage)
    race_dir = data_root / "races" / "r"
    store = FrameStore(storage, rid, race_dir, t0=1000.0)

    rows = store.save([_frame(999.0), _frame(1000.0), _frame(1000.5)])

    assert [r["t_ms"] for r in rows] == [0, 500]
    files = sorted(p.name for p in (race_dir / "frames").iterdir())
    assert files == ["00000000.jpg", "00000500.jpg"]
    assert not list((race_dir / "frames").glob("*.tmp"))


def test_save_overlapping_window_writes_nothing_new(storage, data_root):
    rid = _race(storage)
    race_dir = data_root / "races" / "r"
    store = FrameStore(storage, rid, race_dir, t0=1000.0)

    first = [_frame(1000.0 + i * 0.1) for i in range(6)]  # ms 0,100,...,500
    rows1 = store.save(first)
    frames_dir = race_dir / "frames"
    files_before = sorted(p.name for p in frames_dir.iterdir())

    # A second window wholly inside the first: every t_ms already stored.
    shared = [first[1], first[2], first[3]]
    rows2 = store.save(shared)

    assert sorted(p.name for p in frames_dir.iterdir()) == files_before
    assert [r["id"] for r in rows2] == [r["id"] for r in rows1[1:4]]
    # Every returned row is an existing one — no duplicate frame rows.
    assert storage._conn.execute("SELECT COUNT(*) FROM frame").fetchone()[0] == 6


def test_save_returns_preexisting_and_new_rows(storage, data_root):
    rid = _race(storage)
    store = FrameStore(storage, rid, data_root / "races" / "r", t0=1000.0)
    store.save([_frame(1000.0)])            # ms 0
    rows = store.save([_frame(1000.0), _frame(1000.2)])  # ms 0 (old) + 200 (new)
    assert [r["t_ms"] for r in rows] == [0, 200]


def test_nearest_ties_pick_earlier(storage, data_root):
    rid = _race(storage)
    store = FrameStore(storage, rid, data_root / "races" / "r", t0=1000.0)
    rows = store.save([_frame(1000.0), _frame(1000.002)])  # ms 0 and 2

    assert nearest(rows, 1)["t_ms"] == 0   # tie 1 vs 1 -> earlier
    assert nearest(rows, 0)["t_ms"] == 0
    assert nearest(rows, 2)["t_ms"] == 2
    assert nearest(rows, 100)["t_ms"] == 2
    assert nearest([], 5) is None
