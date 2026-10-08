"""The application's UI state enum.

State was previously spread across ``MainWindow`` flags, ``controller.running``,
several ``setEnabled()`` calls and a status QLabel rebuilt every 500 ms. The
enum remains the single vocabulary the widgets render; the operator's actual
position in the lifecycle now lives in ``hallofframe.session.Session`` and is
mapped to one of these values by ``hallofframe.session.derive_state``.

Precedence when several conditions hold at once (highest first):

    RECORDING > ARMED > REVIEW > RACE_OVER > STREAM_DOWN > RECALIBRATE > READY

A stream drop must NOT knock the UI out of RECORDING — timing continues
regardless; it is surfaced as a health readout instead. Nor may it knock the
operator out of REVIEW or RACE_OVER: in timing-only mode the stream is down for
the whole race, yet the finished race must still be reviewed (bow numbers) and
exported, so those explicit screens outrank stream-health.
"""
from __future__ import annotations

import enum


class AppState(enum.Enum):
    STREAM_DOWN = "stream_down"    # no frames arriving
    RECALIBRATE = "recalibrate"    # calibration file no longer matches live stream
    READY = "ready"
    ARMED = "armed"
    RECORDING = "recording"
    RACE_OVER = "race_over"
    REVIEW = "review"
