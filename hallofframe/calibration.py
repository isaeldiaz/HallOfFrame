"""Latency calibration (spec §5.5).

Sequential flow mandated by the single-display hardware (constraint 2):
full-screen counter -> capture 20 frames -> leave full-screen -> operator
enters the 20 counter values. This module computes the median and IQR of L and
writes the calibration result file with the capture format (resolution, fps,
lens, mean frame bytes) so a mismatch with the live stream can be detected at
race start (§8).

The latency formula is applied in the controller; here we only measure.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from .framebuffer import FrameBuffer
from .mjpeg import Frame

N_SAMPLES = 20

# The calibration result filename lives here, in ONE place: every reader
# (controller, about screen) goes through Calibration.load / calibration_path so
# the name is never duplicated (spec §8).
CALIBRATION_FILENAME = "calibration.json"


def calibration_path(data_root) -> Path:
    """Path of the calibration result file under *data_root* (spec §8)."""
    return Path(data_root) / CALIBRATION_FILENAME


@dataclass(frozen=True)
class Calibration:
    """The persisted latency calibration (spec §5.5, §8).

    ``resolution`` is "" and ``fps``/``mean_frame_bytes`` may be 0 when the
    writer could not measure them; ``mismatch`` treats an unknown field as
    "no constraint" so a partial file still validates.
    """
    latency_median_ms: float
    latency_iqr_ms: float
    resolution: str      # "1440x1080" or ""
    fps: float           # 0 when unknown
    mean_frame_bytes: int
    measured_at: str

    @classmethod
    def load(cls, data_root: Path) -> "Calibration | None":
        """Load the calibration file, or None if it is missing or unreadable
        (spec §8). A malformed body is treated the same as unreadable."""
        path = calibration_path(data_root)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        try:
            return cls(
                latency_median_ms=float(data.get("latency_median_ms", 0.0)),
                latency_iqr_ms=float(data.get("latency_iqr_ms", 0.0)),
                resolution=str(data.get("resolution", "") or ""),
                fps=float(data.get("fps", 0) or 0),
                mean_frame_bytes=int(data.get("mean_frame_bytes", 0) or 0),
                measured_at=str(data.get("measured_at", "") or ""),
            )
        except Exception:
            return None

    def mismatch(self, live_resolution: str, live_fps: float) -> str | None:
        """Compare the calibrated pipeline format against the live stream.

        Returns None when they agree (or when a field is unknown on either
        side), else a human-readable reason. Resolution must be equal when both
        are known; fps must be within ``max(1, cal_fps*0.05)`` when both are
        known (spec §8). ``mean_frame_bytes`` is deliberately NOT gated: JPEG
        size varies with scene complexity, so a scene change would otherwise
        demand a needless re-calibration even though pipeline latency is
        unchanged.
        """
        if (self.resolution and live_resolution
                and self.resolution != live_resolution):
            return (f"RESOLUTION CHANGED: cal {self.resolution} vs live "
                    f"{live_resolution} — re-calibrate")
        if (self.fps and live_fps
                and abs(live_fps - self.fps) > max(1, self.fps * 0.05)):
            return (f"FPS CHANGED: cal {self.fps:.0f} vs live {live_fps:.1f} "
                    "— re-calibrate")
        return None


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
        snapshot = buffer.recent(buffer.maxlen)
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
    path = calibration_path(data_root)
    path.write_text(json.dumps(cal, indent=2) + "\n")
    return path
