"""Tests for the Calibration loader and validator (spec §5.5, §8).

The loader is the single source of truth for the calibration file: missing,
corrupt and partial files must all degrade predictably, and ``mismatch`` must
reproduce the resolution/fps checks the controller used to inline.
"""
from __future__ import annotations

import json

from hallofframe.calibration import (Calibration, calibration_path,
                                     write_calibration)


def test_load_missing_returns_none(data_root):
    assert Calibration.load(data_root) is None


def test_load_corrupt_returns_none(data_root):
    calibration_path(data_root).write_text("{not json")
    assert Calibration.load(data_root) is None


def test_load_non_object_returns_none(data_root):
    calibration_path(data_root).write_text("[1, 2, 3]")
    assert Calibration.load(data_root) is None


def test_load_valid(data_root):
    calibration_path(data_root).write_text(json.dumps({
        "latency_median_ms": 94.0,
        "latency_iqr_ms": 3.0,
        "resolution": "1440x1080",
        "fps": 30,
        "mean_frame_bytes": 12345,
        "measured_at": "2026-01-01T00:00:00Z",
    }))
    cal = Calibration.load(data_root)
    assert cal is not None
    assert cal.latency_median_ms == 94.0
    assert cal.latency_iqr_ms == 3.0
    assert cal.resolution == "1440x1080"
    assert cal.fps == 30.0
    assert cal.mean_frame_bytes == 12345
    assert cal.measured_at == "2026-01-01T00:00:00Z"


def test_load_partial_uses_defaults(data_root):
    calibration_path(data_root).write_text(json.dumps({"latency_median_ms": 10}))
    cal = Calibration.load(data_root)
    assert cal is not None
    assert cal.latency_median_ms == 10.0
    assert cal.resolution == ""
    assert cal.fps == 0.0
    assert cal.mean_frame_bytes == 0
    assert cal.measured_at == ""


def test_write_then_load_roundtrip(data_root):
    write_calibration(data_root, 94.0, 3.0, [90.0, 94.0, 98.0], "water",
                      "1440x1080", 30, "", 12345,
                      measured_at="2026-01-01T00:00:00Z")
    cal = Calibration.load(data_root)
    assert cal is not None
    assert cal.latency_median_ms == 94.0
    assert cal.resolution == "1440x1080"
    assert cal.fps == 30.0
    assert cal.mean_frame_bytes == 12345


def _cal(**overrides) -> Calibration:
    base = dict(latency_median_ms=94.0, latency_iqr_ms=3.0,
                resolution="1440x1080", fps=30.0, mean_frame_bytes=100,
                measured_at="2026-01-01T00:00:00Z")
    base.update(overrides)
    return Calibration(**base)


def test_mismatch_both_unknown_ok():
    assert _cal(resolution="", fps=0.0).mismatch("", 0.0) is None


def test_mismatch_live_unknown_ok():
    assert _cal().mismatch("", 0.0) is None


def test_mismatch_equal_ok():
    assert _cal().mismatch("1440x1080", 30.0) is None


def test_mismatch_resolution_differs():
    reason = _cal().mismatch("1920x1080", 30.0)
    assert reason is not None
    assert "RESOLUTION" in reason


def test_mismatch_resolution_only_when_both_known():
    assert _cal(resolution="1440x1080").mismatch("", 30.0) is None
    assert _cal(resolution="").mismatch("1920x1080", 30.0) is None


def test_mismatch_fps_within_tolerance_ok():
    # tolerance is max(1, cal_fps * 0.05) = 1.5 for 30 fps
    assert _cal(fps=30.0).mismatch("1440x1080", 31.0) is None


def test_mismatch_fps_outside_tolerance():
    reason = _cal(fps=30.0).mismatch("1440x1080", 15.0)
    assert reason is not None
    assert "FPS" in reason


def test_mismatch_fps_unknown_ok():
    assert _cal(fps=0.0).mismatch("1440x1080", 15.0) is None
    assert _cal(fps=30.0).mismatch("1440x1080", 0.0) is None
