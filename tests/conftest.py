"""Shared pytest fixtures for the HallOfFrame test suite (Phase 0, step 0.2)."""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import pytest

from hallofframe.config import Config
from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer
from hallofframe.mjpeg import Frame
from hallofframe.storage import Storage

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# One canonical config for every test that used to carry its own make_config
# copy. Section overrides are merged into these dicts (copy.deepcopy per call).
_CANONICAL: dict = {
    "paths": {"data_root": ""},
    "stream": {"assumed_fps": 30, "buffer_seconds": 10.0},
    "timing": {
        "viewing_mode": "screen",
        "reaction_offset_ms": 0.0,
        "debounce_ms": 20,
        "start_mode": "direct",
        "radio_delay_ms": 0.0,
        "image_mode": "auto",
    },
    "capture": {"window_before_ms": 50, "window_after_ms": 50},
    "archive": {"enabled": False, "every_nth_frame": 1},
    "trigger": {
        "device_path": "/dev/input/event3",
        "crossing_keycodes": [57],
        "start_keycodes": [28],
        "end_keycodes": [88],
        "grab_device": True,
    },
    "ui": {"finish_line_x": 0.5, "preview_fps": 10},
}

# Create the QApplication eagerly, at import time, before any test module is
# collected. The Qt test modules still do ``QApplication.instance() or
# QApplication([])``; holding a single app here means they all reuse this one
# instead of creating (and destroying) their own, which is what segfaulted at
# interpreter teardown.
_QAPP = None
try:  # pragma: no cover - exercised by the whole Qt suite
    from PySide6.QtWidgets import QApplication as _QApplication

    _QAPP = _QApplication.instance() or _QApplication([])
except Exception:  # PySide6 missing or no usable platform plugin
    _QApplication = None


@pytest.fixture(scope="session")
def qapp():
    """The single session-wide QApplication (offscreen)."""
    if _QAPP is None:
        pytest.skip("PySide6 unavailable")
    return _QAPP


@pytest.fixture
def data_root():
    """A throwaway data root, removed on teardown."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def config(data_root):
    """Factory returning a Config from the canonical dict, with section merges.

    ``config(trigger={"device_path": ""})`` replaces only the ``trigger`` keys
    given; every other section keeps the canonical defaults.
    """

    def _make(**overrides) -> Config:
        data = copy.deepcopy(_CANONICAL)
        data["paths"]["data_root"] = str(data_root)
        for section, values in overrides.items():
            data.setdefault(section, {}).update(values)
        return Config(data=data, path=data_root / "config.toml")

    return _make


@pytest.fixture
def storage(data_root):
    st = Storage(data_root)
    yield st
    st.close()


@pytest.fixture
def buffer():
    return FrameBuffer(assumed_fps=30)


@pytest.fixture
def seeded_buffer():
    """Factory seeding a buffer with synthetic monotonically increasing frames."""

    def _seed(buf: FrameBuffer, t0: float = 1000.0, fps: int = 30,
              seconds: float = 6.0) -> None:
        dt = 1.0 / fps
        n = int(seconds * fps)
        for i in range(n):
            t = t0 + i * dt
            buf.append(Frame(t, t, i + 1, b"\xff\xd8jpeg%d\xff\xd9" % i))

    return _seed


@pytest.fixture
def controller(config, storage, buffer):
    ctl = CaptureController(config(), storage, buffer)
    yield ctl
    ctl.stop()


# --- Phase 0, step 0.3: golden-file support -------------------------------
# Additive only: this hook registers the flag used by tests/test_goldens.py and
# does not touch any existing fixture.
def pytest_addoption(parser):
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite tests/goldens/ from the current output instead of asserting",
    )
