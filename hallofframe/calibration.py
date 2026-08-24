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
    """For each frame, L = t_recv - T_shown. Return stats + samples."""
    if len(frames) != len(counter_values_ms):
        raise ValueError(
            f"{len(frames)} frames but {len(counter_values_ms)} counter values")
    samples = [ (f.t_recv - (t_shown_ms / 1000.0)) * 1000.0
                for f, t_shown_ms in zip(frames, counter_values_ms) ]
    samples.sort()
    median = statistics.median(samples)
    try:
        iqr = float(samples[N_SAMPLES // 4] - samples[-(N_SAMPLES // 4) - 1])
        if iqr < 0:
            iqr = -iqr
    except Exception:
        q = statistics.quantiles(samples, n=4)
        iqr = q[2] - q[0]
    return {
        "latency_median_ms": median,
        "latency_iqr_ms": iqr,
        "samples_ms": samples,
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
