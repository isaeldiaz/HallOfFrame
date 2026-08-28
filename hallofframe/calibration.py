"""Latency calibration (spec §5.5).

Sequential flow mandated by the single-display hardware (constraint 2):
full-screen counter -> capture 20 frames -> leave full-screen -> operator
enters the 20 counter values. This module computes the median and IQR of L and
writes ``calibration.json`` with the capture format (resolution, fps, lens,
mean frame bytes) so a mismatch with the live stream can be detected at race
start (§8).

The latency formula is applied in the controller; here we only measure.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .framebuffer import FrameBuffer
from .mjpeg import Frame

N_SAMPLES = 20


def parse_counter(value: str) -> tuple[float, float] | None:
    """Parse an 8-digit counter string that may contain ``?`` for digits the
    operator could not read (blurred by a digit transition during the exposure).

    Returns ``(midpoint_ms, half_width_ms)``. The true value lies in
    [midpoint - half_width, midpoint + half_width]. A single masked LSB digit
    yields half_width = 5 ms. Returns None if the string is not 8 characters or
    contains anything other than digits and ``?``.
    """
    if len(value) != 8:
        return None
    lo = 0
    hi = 0
    for ch in value:
        if ch == "?":
            lo = lo * 10
            hi = hi * 10 + 9
        elif ch.isdigit():
            d = int(ch)
            lo = lo * 10 + d
            hi = hi * 10 + d
        else:
            return None
    return (lo + hi) / 2.0, (hi - lo) / 2.0


def readable_value(value: str, max_half_width_ms: float = 100.0) -> float | None:
    """Return a usable counter midpoint for a possibly ``?``-marked entry, or
    None when it is unparseable or its masked range is too coarse to trust."""
    parsed = parse_counter(value)
    if parsed is None:
        return None
    midpoint, half = parsed
    if half > max_half_width_ms:
        return None
    return midpoint


def capture_calibration_frames(buffer: FrameBuffer, count: int = N_SAMPLES
                               ) -> list[Frame]:
    """Capture *count* frames from the buffer, recording each frame's t_recv.
    Returns frames in arrival order (newest last)."""
    t0 = time.monotonic()
    frames = []
    seen = set()
    # Snapshot new frames for a brief window so we get *count* distinct ones.
    deadline = t0 + 5.0
    while time.monotonic() < deadline and len(frames) < count:
        span = buffer.span()
        if span is None:
            time.sleep(0.05)
            continue
        with buffer._lock:
            snapshot = list(buffer._buf)
        for f in snapshot:
            if id(f) not in seen:
                seen.add(id(f))
                frames.append(f)
                if len(frames) >= count:
                    break
        time.sleep(0.02)
    return frames


def compute_latency(frames: list[Frame], counter_values_ms: list[float]) -> dict:
    """For each frame, L = t_recv - T_shown. Return stats + samples.

    ``frames`` / ``counter_values_ms`` may hold fewer than N_SAMPLES entries:
    an operator can skip unreadable (blurred) frames during a rolling-counter
    calibration, and the median/IQR are computed over whatever readable subset
    was entered. Requires at least 4 samples."""
    if len(frames) != len(counter_values_ms):
        raise ValueError(
            f"{len(frames)} frames but {len(counter_values_ms)} counter values")
    if len(frames) < 4:
        raise ValueError(
            f"need at least 4 readable frames, got {len(frames)}")
    samples = [ (f.t_recv - (t_shown_ms / 1000.0)) * 1000.0
                for f, t_shown_ms in zip(frames, counter_values_ms) ]
    samples.sort()
    median = statistics.median(samples)
    try:
        q = statistics.quantiles(samples, n=4)
        iqr = q[2] - q[0]
    except Exception:
        iqr = 0.0
    return {
        "latency_median_ms": median,
        "latency_iqr_ms": iqr,
        "samples_ms": samples,
        "n": len(samples),
    }


def write_calibration(data_root: Path, latency_median_ms: float, latency_iqr_ms: float,
                      samples_ms: list[float], viewing_mode: str, resolution: str,
                      fps: int, lens: str, mean_frame_bytes: int,
                      measured_at: str | None = None) -> Path:
    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    cal = {
        "measured_at": measured_at or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                    time.gmtime()),
        "latency_median_ms": latency_median_ms,
        "latency_iqr_ms": latency_iqr_ms,
        "samples_ms": samples_ms,
        "viewing_mode": viewing_mode,
        "resolution": resolution,
        "fps": fps,
        "lens": lens,
        "mean_frame_bytes": mean_frame_bytes,
    }
    path = data_root / "calibration.json"
    path.write_text(json.dumps(cal, indent=2) + "\n")
    return path
