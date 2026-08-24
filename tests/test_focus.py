"""Focus regression (REDESIGN-PLAN §4): no QPushButton may be keyboard-focusable.

QPushButton takes focus on click and keeps it; Space/Return then activate the
button and lose the race against the app's shortcuts. Every button in the app
must be ``Qt.NoFocus`` so it is click-only and can never steal a key event.

Skips cleanly if PySide6/display is unavailable (offscreen).
"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from hallofframe.config import Config
from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer
from hallofframe.storage import Storage
from hallofframe.ui.main_window import MainWindow

try:
    _QT = QApplication.instance() is not None or bool(QApplication([]))
except Exception:  # pragma: no cover
    _QT = False


def _make_config(data_root):
    data = {
        "paths": {"data_root": str(data_root)},
        "stream": {"assumed_fps": 30, "buffer_seconds": 10.0},
        "timing": {"viewing_mode": "screen", "reaction_offset_ms": 0.0,
                   "debounce_ms": 20, "start_mode": "direct", "radio_delay_ms": 0.0},
        "capture": {"window_before_ms": 50, "window_after_ms": 50},
        "archive": {"enabled": False, "every_nth_frame": 1},
        "ui": {"finish_line_x": 0.5, "preview_fps": 10},
    }
    return Config(data=data, path=Path(data_root) / "config.toml")


def _collect_buttons(widget, out):
    for child in widget.findChildren(QPushButton):
        out.append(child)


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestButtonFocusPolicy(unittest.TestCase):
    def test_all_main_window_buttons_are_no_focus(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            storage = Storage(Path(tmp))
            buffer = FrameBuffer(assumed_fps=30)
            controller = CaptureController(config, storage, buffer)
            win = MainWindow(config, controller, buffer)
            buttons = []
            _collect_buttons(win, buttons)
            self.assertGreaterEqual(len(buttons), 1,
                                    "expected the key bar to expose buttons")
            for btn in buttons:
                self.assertEqual(btn.focusPolicy(), Qt.NoFocus,
                                 f"button {btn.text()!r} is keyboard-focusable")
                self.assertFalse(btn.autoDefault())
            controller.stop()
            storage.close()
            win.close()


if __name__ == "__main__":
    unittest.main()
