"""MainWindow scratchpad wiring + the arm gate (plan step 07, §13.3).

The window builds and hosts the scratchpad when ``[scratchpad] enabled``,
routes committed numbers through the controller, mirrors counts/bows back to
the widgets, refuses to arm on the laptop's internal keyboard, and toasts a
typed-vs-crossings mismatch at race end.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit

from hallofframe.controller import CaptureController
from hallofframe.session import Phase
from hallofframe.ui.scratchpad_widget import ScratchpadWidget
from hallofframe.ui.state import AppState

pytestmark = pytest.mark.qt


def _build(inst, config, storage, buffer, qapp, enabled: bool) -> None:
    from hallofframe.ui.main_window import MainWindow

    inst.app = qapp
    inst.config = config(
        scratchpad={"enabled": enabled},
        timing={"viewing_mode": "screen", "image_mode": "off"},
        trigger={"device_path": "/dev/input/event-test", "grab_device": False},
    )
    inst.storage = storage
    inst.buffer = buffer
    inst.buffer.health = lambda: (True, 30.0, 0.1)
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer)
    inst.win = MainWindow(inst.config, inst.controller, inst.buffer)
    inst.win.show()
    qapp.processEvents()
    inst.events = []
    inst.controller.events = lambda k, p: inst.events.append((k, p))


def _teardown(inst) -> None:
    inst.win.status_timer.stop()
    inst.controller.stop()
    inst.win.close()


@pytest.fixture
def scratch_env(request, data_root, config, storage, buffer, qapp):
    inst = request.instance
    _build(inst, config, storage, buffer, qapp, True)
    yield
    _teardown(inst)


@pytest.fixture
def scratch_env_disabled(request, data_root, config, storage, buffer, qapp):
    inst = request.instance
    _build(inst, config, storage, buffer, qapp, False)
    yield
    _teardown(inst)


@pytest.mark.usefixtures("scratch_env")
class TestScratchWiring:
    def pump(self):
        self.controller._queue.join()
        for k, p in list(self.events):
            self.win._on_controller_event(k, p)
        self.events.clear()
        self.app.processEvents()

    def start(self):
        self.win._arm_start()
        self.win.on_evdev_start(1000.0)
        self.pump()

    def test_focus_on_recording(self):
        rival = QLineEdit(self.win)
        rival.show()
        rival.setFocus()
        self.app.processEvents()
        assert self.app.focusWidget() is rival
        self.start()
        assert self.win._last_state == AppState.RECORDING
        assert self.app.focusWidget() is self.win.scratchpad.field

    def test_commit_flows_to_db_and_counters(self):
        self.start()
        self.controller.record_crossing(1001.0)
        self.pump()
        self.win.scratchpad.committed.emit("14")
        self.pump()
        rows = self.storage.captures_for_race(self.controller.race_id)
        assert rows[0]["sequence"] == 1
        assert rows[0]["bow_number"] == "14"
        assert self.win.scratchpad.typed_lbl.text() == "1"
        assert self.win.scratchpad.cross_lbl.text() == "1"
        assert self.win.recording.log._rows[1].bow_lbl.text() == "14"

    def test_new_capture_keeps_focus(self):
        self.start()
        rival = QLineEdit(self.win)
        rival.show()
        rival.setFocus()
        self.app.processEvents()
        assert self.app.focusWidget() is rival
        self.controller.record_crossing(1001.0)
        self.pump()
        assert self.app.focusWidget() is self.win.scratchpad.field

    def test_arm_allowed_on_external_device(self, monkeypatch):
        monkeypatch.setattr("hallofframe.trigger.is_internal_keyboard",
                            lambda p: False)
        self.win._arm_start()
        assert self.win.session.phase is Phase.ARMED

    def test_resume_refused_on_internal_keyboard(self, monkeypatch):
        monkeypatch.setattr("hallofframe.trigger.is_internal_keyboard",
                            lambda p: True)
        self.win._resume_race(1)
        assert self.win.session.phase is Phase.IDLE
        assert not self.controller.running
        assert "external trigger" in self.win.toast._label.text()

    def test_uncommitted_text_cleared_between_races(self):
        self.start()
        self.win.scratchpad.field.setText("7")
        self.controller.record_crossing(1001.0)
        self.pump()
        self.controller.end_race()
        self.pump()
        assert self.win.scratchpad.field.text() == ""

    def test_arm_refused_on_internal_keyboard(self, monkeypatch):
        monkeypatch.setattr("hallofframe.trigger.is_internal_keyboard",
                            lambda p: True)
        self.win._arm_start()
        assert self.win.session.phase is Phase.IDLE
        assert "external trigger" in self.win.toast._label.text()

    def test_return_claimed_by_field_in_recording(self):
        self.start()
        event = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Return, Qt.NoModifier)
        self.app.sendEvent(self.win.scratchpad.field, event)
        assert event.isAccepted()
        assert self.win.recording.log.count() == 0
        assert self.controller.running

    def test_mismatch_toast_at_race_end(self):
        self.start()
        self.controller.record_crossing(1001.0)
        self.controller.record_crossing(1002.0)
        self.win.scratchpad.committed.emit("14")
        self.pump()
        self.controller.end_race()
        self.pump()
        assert "1 typed / 2 crossings" in self.win.toast._label.text()

    def test_no_toast_when_counts_match(self):
        self.start()
        self.controller.record_crossing(1001.0)
        self.win.scratchpad.committed.emit("14")
        self.pump()
        self.controller.end_race()
        self.pump()
        assert "Scratchpad" not in self.win.toast._label.text()


@pytest.mark.usefixtures("scratch_env_disabled")
class TestScratchDisabled:
    def test_widget_absent_when_disabled(self):
        assert self.win.scratchpad is None
        assert self.win.recording.findChildren(ScratchpadWidget) == []

    def test_arm_allowed_when_disabled_even_on_internal_keyboard(self, monkeypatch):
        monkeypatch.setattr("hallofframe.trigger.is_internal_keyboard",
                            lambda p: True)
        self.win._arm_start()
        assert self.win.session.phase is Phase.ARMED
