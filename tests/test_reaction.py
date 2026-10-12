"""Tests for reaction-time measurement and its Δ wiring (spec §5.5.1)."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from hallofframe.calibration import (ReactionRun, reaction_offset_ms,
                                     reaction_stats, update_reaction,
                                     write_calibration, calibration_path,
                                     Calibration)
from hallofframe.controller import (CalibrationError, CaptureController,
                                    _load_latency, calibration_status)
from hallofframe.ui.about_screen import environment_rows


def test_reaction_stats():
    stats = reaction_stats([200, 210, 220, 230, 240, 250, 260, 270])
    assert stats["median_ms"] == 235
    assert stats["iqr_ms"] == pytest.approx(45.0)
    assert stats["mean_ms"] == 235
    assert stats["n"] == 8


def test_reaction_stats_too_few():
    with pytest.raises(ValueError):
        reaction_stats([200] * 5)


def test_run_bad_kind():
    with pytest.raises(ValueError):
        ReactionRun("tactile")


def test_run_ok_press():
    run = ReactionRun("visual", trials=2, rng=lambda a, b: 2.0)
    assert run.next_delay() == 2.0
    assert run.state == "waiting"
    run.stimulus(10.0)
    assert run.press(10.25) == "ok"
    assert run.samples_ms == [250.0]
    assert run.state == "idle"
    assert run.done is False
    run.next_delay()
    run.stimulus(20.0)
    assert run.press(20.3) == "ok"
    assert run.done is True
    assert run.samples_ms == pytest.approx([250.0, 300.0])


def test_run_early_while_waiting():
    run = ReactionRun("visual", trials=2)
    run.next_delay()
    assert run.press(1.0) == "early"
    assert run.false_starts == 1
    assert run.samples_ms == []
    assert run.state == "idle"


def test_run_early_after_stimulus():
    run = ReactionRun("visual", trials=2)
    run.next_delay()
    run.stimulus(5.0)
    assert run.press(5.05) == "early"
    assert run.false_starts == 1
    assert run.samples_ms == []


def test_run_late_and_timeout():
    run = ReactionRun("visual", trials=2)
    run.next_delay()
    run.stimulus(5.0)
    assert run.press(7.0) == "late"
    assert run.misses == 1
    run.next_delay()
    run.stimulus(9.0)
    assert run.timeout() == "late"
    assert run.misses == 2
    assert run.timeout() == "ignored"
    assert run.press(9.5) == "ignored"


def test_update_reaction_creates_and_merges(data_root):
    update_reaction(data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev",
                    measured_at="2026-10-12T09:00:00Z")
    update_reaction(data_root, "audio",
                    {"median_ms": 180.0, "iqr_ms": 10.0, "mean_ms": 181.0,
                     "n": 8}, [2.0] * 8, "qt")
    data = json.loads(calibration_path(data_root).read_text())
    assert data["reaction"]["visual"]["median_ms"] == 231.0
    assert data["reaction"]["visual"]["press_source"] == "evdev"
    assert data["reaction"]["visual"]["measured_at"] == "2026-10-12T09:00:00Z"
    assert data["reaction"]["visual"]["samples_ms"] == [1.0] * 10
    assert data["reaction"]["audio"]["median_ms"] == 180.0
    assert "latency_median_ms" not in data


def test_update_reaction_rejects_bad_args(data_root):
    with pytest.raises(ValueError):
        update_reaction(data_root, "x",
                        {"median_ms": 1.0, "iqr_ms": 1.0, "mean_ms": 1.0,
                         "n": 1}, [], "evdev")
    with pytest.raises(ValueError):
        update_reaction(data_root, "visual",
                        {"median_ms": 1.0, "iqr_ms": 1.0, "mean_ms": 1.0,
                         "n": 1}, [], "mouse")


def test_write_calibration_preserves_reaction(data_root):
    update_reaction(data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    write_calibration(data_root, 94.0, 3.0, [94.0] * 4, "water",
                      "1440x1080", 30, "", 12345)
    data = json.loads(calibration_path(data_root).read_text())
    assert data["latency_median_ms"] == 94.0
    assert data["reaction"]["visual"]["median_ms"] == 231.0


def test_load_reaction_fields(data_root):
    write_calibration(data_root, 94.0, 3.0, [94.0] * 4, "water",
                      "1440x1080", 30, "", 12345)
    update_reaction(data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    update_reaction(data_root, "audio",
                    {"median_ms": 180.0, "iqr_ms": 10.0, "mean_ms": 181.0,
                     "n": 8}, [2.0] * 8, "qt")
    cal = Calibration.load(data_root)
    assert cal.has_latency is True
    assert cal.reaction_visual_ms == 231.0
    assert cal.reaction_audio_ms == 180.0


def test_load_reaction_only_file(data_root):
    update_reaction(data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    cal = Calibration.load(data_root)
    assert cal is not None
    assert cal.has_latency is False
    assert cal.latency_median_ms == 0.0
    assert cal.reaction_visual_ms == 231.0
    assert cal.reaction_audio_ms is None


def test_load_malformed_reaction_ignored(data_root):
    calibration_path(data_root).write_text(
        json.dumps({"latency_median_ms": 94.0, "reaction": "garbage"}))
    cal = Calibration.load(data_root)
    assert cal.has_latency is True
    assert cal.reaction_visual_ms is None
    assert cal.reaction_audio_ms is None


def test_reaction_offset_from_config(config):
    cfg = config(timing={"reaction_offset_ms": 120.0})
    assert reaction_offset_ms(cfg) == (120.0, "config")


def test_reaction_offset_measured_wins(config):
    cfg = config(timing={"reaction_offset_ms": 120.0})
    update_reaction(cfg.data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    assert reaction_offset_ms(cfg) == (231.0, "measured")


def test_delta_screen_mode_uses_measured(config, storage, buffer):
    cfg = config(timing={"viewing_mode": "screen", "reaction_offset_ms": 120.0})
    ctl = CaptureController(cfg, storage, buffer)
    try:
        assert ctl._compute_delta() == pytest.approx(0.120)
        update_reaction(cfg.data_root, "visual",
                        {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                         "n": 10}, [1.0] * 10, "evdev")
        assert ctl._compute_delta() == pytest.approx(0.231)
    finally:
        ctl.stop()


def test_delta_water_mode_uses_measured(config, storage, buffer):
    cfg = config(timing={"viewing_mode": "water", "reaction_offset_ms": 0.0})
    update_reaction(cfg.data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    ctl = CaptureController(cfg, storage, buffer)
    try:
        with mock.patch("hallofframe.controller._load_latency",
                        return_value=94.0):
            assert ctl._compute_delta() == pytest.approx(0.137)
    finally:
        ctl.stop()


def test_status_reaction_only_file(config, buffer):
    cfg = config()
    update_reaction(cfg.data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    assert calibration_status(cfg, buffer) == (
        False, "calibration.json has no latency — run Calibrate (latency)")


def test_load_latency_reaction_only_raises(config, buffer):
    cfg = config()
    update_reaction(cfg.data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    with pytest.raises(CalibrationError) as exc:
        _load_latency(cfg, buffer)
    assert "has no latency" in str(exc.value)


def test_about_rows_reaction(config):
    cfg = config()
    rows = environment_rows(cfg)
    assert ("Reaction (visual)", "0 ms (config)") in rows
    assert ("Reaction (sound)", "not measured") in rows
    update_reaction(cfg.data_root, "visual",
                    {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1,
                     "n": 10}, [1.0] * 10, "evdev")
    update_reaction(cfg.data_root, "audio",
                    {"median_ms": 180.0, "iqr_ms": 10.0, "mean_ms": 181.0,
                     "n": 8}, [2.0] * 8, "qt")
    rows = environment_rows(cfg)
    assert ("Reaction (visual)", "231 ms (measured)") in rows
    assert ("Reaction (sound)", "180 ms") in rows
