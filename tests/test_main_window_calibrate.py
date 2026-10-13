"""Calibrate dialog: evdev presses route to the open dialog (sheet 23, adapted)."""
from __future__ import annotations

from unittest import mock

import pytest

pytest.importorskip("PySide6")

from hallofframe.controller import CaptureController
from hallofframe.ui.calibration_dialog import CalibrationDialog
from hallofframe.ui.state import AppState

pytestmark = pytest.mark.qt


def _build(config, storage, buffer, qapp):
    from hallofframe.ui.main_window import MainWindow

    cfg = config(timing={"viewing_mode": "screen", "image_mode": "off"},
                 trigger={"device_path": "", "grab_device": False},
                 scratchpad={"enabled": False})
    ctl = CaptureController(cfg, storage, buffer)
    win = MainWindow(cfg, ctl, buffer)
    win.show()
    qapp.processEvents()
    return win, ctl


def _teardown(win, ctl):
    win.status_timer.stop()
    ctl.stop()
    win.close()


def test_evdev_press_routed_to_dialog(qapp, config, storage, buffer):
    win, ctl = _build(config, storage, buffer, qapp)
    try:
        win.evdev_active = True
        win.controller.record_crossing = mock.Mock()
        win._calibrate()
        qapp.processEvents()
        dlg = win.findChild(CalibrationDialog)
        assert dlg is not None
        pressed = []
        dlg.reaction.on_press = pressed.append
        win.on_evdev_crossing(5.0, 57)
        win.on_evdev_start(6.0)
        assert pressed == [5.0, 6.0]
        win.controller.record_crossing.assert_not_called()
    finally:
        _teardown(win, ctl)


def test_hook_cleared_on_close(qapp, config, storage, buffer):
    win, ctl = _build(config, storage, buffer, qapp)
    try:
        win._calibrate()
        qapp.processEvents()
        assert win.press_hook is not None
        dlg = win.findChild(CalibrationDialog)
        dlg.close()
        qapp.processEvents()
        assert win.press_hook is None
        win.controller.record_crossing = mock.Mock()
        win.on_evdev_crossing(5.0, 57)
        win.controller.record_crossing.assert_called_once()
    finally:
        _teardown(win, ctl)


def test_calibrate_refused_during_race(qapp, config, storage, buffer):
    win, ctl = _build(config, storage, buffer, qapp)
    try:
        win._last_state = AppState.RECORDING
        win._calibrate()
        qapp.processEvents()
        assert win.findChild(CalibrationDialog) is None
        assert win.press_hook is None
    finally:
        _teardown(win, ctl)


def test_hook_bypassed_while_armed(qapp, config, storage, buffer):
    win, ctl = _build(config, storage, buffer, qapp)
    try:
        hook = mock.Mock()
        win.press_hook = hook
        win.controller.start_race = mock.Mock()
        win.session.arm()
        win.on_evdev_crossing(5.0, 57)
        hook.assert_not_called()
        win.controller.start_race.assert_called_once()
    finally:
        _teardown(win, ctl)


def test_dialog_evdev_flag(qapp, config, storage, buffer):
    win, ctl = _build(config, storage, buffer, qapp)
    try:
        win.evdev_active = True
        win._calibrate()
        qapp.processEvents()
        dlg = win.findChild(CalibrationDialog)
        assert dlg is not None
        assert dlg.reaction.evdev is True
    finally:
        _teardown(win, ctl)
