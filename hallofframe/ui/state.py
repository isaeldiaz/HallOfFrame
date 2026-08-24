"""Explicit application state machine (REDESIGN-PLAN §1).

State was previously spread across ``MainWindow._armed``, ``_race_over``,
``controller.running``, several ``setEnabled()`` calls and a status QLabel
rebuilt every 500 ms. This module is the single source of truth: one enum plus a
pure, Qt-free ``derive_state()`` so the precedence rules are unit-testable.

Precedence when several conditions hold at once (highest first):

    RECORDING > ARMED > REVIEW > RACE_OVER > STREAM_DOWN > RECALIBRATE > READY

A stream drop must NOT knock the UI out of RECORDING — timing continues
regardless; it is surfaced as a health readout instead (§1). Nor may it knock
the operator out of REVIEW or RACE_OVER: in timing-only mode the stream is down
for the whole race, yet the finished race must still be reviewed (bow numbers)
and exported, so those explicit screens outrank stream-health.
"""
from __future__ import annotations

import enum


class AppState(enum.Enum):
    STREAM_DOWN = "stream_down"    # no frames arriving
    RECALIBRATE = "recalibrate"    # calibration.json no longer matches live stream
    READY = "ready"
    ARMED = "armed"
    RECORDING = "recording"
    RACE_OVER = "race_over"
    REVIEW = "review"


def derive_state(controller, buffer, cal_ok: bool, armed: bool,
                 reviewing: bool, race_over: bool) -> AppState:
    """Derive the app state from the live inputs, in §1 precedence order.

    Args:
        controller: object with ``.running`` (CaptureController-compatible).
        buffer: FrameBuffer with ``.health()`` -> (alive, fps, age).
        cal_ok: True when calibration matches the live stream (or no stream).
        armed: True between Ctrl+S (arm) and the ENTER that starts the race.
        reviewing: True while the REVIEW state is on screen.
        race_over: True from end_race() until the next race is armed.
    """
    if getattr(controller, "running", False):
        return AppState.RECORDING
    if armed:
        return AppState.ARMED
    if reviewing:
        return AppState.REVIEW
    if race_over:
        return AppState.RACE_OVER
    alive, _fps, _age = buffer.health()
    if not alive:
        return AppState.STREAM_DOWN
    if not cal_ok:
        return AppState.RECALIBRATE
    return AppState.READY
