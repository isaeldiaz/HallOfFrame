"""Build a self-contained fake-camera folder from recorded regatta frames.

The Kjerbo event stores frames per race under ``races/<id>/captures/`` and keeps
the authoritative gun order in the SQLite ``frame`` table (``frame.t_ms``,
``frame.path``). This tool copies a small, ordered subset of those JPEGs into a
flat directory named ``000001.jpg``, ``000002.jpg``, ... so that
:mod:`hallofframe.tools.fake_camera` (which plays a folder in filename order) can
serve it as a virtual MJPEG feed.

Unlike a symlink farm the output is self-contained: copy it anywhere and the
feed still works.

    python -m hallofframe.tools.build_feed \
        --db ~/kjorbo-regatta-2026/Kjorbo_regatta_2026.db \
        --race 28 --crossings 2 --max-frames 120 \
        --out /tmp/feed --launch

Point ``[stream] url`` at the served address and set
``[transport] enabled = false``. With ``--launch`` the feed cycles forever
(``--loop``); pass ``--counter`` to overlay the live millisecond counter for the
latency-calibration flow.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_JPEG_EXTS = {".jpg", ".jpeg"}
DEFAULT_MAX_FRAMES = 240
DEFAULT_MAX_GAP_MS = 200


@dataclass(frozen=True)
class FrameRef:
    """One source JPEG, with its gun time when known (DB source)."""
    race_id: int
    t_ms: int | None
    path: Path


def collect_from_db(db: Path, data_root: Path,
                    races: list[str] | None = None) -> list[FrameRef]:
    """Read ``frame`` rows in gun order, optionally filtered to *races*.

    *races* tokens match either ``race.id`` or ``race.race_no``. A relative
    ``frame.path`` is resolved against *data_root* (the directory that holds the
    database in the reference layout)."""
    # Read-only URI: building a feed must never modify the source event DB.
    uri = Path(db).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute("select id, race_no from race").fetchall()
        by_id = {str(r[0]): r[0] for r in rows}
        by_no = {}
        for rid, race_no in rows:
            by_no.setdefault(str(race_no), []).append(rid)

        ids: set[int] | None = None
        if races:
            ids = set()
            for token in races:
                if token in by_id:
                    ids.add(by_id[token])
                elif token in by_no:
                    ids.update(by_no[token])
                else:
                    raise ValueError(f"unknown race {token!r}")

        out: list[FrameRef] = []
        for race_id, t_ms, rel in con.execute(
                "select race_id, t_ms, path from frame "
                "order by race_id, t_ms"):
            if ids is not None and race_id not in ids:
                continue
            p = Path(rel)
            if not p.is_absolute():
                p = data_root / p
            out.append(FrameRef(int(race_id), int(t_ms), p))
        return out
    finally:
        con.close()


def collect_from_dir(races_dir: Path) -> list[FrameRef]:
    """Fallback source: every JPEG under *races_dir*, in filename order."""
    files = sorted(
        (p for p in races_dir.rglob("*")
         if p.is_file() and p.suffix.lower() in _JPEG_EXTS),
        key=lambda p: str(p))
    return [FrameRef(0, None, p) for p in files]


def select(entries: list[FrameRef], *, max_frames: int = DEFAULT_MAX_FRAMES,
           every: int = 1, crossings: int | None = None,
           max_gap_ms: int = DEFAULT_MAX_GAP_MS) -> list[FrameRef]:
    """Pick a bounded, ordered subset of *entries*.

    *crossings* keeps the first N contiguous runs (a run breaks when the race
    changes or the gun-time gap exceeds *max_gap_ms*). *every* then strides, and
    *max_frames* caps the result."""
    if every < 1:
        raise ValueError("every must be >= 1")
    if crossings is not None:
        kept: list[FrameRef] = []
        runs = 0
        prev: FrameRef | None = None
        for ref in entries:
            new_run = prev is None or ref.race_id != prev.race_id
            if not new_run and ref.t_ms is not None and prev.t_ms is not None:
                new_run = (ref.t_ms - prev.t_ms) > max_gap_ms
            if new_run:
                runs += 1
                if runs > crossings:
                    break
            kept.append(ref)
            prev = ref
        entries = kept

    if every > 1:
        entries = entries[::every]
    return entries[:max_frames]


def build(entries: list[FrameRef], out: Path, *, force: bool = False) -> int:
    """Copy *entries* into *out* as ``NNNNNN.jpg`` and write ``manifest.tsv``.

    Returns the number of files copied. Missing sources are skipped."""
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        raise FileExistsError(
            f"{out} is not empty (use --force to overwrite)")
    out.mkdir(parents=True, exist_ok=True)

    lines = ["index\trace_id\tt_ms\tsource"]
    n = 0
    for ref in entries:
        if not ref.path.is_file():
            continue
        n += 1
        dst = out / f"{n:06d}.jpg"
        shutil.copyfile(ref.path, dst)
        t_ms = "" if ref.t_ms is None else str(ref.t_ms)
        lines.append(f"{n:06d}\t{ref.race_id}\t{t_ms}\t{ref.path}")
    (out / "manifest.tsv").write_text("\n".join(lines) + "\n")
    return n


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hallofframe.tools.build_feed",
        description="Copy an ordered subset of recorded frames into a "
                    "self-contained fake-camera folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--db", help="SQLite event DB (gun order via `frame`)")
    parser.add_argument("--races-dir",
                        help="fallback source: a directory of JPEGs")
    parser.add_argument("--data-root",
                        help="base for relative DB paths (default: DB's dir)")
    parser.add_argument("--race", action="append", default=None,
                        metavar="ID|NO",
                        help="race id or race_no to include (repeatable)")
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument("--every", type=int, default=1,
                        help="keep every Nth frame")
    parser.add_argument("--crossings", type=int, default=None,
                        help="keep only the first N contiguous runs")
    parser.add_argument("--max-gap-ms", type=int, default=DEFAULT_MAX_GAP_MS,
                        help="gap that starts a new crossing run")
    parser.add_argument("--out", required=True, help="feed folder to write")
    parser.add_argument("--force", action="store_true",
                        help="overwrite a non-empty --out folder")
    parser.add_argument("--launch", action="store_true",
                        help="serve the folder with fake_camera after building")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--counter", action="store_true",
                        help="overlay a live ms counter (calibration flow)")
    args = parser.parse_args(argv)

    if bool(args.db) == bool(args.races_dir):
        parser.error("give exactly one of --db or --races-dir")

    if args.db:
        db = Path(args.db).expanduser()
        data_root = (Path(args.data_root).expanduser() if args.data_root
                     else db.parent)
        entries = collect_from_db(db, data_root, args.race)
    else:
        entries = collect_from_dir(Path(args.races_dir).expanduser())

    if not entries:
        parser.error("no frames found")

    chosen = select(entries, max_frames=args.max_frames, every=args.every,
                    crossings=args.crossings, max_gap_ms=args.max_gap_ms)
    out = Path(args.out).expanduser()
    n = build(chosen, out, force=args.force)
    print(f"feed: {n} frames copied to {out} (from {len(entries)} available)")

    if not args.launch:
        print(f"serve with: python -m hallofframe.tools.fake_camera "
              f"--folder {out} --fps {args.fps:g} --port {args.port} --loop")
        return 0

    from . import fake_camera
    return fake_camera.main([
        "--folder", str(out), "--host", args.host, "--port", str(args.port),
        "--fps", str(args.fps), "--loop",
        *(["--counter"] if args.counter else []),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
