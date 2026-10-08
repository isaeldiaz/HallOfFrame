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

Skips cleanly if PySide6 is unavailable (offscreen otherwise).
"""
import time
import unittest
from unittest import mock

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtWidgets import QLineEdit

from hallofframe.controller import CaptureController
from hallofframe.render import format_elapsed
from hallofframe.session import Phase
from hallofframe.ui.state import AppState

SCREEN_W = 1920  # the deployed panel (system-environment.md §2.4)
SCREEN_H = 1080
FRAMES = 30      # a +/-500 ms window at 30 fps

pytestmark = pytest.mark.qt


@pytest.fixture
def review_screen_env(request, data_root, config, storage, buffer, qapp):
    from hallofframe.ui.main_window import MainWindow

    inst = request.instance
    root = data_root
    (root / "caps").mkdir()
    frame = QImage(640, 360, QImage.Format_RGB888)
    frame.fill(QColor("#334455"))
    frame.save(str(root / "caps" / "f.jpg"), "JPG")

    inst.app = qapp
    inst.data_root = root
    inst.config = config(
        capture={"window_before_ms": 500, "window_after_ms": 500},
        trigger={"device_path": "", "start_keycodes": [28],
                 "end_keycodes": [88], "crossing_keycodes": [57],
                 "grab_device": False},
        races={"csv_path": str(root / "races.csv")},
    )
    inst.storage = storage
    inst.buffer = buffer
    inst.buffer.health = lambda: (True, 30.0, 0.1)
    inst.controller = CaptureController(inst.config, inst.storage, inst.buffer)
    inst.race_id = inst.storage.create_race("R", 0.0, time.time(), "direct",
                                            0.0, 0.0, "screen",
                                            window_before_ms=500,
                                            window_after_ms=500)
    for seq in (1, 2, 3):
        target_ms = seq * 1000
        cap = inst.storage.insert_capture(inst.race_id, seq, float(seq),
                                          time.time(), float(seq) * 10, 0.0,
                                          target_ms=target_ms)
        t_ms = [target_ms + round(-500.0 + i * (1000.0 / (FRAMES - 1)))
                for i in range(FRAMES)]
        frame_rows = inst.storage.insert_frames(
            [(inst.race_id, ms, ms / 1000.0, "caps/f.jpg") for ms in t_ms])
        inst.storage.set_primary(cap, frame_rows[FRAMES // 2]["id"])
    inst.controller.race_id = inst.race_id

    inst.win = MainWindow(inst.config, inst.controller, inst.buffer)
    inst.win.setFixedSize(SCREEN_W, SCREEN_H)
    inst.win.show()
    qapp.processEvents()
    yield
    # The 500 ms status timer polls disk usage under data_root; stop it before
    # the temp dir goes away.
    inst.win.status_timer.stop()
    inst.controller.stop()
    inst.win.close()


@pytest.mark.usefixtures("review_screen_env")
class TestReviewScreen(unittest.TestCase):
    # --- helpers ---------------------------------------------------------
    def review(self):
        self.win._open_review()
        self.app.processEvents()
        return self.win._review_screen

    def key(self, key, modifier=Qt.NoModifier, text=""):
        target = self.app.focusWidget() or self.win
        self.app.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, modifier, text))
        self.app.processEvents()

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

    def test_crossing_list_minimum_width_fits_the_screen(self):
        screen = self.review()
        self.assertLessEqual(screen.minimumSizeHint().width(), SCREEN_W,
                             f"review page cannot fit the {SCREEN_W}px panel")
        edits = screen.list.findChildren(QLineEdit)
        self.assertEqual(len(edits), 6, "a time and a bow field per crossing")

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
        cap = self.storage.capture(capture_id)
        self.assertEqual(cap["primary_frame_id"], chosen["id"],
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
        self.assertIsNot(self.win.session.phase, Phase.ARMED,
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

    @pytest.mark.slow
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
        race_keys = ("Return", "Enter", "SPACE")
        self.assertTrue(all(not self.win._shortcuts[k].isEnabled()
                            for k in race_keys),
                        "Enter/Space must not fire while REVIEW is on screen")
        self.win._close_review()
        self.app.processEvents()
        self.assertTrue(all(self.win._shortcuts[k].isEnabled()
                            for k in race_keys),
                        "race controls must come back on leaving REVIEW")

    def test_typing_silences_the_typable_shortcuts(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Tab)
        self.assertIsInstance(self.app.focusWidget(), QLineEdit)
        typable = ("C", "E", "L", "D", "R", "N", "/", "End",
                   "Return", "Enter", "SPACE")
        self.assertTrue(all(not self.win._shortcuts[k].isEnabled()
                            for k in typable),
                        "a focused text field must get its own characters")

    # --- 5. navigation must survive a focused bow field -------------------
    def test_shift_arrow_and_up_down_work_from_a_bow_field(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Tab)                       # land in crossing 1's bow
        self.assertIsInstance(self.app.focusWidget(), QLineEdit)
        first = screen._selected_seq
        capture_id = screen._current_capture["id"]

        chosen = screen.scrubber.selected_frame()
        self.key(Qt.Key_Right, Qt.ShiftModifier)   # step the frame in text mode
        stepped = screen.scrubber.selected_frame()
        self.assertNotEqual(stepped["id"], chosen["id"],
                            "Shift+Right in a bow field did not step the frame")
        self.key(Qt.Key_Tab)                       # commit it and advance
        self.assertIsInstance(self.app.focusWidget(), QLineEdit)
        cap = self.storage.capture(capture_id)
        self.assertEqual(cap["primary_frame_id"], stepped["id"],
                         "Tab after text-mode stepping did not promote the frame")
        after_tab = screen._selected_seq
        self.assertNotEqual(after_tab, first, "Tab in a bow field did not advance")

        self.key(Qt.Key_Down)                      # change the selection
        self.assertNotEqual(screen._selected_seq, after_tab,
                            "Down in a bow field did not change the crossing")
        self.assertIsInstance(self.app.focusWidget(), QLineEdit,
                              "navigation from a bow field must stay in a bow field")

    def test_empty_race_clears_the_previous_selection(self):
        screen = self.review()
        self.assertIsNotNone(screen._selected_seq)
        empty = self.storage.create_race(
            "Empty", 0.0, time.time(), "direct", 0.0, 0.0, "screen",
            window_before_ms=500, window_after_ms=500)
        screen.race_id = empty
        screen.load_captures()
        self.assertIsNone(screen._selected_seq)
        self.assertEqual(screen.counter.text(), "")
        self.assertIn("no crossings", screen.photo.text())

    def test_up_moves_toward_faster_rows(self):
        # The list is fastest-first, so Up selects the row above (faster) and
        # Down the row below (slower).
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        screen._select(2)
        self.key(Qt.Key_Up)
        self.assertEqual(screen._selected_seq, 1)
        self.key(Qt.Key_Down)
        self.assertEqual(screen._selected_seq, 2)
        self.key(Qt.Key_Down)
        self.assertEqual(screen._selected_seq, 3)

    def test_save_shows_confirmation(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Tab)
        self.assertEqual(screen.saved_lbl.text(), "saved",
                         "committing a frame must show a visible confirmation")
        screen._saved_timer.stop()  # don't leave a pending timer in the test

    def test_committed_frame_sticks_when_the_crossing_is_revisited(self):
        """The UI must show the operator-chosen primary, not re-derive the
        frame nearest the recorded time, after navigating away and back."""
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Right, Qt.ShiftModifier)
        self.key(Qt.Key_Right, Qt.ShiftModifier)
        stepped = screen.scrubber.selected_frame()
        self.key(Qt.Key_Return)        # commit + advance to crossing 2
        self.key(Qt.Key_Up)            # back to crossing 1 (faster, above)
        shown = screen.scrubber.selected_frame()
        self.assertEqual(shown["id"], stepped["id"],
                         "revisiting a crossing must show the committed frame")
        self.assertEqual(screen.offset_lbl.text(),
                         f"{stepped['offset_ms']:+.0f} ms",
                         "photo pane must reflect the committed frame")

    # --- 4. the rows the save writes into -------------------------------
    def test_captures_are_writable_rows(self):
        screen = self.review()
        self.assertTrue(screen._commit_selected_frame(),
                        "commit failed (sqlite3.Row is not writable)")
        row = next(c for c in screen._captures
                   if c["id"] == screen._current_capture["id"])
        self.assertTrue(row["primary_image"])

    # --- 5. editing a crossing time --------------------------------------
    def times(self):
        return {c["sequence"]: c["elapsed_s"]
                for c in self.storage.captures_for_race(self.race_id)}

    def test_time_field_edits_and_persists(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        te = screen.list._rows[seq].time_edit
        te.setFocus()
        te.clear()
        for ch in "99.500":
            key = Qt.Key_Period if ch == "." else getattr(Qt, f"Key_{ch}")
            self.app.sendEvent(te, QKeyEvent(
                QEvent.KeyPress, key, Qt.NoModifier, ch))
        self.app.processEvents()
        self.key(Qt.Key_Return)          # commits via editingFinished + advances
        self.assertAlmostEqual(self.times()[seq], 99.5, places=3,
                               msg="edited time was not persisted")

    def test_invalid_time_is_reverted(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        before = self.times()[seq]
        te = screen.list._rows[seq].time_edit
        te.setFocus()
        te.clear()
        for ch in "ABC":
            self.app.sendEvent(te, QKeyEvent(
                QEvent.KeyPress, getattr(Qt, f"Key_{ch}"), Qt.NoModifier, ch.lower()))
        self.app.processEvents()
        self.key(Qt.Key_Return)
        self.assertEqual(self.times()[seq], before,
                         "an invalid edit must not change the stored time")

    def test_advancing_through_a_row_leaves_elapsed_and_updated_at_untouched(self):
        # A stored time keeps full precision; the field displays only M:SS.cc.
        # Qt emits editingFinished on Return/Enter (the operator's advance key)
        # even when nothing was typed, so advancing through a row must not write
        # the rounded display back, nor churn updated_at (which the web "Results
        # updated"/ETag read). The time's window holds no frames, so the advance
        # save-frame path is a no-op and any write must come from the time edit.
        cap_id = self.storage.insert_capture(
            self.race_id, 4, 372.4837, time.time(), 372.4837, 0.0,
            target_ms=372484)
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        screen._select(4)
        self.app.processEvents()
        before = dict(self.storage.capture(cap_id))
        te = screen.list._rows[4].time_edit
        self.assertEqual(te.text(), format_elapsed(372.4837))
        te.setFocus()
        self.app.processEvents()
        self.key(Qt.Key_Return)       # commits editingFinished without an edit
        after = dict(self.storage.capture(cap_id))
        self.assertEqual(after["elapsed_s"], before["elapsed_s"])
        self.assertEqual(after["updated_at"], before["updated_at"],
                         "advancing through a row must not bump updated_at")

    # --- 6. remove / restore / clone (plan step 7.2) ---------------------
    def test_delete_key_reaches_the_controller_and_strikes_the_row(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        capture_id = screen._current_capture["id"]
        with mock.patch.object(self.controller, "remove") as rm:
            self.key(Qt.Key_Delete)
        rm.assert_called_once_with(capture_id)
        row = screen.list._rows[seq]
        self.assertTrue(row.deleted, "the row must stay, marked deleted")
        self.assertTrue(row.seq_lbl.font().strikeOut(),
                        "a deleted row must render struck through")

    def test_restore_key_reaches_the_controller(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        capture_id = screen._current_capture["id"]
        screen.remove_selected()             # soft-delete for real
        self.assertTrue(screen.list._rows[seq].deleted)
        with mock.patch.object(self.controller, "restore") as rs:
            self.key(Qt.Key_U)
        rs.assert_called_once_with(capture_id)
        self.assertFalse(screen.list._rows[seq].deleted)
        self.assertFalse(screen.list._rows[seq].seq_lbl.font().strikeOut())

    def test_shift_delete_restores_the_controller_row(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        capture_id = screen._current_capture["id"]
        screen.remove_selected()
        with mock.patch.object(self.controller, "restore") as rs:
            self.key(Qt.Key_Delete, Qt.ShiftModifier)
        rs.assert_called_once_with(capture_id)
        self.assertFalse(screen.list._rows[seq].deleted)

    def test_u_undoes_deletions_newest_first(self):
        # A longer history than one: repeated U walks back through the deletions
        # in reverse order.
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seqs = [c["sequence"] for c in screen._captures]
        for seq in seqs:
            screen._delete(seq)
        self.assertTrue(all(screen.list._rows[s].deleted for s in seqs))
        for expected in reversed(seqs):
            self.key(Qt.Key_U)
            self.assertFalse(screen.list._rows[expected].deleted,
                             f"U must restore sequence {expected} next")
        self.assertEqual(screen._undo_stack, [])

    def test_escape_in_a_bow_field_releases_focus_without_leaving_review(self):
        # The operator types a bow, then needs Del/Ins again: Esc must drop the
        # field (and the app-wide Esc shortcut must not fire).
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        bow = screen.list._rows[seq].bow_edit
        bow.setFocus()
        self.app.processEvents()
        self.assertTrue(bow.hasFocus())
        self.app.sendEvent(bow, QKeyEvent(
            QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.app.processEvents()
        self.assertFalse(bow.hasFocus(), "Esc must release the field")
        self.assertEqual(self.win.session.phase, Phase.REVIEW,
                         "Esc in a field must not close REVIEW")
        capture_id = screen._current_capture["id"]
        with mock.patch.object(self.controller, "remove") as rm:
            self.key(Qt.Key_Delete)
        rm.assert_called_once_with(capture_id)

    def test_insert_key_clones_the_controller_row(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        capture_id = screen._current_capture["id"]
        with mock.patch.object(self.controller, "clone") as cl:
            cl.return_value = None
            self.key(Qt.Key_Insert)
        cl.assert_called_once_with(capture_id)

    def test_shift_d_clones_the_controller_row(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        capture_id = screen._current_capture["id"]
        with mock.patch.object(self.controller, "clone") as cl:
            cl.return_value = None
            self.key(Qt.Key_D, Qt.ShiftModifier)
        cl.assert_called_once_with(capture_id)

    def test_deleted_rows_render_struck_through(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        self.key(Qt.Key_Delete)              # real soft delete
        row = screen.list._rows[seq]
        self.assertTrue(row.deleted)
        for widget in (row.seq_lbl, row.flag_lbl, row.time_edit, row.bow_edit):
            self.assertTrue(widget.font().strikeOut(),
                            "every text widget on a deleted row is struck through")

    def test_time_edit_to_no_frames_shows_the_no_frames_label(self):
        screen = self.review()
        screen.setFocus()
        self.app.processEvents()
        seq = screen._selected_seq
        screen._time_edited(seq, "99.500")
        self.assertEqual(screen._current_capture["image_flag"], "missing")
        self.assertEqual(screen.photo.text(), "no frames at this time")


if __name__ == "__main__":
    unittest.main()
