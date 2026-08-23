"""MJPEGReader soak tool (TESTING.md T2).

Prints seq, t_recv, byte count per frame and samples RSS every 10 s. Used to
prove spec §6.2: steady fps, no drift, no memory growth over 10 minutes.
"""
from __future__ import annotations

import argparse
import os
import time

from ..config import load_config
from ..mjpeg import MJPEGReader


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    stream = config.section("stream")
    auth = (stream["username"], stream["password"]) if stream["username"] else None

    deadline = time.monotonic() + args.minutes * 60.0
    last_beat = time.monotonic()
    n = 0
    bytes_total = 0
    seq_gaps = 0
    prev_seq = 0
    start = time.monotonic()

    reader = MJPEGReader(stream["url"], auth=auth,
                         require_content_length=bool(stream["require_content_length"]))

    def on_frame(frame):
        nonlocal n, bytes_total, seq_gaps, prev_seq, last_beat
        n += 1
        bytes_total += len(frame.jpeg)
        if frame.seq != prev_seq + 1 and prev_seq != 0:
            seq_gaps += 1
        prev_seq = frame.seq
        if time.monotonic() - last_beat >= 10.0:
            last_beat = time.monotonic()
            rss = _rss_kb()
            print(f"beat t={time.monotonic()-start:.0f}s frames={n} "
                  f"bytes={bytes_total} rss_kb={rss} seq_gaps={seq_gaps}",
                  flush=True)
        if n % 1000 == 0:
            print(f"  frame {frame.seq} t_recv={frame.t_recv:.3f} "
                  f"nbytes={len(frame.jpeg)}", flush=True)

    reader.on_frame = on_frame
    reader.start()
    try:
        while time.monotonic() < deadline:
            time.sleep(1.0)
    finally:
        reader.stop()
        time.sleep(0.2)

    elapsed = time.monotonic() - start
    fps = n / elapsed if elapsed else 0.0
    print(f"\nsummary: frames={n} fps={fps:.2f} elapsed={elapsed:.1f}s "
          f"seq_gaps={seq_gaps} rss_kb={_rss_kb()}", flush=True)
    return 0


def _rss_kb() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


if __name__ == "__main__":
    raise SystemExit(main())
