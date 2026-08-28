"""Late-regatta soak: is the app still correct when the day is almost over?

Synthesises a near-complete regatta in a data root — many races, many crossings,
many saved window frames and image files — then drives the REAL
``CaptureController`` through the final race of the day and verifies the whole
store end-to-end: integrity, sequence discipline, image files on disk, exports
at full scale, trigger-path latency and memory.

Two ways to use the helpers:

* ``python -m hallofframe.tools.late_regatta_soak [--flags]`` — a standalone
  report with tunable scale, for running on the actual race-day machine
  (the "db is almost complete" check, TESTING.md spirit).
* ``tests/test_late_regatta.py`` — the same helpers at a fixed smaller scale,
  run in CI.

Nothing here needs a phone or a display. ``viewing_mode = "screen"`` so no
calibration file is required (spec §5.4).
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

from ..config import Config
from ..controller import CaptureController
from ..export import export_all_csv, export_all_html
from ..framebuffer import FrameBuffer
from ..mjpeg import Frame
from ..storage import Storage

# Rotating category labels so the seeded roster looks like a real programme.
_NAMES = [
    "D U17 2x", "M Open 1x", "W U23 4x-", "M Nov 8+", "W Open 2x",
    "M U19 4x+", "W Nov 1x", "M D 4-", "W U17 8+", "M U23 2x",
]


def make_config(data_root, window_ms=500, fps=30):
    data = {
        "paths": {"data_root": str(data_root)},
        "stream": {"assumed_fps": fps, "buffer_seconds": 10.0},
        "timing": {"viewing_mode": "screen", "reaction_offset_ms": 0.0,
                   "debounce_ms": 20, "start_mode": "direct",
                   "radio_delay_ms": 0.0, "image_mode": "auto"},
        "capture": {"window_before_ms": window_ms, "window_after_ms": window_ms},
        "archive": {"enabled": False, "every_nth_frame": 1},
    }
    return Config(data=data, path=Path(data_root) / "config.toml")


def make_jpeg(w=64, h=36) -> bytes:
    """A tiny but real JPEG (single grey field) for the seeded window frames."""
    from PIL import Image
    import io
    bio = io.BytesIO()
    Image.new("RGB", (w, h), (96, 118, 138)).save(bio, "JPEG")
    return bio.getvalue()


def _race_dir(data_root, race_id, name):
    """Mirror of CaptureController._race_dir so seeded paths match the app's."""
    clean = "".join(c if (c.isalnum() or c in "._- ") else "_"
                    for c in name).strip()
    clean = clean or "race"
    return Path(data_root) / "races" / f"{race_id:04d}_{clean}"


def _offset_for(k, n, window_ms):
    """Offset (ms) of the k-th of n window frames spanning +-window_ms."""
    if n <= 1:
        return 0.0
    return -window_ms + k * (2.0 * window_ms) / (n - 1)


def seed_regatta(storage, *, races=150, min_captures=4, max_captures=8,
                 frames_per_capture=31, fps=30, jpeg=None, window_ms=500,
                 seed=1, delete_fraction=0.02, approximate_fraction=0.05):
    """Build a near-complete regatta DB through the real Storage API.

    Every race, capture, window frame and image file is written exactly as the
    app would produce it (same schema, ``UNIQUE(race_id, sequence)``, one
    primary per capture, relative ``capture_frame.path``). Returns a stats dict
    used both for the report and for exact count checks.
    """
    if jpeg is None:
        jpeg = make_jpeg()
    rng = random.Random(seed)
    stats = {"races": 0, "captures": 0, "live": 0, "deleted": 0, "frames": 0,
             "files_written": 0}
    t0 = 500_000.0          # synthetic monotonic domain, strictly increasing
    race_duration = 300.0   # seconds per race
    started = time.monotonic()

    for i in range(races):
        race_no = f"{100 + i:03d}"
        name = _NAMES[i % len(_NAMES)]
        # Historical wall-clock: races progress backwards from "now".
        t0_wall = time.time() - (i * race_duration)
        race_id = storage.create_race(
            name=name, t0_monotonic=t0, t0_wall=t0_wall,
            start_mode="direct", radio_delay_ms=0.0, delta_used=0.0,
            viewing_mode="screen", fps_nominal=fps,
            race_no=race_no, heat_no="1")
        caps_dir = _race_dir(storage.data_root, race_id, name) / "captures"
        caps_dir.mkdir(parents=True, exist_ok=True)

        n = rng.randint(min_captures, max_captures)
        for seq in range(1, n + 1):
            t_press = t0 + seq * 12.0
            elapsed = t_press - t0
            approx = rng.random() < approximate_fraction
            cap_id = storage.insert_capture(
                race_id, seq, t_press, t0_wall + elapsed, elapsed, 0.0,
                image_flag="approximate" if approx else None,
                bow_number=str(rng.randint(1, 20)),
                debounce_suspect=int(rng.random() < 0.03))
            primary_id = None
            for k in range(frames_per_capture):
                offset = _offset_for(k, frames_per_capture, window_ms)
                sign = "-" if offset < 0 else "+"
                fname = f"{seq:03d}_w{sign}{abs(offset):04.0f}.jpg"
                fpath = caps_dir / fname
                fpath.write_bytes(jpeg)
                frame_id = storage.insert_frame(
                    cap_id, t_press + offset / 1000.0, offset,
                    str(fpath.relative_to(storage.data_root)))
                stats["files_written"] += 1
                # primary = the frame nearest the target (offset ~ 0)
                if k == frames_per_capture // 2:
                    primary_id = frame_id
            storage.set_primary(cap_id, primary_id)
            stats["captures"] += 1
            stats["frames"] += frames_per_capture
            if rng.random() < delete_fraction:
                storage.update_capture(cap_id, deleted=1)
                stats["deleted"] += 1
            else:
                stats["live"] += 1
        stats["races"] += 1
        t0 += race_duration

    stats["seed_seconds"] = time.monotonic() - started
    return stats


class _Feeder:
    """Append real-clock frames at ``fps`` until stop() — the synthetic camera."""

    def __init__(self, buffer, fps, jpeg):
        self._buffer = buffer
        self._fps = fps
        self._jpeg = jpeg
        self._stop = threading.Event()
        self._seq = 0
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="soak-feeder")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5.0)

    def _run(self):
        while not self._stop.is_set():
            self._seq += 1
            t = time.monotonic()
            self._buffer.append(Frame(t, time.time(), self._seq, self._jpeg))
            self._stop.wait(1.0 / self._fps)


def run_final_race(config, storage, buffer, controller, *, crossings=8, fps=30,
                   jpeg=None, debounce_seq=4, undo_seq=2):
    """Drive the final race of the day through the real controller.

    Real-clock frames feed the buffer; presses use real ``time.monotonic()`` so
    target selection behaves as in production (flags stay clean). One crossing
    is a debounce suspect; one is undone (soft delete). Returns a stats dict.
    """
    if jpeg is None:
        jpeg = make_jpeg()
    commit_times: dict[int, float] = {}
    call_times: dict[int, float] = {}
    call_durations: dict[int, float] = {}

    def on_capture(cap):
        commit_times[cap.sequence] = time.monotonic()

    deleted_seqs: list[int] = []
    image_ready: list[tuple[int, str]] = []
    controller.signal_capture_added = on_capture
    controller.signal_capture_deleted = deleted_seqs.append
    controller.signal_image_ready = lambda seq, p: image_ready.append((seq, p))

    feeder = _Feeder(buffer, fps, jpeg)
    feeder.start()
    time.sleep(0.4)  # fill the buffer so health() reports the stream alive

    t0 = time.monotonic()
    race_id = controller.start_race(t0, name="FINAL", race_no="999", heat_no="1")

    # Irregular real-time spacing so this is not a boring metronome.
    delays = [0.8 + i * 0.9 for i in range(crossings)]

    for i, d in enumerate(delays):
        seq = i + 1
        while time.monotonic() - t0 < d:
            time.sleep(0.01)
        t_press = time.monotonic()
        call_times[seq] = time.monotonic()
        controller.record_crossing(t_press, debounce_suspect=(seq == debounce_seq))
        call_durations[seq] = time.monotonic() - call_times[seq]
        if seq == undo_seq and crossings >= undo_seq:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if len(storage.captures_for_race(race_id)) >= seq:
                    break
                time.sleep(0.02)
            controller.undo_last()

    t_end = t0 + delays[-1] + 0.4
    while time.monotonic() < t_end:
        time.sleep(0.01)
    controller.end_race(time.monotonic())

    # The last press's deferred selection fires ~window_after_ms + margin after
    # the commit — and must still land after end_race() (timers are not
    # cancelled by ending a race). Wait for it to FULLY complete: the flag is
    # only finalized once primary_image is set, because _select_images recomputes
    # image_flag and only then calls set_primary (controller._select_images).
    # Waiting on the first window frame is racy — it appears while the selection
    # is still writing frames, before the flag rewrite. The feeder keeps running
    # so the after-window frames exist.
    last_seq = crossings
    deadline = time.monotonic() + 3.0 + 0.001 * config.section("capture")["window_after_ms"]
    while time.monotonic() < deadline:
        cap = next((c for c in storage.captures_for_race(race_id, include_deleted=True)
                    if c["sequence"] == last_seq), None)
        if cap is not None and cap["primary_image"]:
            break
        time.sleep(0.02)
    feeder.stop()

    live = [c for c in storage.captures_for_race(race_id)]
    latencies = [commit_times[seq] - call_times[seq] for seq in commit_times]
    return {
        "race_id": race_id,
        "t0": t0,
        "presses": call_times,
        "commit_latencies_s": sorted(latencies),
        "max_enqueue_s": max(call_durations.values()) if call_durations else 0.0,
        "deleted_seqs": deleted_seqs,
        "live_seqs": [c["sequence"] for c in live],
        "image_ready": image_ready,
        "ended": True,
    }


def run_session(config, storage, buffer, controller, *, races=10, crossings=4,
                fps=30, jpeg=None):
    """Back-to-back races on one controller — the accumulation half.

    Watches the deferred-selection timer set drain to zero after every race and
    records WAL growth, so a leak (timers, writer backlog) shows up as a number,
    not a suspicion.
    """
    if jpeg is None:
        jpeg = make_jpeg()
    feeder = _Feeder(buffer, fps, jpeg)
    feeder.start()
    time.sleep(0.3)

    wal_path = storage.db_path.with_suffix(".db-wal")
    wal_before = wal_path.stat().st_size if wal_path.exists() else 0
    stats = {"races_done": 0, "crossings": 0, "max_timers_pending": 0,
             "timers_drained": True, "wal_before": wal_before,
             "wal_after": 0, "commit_latencies_s": []}

    for i in range(races):
        t0 = time.monotonic()
        race_id = controller.start_race(t0, name=f"Session {i}",
                                        race_no=str(300 + i), heat_no="1")
        for _ in range(crossings):
            time.sleep(0.25)
            controller.record_crossing(time.monotonic())
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(storage.captures_for_race(race_id)) >= crossings:
                break
            time.sleep(0.02)
        controller.end_race(time.monotonic())

        # Wait for every deferred selection to fire so the timer set drains.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with controller._timers_lock:
                pending = len(controller._timers)
            stats["max_timers_pending"] = max(stats["max_timers_pending"], pending)
            if pending == 0:
                break
            time.sleep(0.05)
        else:
            stats["timers_drained"] = False
        stats["races_done"] += 1
        stats["crossings"] += crossings

    feeder.stop()
    stats["wal_after"] = wal_path.stat().st_size if wal_path.exists() else 0
    return stats


def verify_end_of_day(storage, data_root, seed_stats, final_stats):
    """End-of-day checks against the whole store. Returns (name, ok, detail)."""
    checks: list[tuple[str, bool, str]] = []
    races = storage.all_races()

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)))

    # --- database health --------------------------------------------------
    add("integrity_check", storage.integrity_ok(), "PRAGMA integrity_check")
    n_races = len(races)
    expected_races = seed_stats["races"] + 1
    add("race_count", n_races == expected_races, f"{n_races} == {expected_races}")

    cap_rows = sum(len(storage.captures_for_race(r["id"], include_deleted=True))
                   for r in races)
    live = sum(len(storage.captures_for_race(r["id"])) for r in races)
    expected_caps = seed_stats["captures"] + len(final_stats["presses"])
    expected_live = seed_stats["live"] + len(final_stats["live_seqs"])
    add("capture_rows", cap_rows == expected_caps,
        f"{cap_rows} == {expected_caps}")
    add("live_captures", live == expected_live, f"{live} == {expected_live}")

    # Seed frame count is exact; final-race frame counts depend on buffer timing
    # at selection so they are checked structurally (>=1 window frame, primary).
    seed_frame_total = sum(
        len(storage.frames_for_capture(c["id"]))
        for r in races for c in storage.captures_for_race(r["id"], include_deleted=True)
        if r["id"] < final_stats["race_id"])
    add("seed_frame_count", seed_frame_total == seed_stats["frames"],
        f"{seed_frame_total} == {seed_stats['frames']}")

    # --- the Ready screen's gray-out set (race_keys) -----------------------
    keys = storage.race_keys()
    add("race_keys_distinct", len(keys) == n_races,
        f"{len(keys)} keys for {n_races} races")

    # --- sequence discipline (numbers never reused, never gapped) ----------
    bad = []
    for r in races:
        seqs = sorted(c["sequence"] for c in
                      storage.captures_for_race(r["id"], include_deleted=True))
        if seqs != list(range(1, len(seqs) + 1)):
            bad.append((r["id"], seqs))
    add("sequence_discipline", not bad, f"{len(bad)} races with gaps")

    # --- image files exist -------------------------------------------------
    missing: list[str] = []
    for r in races:
        for c in storage.captures_for_race(r["id"], include_deleted=True):
            p = c["primary_image"]
            if p and not (storage.data_root / p).exists():
                missing.append(f"primary {p}")
            for f in storage.frames_for_capture(c["id"]):
                if not (storage.data_root / f["path"]).exists():
                    missing.append(f"frame {f['path']}")
    add("image_files_exist", not missing, f"{len(missing)} missing paths")

    # --- exports at full scale ---------------------------------------------
    csv_path = storage.data_root / "export.csv"
    export_all_csv(storage, csv_path)
    n_rows = sum(1 for _ in csv.reader(csv_path.open(encoding="utf-8"))) - 1
    add("csv_rows", n_rows == live, f"{n_rows} data rows == {live} live")

    html_path = storage.data_root / "export.html"
    export_all_html(storage, html_path)
    text = html_path.read_text(encoding="utf-8")
    add("html_relative_paths",
        str(storage.data_root) not in text and "://" not in text,
        "no absolute paths or URLs in the page")
    add("html_race_sections", text.count("<section data-race=") == n_races,
        f"{text.count('<section data-race=')} sections == {n_races} races")
    n_cards = text.count("<div data-search=")
    add("html_cards", n_cards == live, f"{n_cards} cards == {live} live")
    refs = [urllib.parse.unquote(s) for s in re.findall(r'src="([^"]+)"', text)]
    missing_src = [s for s in refs
                   if s and not (storage.data_root / s).exists()]
    add("html_images_exist", not missing_src, f"{len(missing_src)} broken refs")
    return checks


def _rss_kb() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Late-regatta soak: near-complete DB + the final race.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--data-root", default=None,
                    help="persist here; default: fresh temp dir (cleaned up)")
    ap.add_argument("--races", type=int, default=150)
    ap.add_argument("--min-captures", type=int, default=4)
    ap.add_argument("--max-captures", type=int, default=8)
    ap.add_argument("--frames-per-capture", type=int, default=31)
    ap.add_argument("--window-ms", type=int, default=500)
    ap.add_argument("--final-crossings", type=int, default=8)
    ap.add_argument("--session-races", type=int, default=0,
                    help="back-to-back races after the final one (accumulation)")
    ap.add_argument("--session-crossings", type=int, default=4)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-commit-p99-ms", type=float, default=100.0)
    ap.add_argument("--keep", action="store_true",
                    help="leave the data root in place after the run")
    args = ap.parse_args(argv)

    own_root = args.data_root is None
    root = Path(args.data_root) if args.data_root else \
        Path(tempfile.mkdtemp(prefix="regatta-soak-"))
    root.mkdir(parents=True, exist_ok=True)

    rss_before = _rss_kb()
    config = make_config(root, window_ms=args.window_ms, fps=args.fps)
    storage = Storage(root)
    buffer = FrameBuffer(seconds=10.0, assumed_fps=args.fps)
    controller = CaptureController(config, storage, buffer)

    try:
        seed_stats = seed_regatta(
            storage, races=args.races,
            min_captures=args.min_captures, max_captures=args.max_captures,
            frames_per_capture=args.frames_per_capture, fps=args.fps,
            window_ms=args.window_ms, seed=args.seed)
        rss_seeded = _rss_kb()

        final = run_final_race(
            config, storage, buffer, controller,
            crossings=args.final_crossings, fps=args.fps)
        rss_final = _rss_kb()

        checks = verify_end_of_day(storage, root, seed_stats, final)

        session = None
        if args.session_races > 0:
            session = run_session(
                config, storage, buffer, controller,
                races=args.session_races, crossings=args.session_crossings,
                fps=args.fps)
        rss_done = _rss_kb()

        # --- report --------------------------------------------------------
        print("=" * 64)
        print(f"Late-regatta soak   data_root={root}")
        print(f"  seeded     {seed_stats['races']} races / {seed_stats['captures']} "
              f"captures ({seed_stats['deleted']} deleted) / "
              f"{seed_stats['frames']} window frames / "
              f"{seed_stats['files_written']} files  [{seed_stats['seed_seconds']:.1f}s]")
        print(f"  final race race_id={final['race_id']}: "
              f"{len(final['presses'])} presses, live "
              f"{final['live_seqs']}, undone {final['deleted_seqs']}")
        if session:
            print(f"  session    {session['races_done']} races x "
                  f"{session['crossings'] // max(1, session['races_done'])} crossings, "
                  f"max {session['max_timers_pending']} pending timers, "
                  f"drained={session['timers_drained']}, "
                  f"WAL {session['wal_before']//1024}KiB -> "
                  f"{session['wal_after']//1024}KiB")
        print("-" * 64)
        lat = final["commit_latencies_s"]
        if lat:
            n = len(lat)
            p50 = lat[n // 2]
            p99 = lat[min(n - 1, int(n * 0.99))]
            print(f"trigger path: max enqueue {final['max_enqueue_s']*1000:.3f} ms "
                  f"(record_crossing must not block); "
                  f"commit p50 {p50*1000:.1f} ms p99 {p99*1000:.1f} ms "
                  f"max {lat[-1]*1000:.1f} ms")
        for name, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:24s} {detail}")
        print("-" * 64)
        print(f"RSS (kB): before={rss_before} seeded={rss_seeded} "
              f"final={rss_final} done={rss_done}")
        print("=" * 64)

        failed = [c for c in checks if not c[1]]
        lat_fail = bool(lat) and p99 > args.max_commit_p99_ms / 1000.0
        if session and not session["timers_drained"]:
            failed.append(("timers_drained", False,
                           "deferred timers did not drain between races"))
        if lat_fail:
            failed.append(("commit_p99", False,
                           f"{p99*1000:.1f} ms > {args.max_commit_p99_ms} ms"))
        code = 1 if failed else 0
        print(f"RESULT: {'FAIL' if failed else 'PASS'}"
              + (f" ({len(failed)} failed)" if failed else ""))
        return code
    finally:
        controller.stop()
        storage.close()
        if own_root and not args.keep:
            shutil.rmtree(root, ignore_errors=True)
        elif args.keep or own_root:
            print(f"data left at {root}")


if __name__ == "__main__":
    raise SystemExit(main())