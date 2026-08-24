"""REVIEW state regressions (REDESIGN-PLAN §6) — the four faults found on the
1920x1080 deployment, where the crossing list and its bow fields were invisible
and neither Tab nor Enter moved to the next crossing:

1. Qt sizes a window up to its layout's minimum, ignoring the screen. The review
   page's minimum was 2366 px (a scrubber tick per window frame, ~1600 px, plus a
   hard 700 px list), and other pages went to 2542 px, so the full-screen window
   was ~600 px wider than the panel and the list hung off the right edge.
2. Tab never reached ``keyPressEvent`` — ``QWidget::event()`` spends it on focus
   navigation first.
3. Enter never reached the screen either: it is an application-wide shortcut
   (race start), and those beat the focused widget.
4. ``_commit_selected_frame`` wrote into a ``sqlite3.Row``, so the save that Tab
   and Enter perform raised TypeError and the advance never ran.

Skips cleanly if PySide6 or evdev is unavailable (offscreen otherwise).
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtWidgets import QApplication, QLineEdit

from hallofframe.config import Config
from hallofframe.controller import CaptureController
from hallofframe.framebuffer import FrameBuffer
from hallofframe.storage import Storage
from hallofframe.ui.state import AppState

try:
    _QT = QApplication.instance() is not None or bool(QApplication([]))
except Exception:  # pragma: no cover
    _QT = False

try:  # main_window resolves trigger keycodes through evdev.ecodes
    import evdev  # noqa: F401
    _EVDEV = True
except Exception:  # pragma: no cover
    _EVDEV = False

SCREEN_W = 1920  # the deployed panel (system-environment.md §2.4)
SCREEN_H = 1080
FRAMES = 30      # a +/-500 ms window at 30 fps


def _config(data_root):
    data = {
        "paths": {"data_root": str(data_root)},
        "stream": {"assumed_fps": 30, "buffer_seconds": 10.0},
        "timing": {"viewing_mode": "screen", "reaction_offset_ms": 0.0,
                   "debounce_ms": 20, "start_mode": "direct",
                   "radio_delay_ms": 0.0},
        "capture": {"window_before_ms": 500, "window_after_ms": 500},
        "archive": {"enabled": False, "every_nth_frame": 1},
        "trigger": {"device_path": "", "start_keycodes": [28],
                    "end_keycodes": [88], "crossing_keycodes": [57],
                    "grab_device": False},
        "races": {"excel_path": str(Path(data_root) / "races.xlsx")},
        "ui": {"finish_line_x": 0.5, "preview_fps": 10},
    }
    return Config(data=data, path=Path(data_root) / "config.toml")


@unittest.skipUnless(_QT and _EVDEV, "PySide6/evdev unavailable")
class TestReviewScreen(unittest.TestCase):
    def setUp(self):
        from hallofframe.ui.main_window import MainWindow

        self.app = QApplication.instance()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "caps").mkdir()
        frame = QImage(640, 360, QImage.Format_RGB888)
        frame.fill(QColor("#334455"))
        frame.save(str(root / "caps" / "f.jpg"), "JPG")

        self.config = _config(root)
        self.storage = Storage(root)
        self.buffer = FrameBuffer(assumed_fps=30)
        # REVIEW is only reachable with a live stream (STREAM_DOWN outranks it).
        self.buffer.health = lambda: (True, 30.0, 0.1)
        self.controller = CaptureController(self.config, self.storage, self.buffer)
        self.race_id = self.storage.create_race("R", 0.0, time.time(), "direct",
                                                0.0, 0.0, "screen")
        for seq in (1, 2, 3):
            cap = self.storage.insert_capture(self.race_id, seq, float(seq),
                                              time.time(), float(seq) * 10, 0.0)
            for i in range(FRAMES):
                offset = -500.0 + i * (1000.0 / (FRAMES - 1))
                frame_id = self.storage.insert_frame(cap, 0.0, offset, "caps/f.jpg")
                if i == FRAMES // 2:
                    self.storage.set_primary(cap, frame_id)
        self.controller.race_id = self.race_id

        self.win = MainWindow(self.config, self.controller, self.buffer)
        self.win.setFixedSize(SCREEN_W, SCREEN_H)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        # The 500 ms status timer polls disk usage under data_root; stop it
        # before the temp dir goes away.
        self.win.status_timer.stop()
        self.controller.stop()
        self.storage.close()
        self.win.close()
        self._tmp.cleanup()

    # --- helpers ---------------------------------------------------------
    def review(self):
        self.win._open_review()
        self.app.processEvents()
        return self.win._review_screen

    def key(self, key, modifier=Qt.NoModifier, text=""):
        target = self.app.focusWidget() or self.win
        self.app.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, modifier, text))
        self.app.processEvents()

    def right_edge(self, widget):
        return widget.mapTo(self.win, widget.rect().topLeft()).x() + widget.width()

    def bows(self):
        return {row["sequence"]: row["bow_number"]
                for row in self.storage.captures_for_race(self.race_id)}

    # --- 1. nothing may be wider than the screen -------------------------
    def test_no_page_forces_the_window_wider_than_the_screen(self):
        pages = {"ready": self.win.ready, "armed": self.win.armed,
                 "recording": self.win.recording, "race_over": self.win.race_over,
                 "review": self.review()}
        for name, page in pages.items():
            self.assertLessEqual(
                page.minimumSizeHint().width(), SCREEN_W,
                f"{name} page cannot fit the {SCREEN_W}px panel")
        for state in (AppState.READY, AppState.REVIEW):
            self.win._apply_state(state)
            self.app.processEvents()
            self.assertLessEqual(
                self.win.minimumSizeHint().width(), SCREEN_W,
                f"window minimum exceeds the screen in {state.name}")

    def test_crossing_list_and_bow_fields_are_on_screen(self):
        screen = self.review()
        self.assertLessEqual(self.right_edge(screen.panel), SCREEN_W)
        edits = screen.list.findChildren(QLineEdit)
        self.assertEqual(len(edits), 3, "one bow field per crossing")
        for edit in edits:
            self.assertLessEqual(self.right_edge(edit), SCREEN_W,
                                 "bow field is off the right edge of the screen")
            self.assertTrue(edit.isVisible())

    def test_photo_is_scaled_to_the_pane_it_ends_up_in(self):
        screen = self.review()
        pixmap = screen.photo.pixmap()
        self.assertFalse(pixmap.isNull(), "no frame shown")
        self.assertLessEqual(pixmap.width(), screen.photo.width())
        # Stale-size regression: the frame was loaded before the screen was laid
        # out, so it must have been re-scaled to the pane it is now in.
        self.assertGreater(pixmap.width(), screen.photo.width() // 2)

    # --- 2./4. Tab saves the frame and lands in the bow field ------------
    def test_tab_saves_the_selected_frame_and_focuses_the_bow(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        selected = screen._selected_seq
        capture_id = screen._current_capture["id"]
        self.key(Qt.Key_Right, Qt.ShiftModifier)   # step off the primary
        chosen = screen.scrubber.selected_frame()
        self.key(Qt.Key_Tab)
        self.assertEqual(screen._selected_seq, selected,
                         "Tab stays on the crossing so the bow can be typed")
        self.assertIsInstance(self.app.focusWidget(), QLineEdit)
        frames = {f["id"]: f for f in self.storage.frames_for_capture(capture_id)}
        self.assertEqual(frames[chosen["id"]]["is_primary"], 1,
                         "the scrubber frame was not promoted to primary")

    # --- 3. Enter reaches the screen and advances -----------------------
    def test_enter_commits_the_bow_and_advances(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        first = screen._selected_seq
        self.key(Qt.Key_Tab)                       # into the bow field
        self.key(Qt.Key_4, text="4")
        self.key(Qt.Key_2, text="2")
        self.key(Qt.Key_Return)
        self.assertEqual(self.bows()[first], "42")
        self.assertNotEqual(screen._selected_seq, first, "Enter did not advance")
        self.assertIsInstance(self.app.focusWidget(), QLineEdit,
                              "advance should land in the next bow field")
        self.assertFalse(self.win._armed,
                         "Enter must not reach the race-start shortcut")

    def test_tab_in_the_bow_field_advances(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Tab)
        first = screen._selected_seq
        self.key(Qt.Key_7, text="7")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.bows()[first], "7")
        self.assertNotEqual(screen._selected_seq, first, "Tab did not advance")

    def test_stepping_frames_keeps_the_selected_tick_in_view(self):
        # The ticks scroll now (that is what stopped them setting the window's
        # minimum width), so stepping has to follow the selection.
        scrubber = self.review().scrubber
        viewport = scrubber._area.viewport()
        for step in range(FRAMES + 1):
            scrubber.step(1)
            self.app.processEvents()
            tick = scrubber._sel_widget
            left = tick.mapTo(viewport, tick.rect().topLeft()).x()
            self.assertGreaterEqual(left, 0, f"tick {step} scrolled off the left")
            self.assertLessEqual(left + tick.width(), viewport.width(),
                                 f"tick {step} scrolled off the right")

    def test_review_silences_the_race_shortcuts(self):
        self.review()
        self.assertTrue(all(not s.isEnabled() for s in self.win._race_shortcuts),
                        "Enter/Space must not fire while REVIEW is on screen")
        self.win._close_review()
        self.app.processEvents()
        self.assertTrue(all(s.isEnabled() for s in self.win._race_shortcuts),
                        "race controls must come back on leaving REVIEW")

    def test_typing_silences_the_typable_shortcuts(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Tab)
        self.assertIsInstance(self.app.focusWidget(), QLineEdit)
        self.assertTrue(all(not s.isEnabled() for s in self.win._typable_shortcuts),
                        "a focused text field must get its own characters")

    # --- 4. the rows the save writes into -------------------------------
    def test_captures_are_writable_rows(self):
        screen = self.review()
        self.assertTrue(screen._commit_selected_frame(),
                        "commit failed (sqlite3.Row is not writable)")
        row = next(c for c in screen._captures
                   if c["id"] == screen._current_capture["id"])
        self.assertTrue(row["primary_image"])


if __name__ == "__main__":
    unittest.main()
