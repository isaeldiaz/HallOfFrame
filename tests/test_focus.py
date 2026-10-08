"""Focus regression (REDESIGN-PLAN §4): no QPushButton may be keyboard-focusable.

QPushButton takes focus on click and keeps it; Space/Return then activate the
button and lose the race against the app's shortcuts. Every button in the app
must be ``Qt.NoFocus`` so it is click-only and can never steal a key event.

Skips cleanly if PySide6/display is unavailable (offscreen).
"""
import unittest

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from hallofframe.controller import CaptureController
from hallofframe.ui.main_window import MainWindow

pytestmark = pytest.mark.qt


def _collect_buttons(widget, out):
    for child in widget.findChildren(QPushButton):
        out.append(child)


@pytest.fixture
def focus_env(request, data_root, config, storage, buffer, qapp):
    inst = request.instance
    inst.data_root = data_root
    inst.config = config()
    inst.storage = storage
    inst.buffer = buffer
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer)
    inst.win = MainWindow(inst.config, inst.controller, inst.buffer)
    yield
    inst.controller.stop()
    inst.win.close()


@pytest.mark.usefixtures("focus_env")
class TestButtonFocusPolicy(unittest.TestCase):
    def test_all_main_window_buttons_are_no_focus(self):
        buttons = []
        _collect_buttons(self.win, buttons)
        self.assertGreaterEqual(len(buttons), 1,
                                "expected the key bar to expose buttons")
        for btn in buttons:
            self.assertEqual(btn.focusPolicy(), Qt.NoFocus,
                             f"button {btn.text()!r} is keyboard-focusable")
            self.assertFalse(btn.autoDefault())


if __name__ == "__main__":
    unittest.main()
