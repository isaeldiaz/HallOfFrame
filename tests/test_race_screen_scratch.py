"""Race screen bow column + scratchpad slot (plan step 06, §13.3).

The read-only crossing list shows each row's bow number live; the race screen
can host the scratchpad under the list and re-focus it as crossings arrive.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from PySide6.QtWidgets import QLineEdit

from hallofframe.ui.crossing_list import CrossingList
from hallofframe.ui.race_screen import RaceScreen
from hallofframe.ui.scratchpad_widget import ScratchpadWidget

pytestmark = pytest.mark.qt


def _data(seq: int, elapsed: float = 5.0, bow: str = "") -> dict:
    return {"sequence": seq, "elapsed_s": elapsed, "image_path": None,
            "image_flag": None, "suspect": False, "bow": bow}


def test_readonly_row_shows_bow(qapp):
    lst = CrossingList(editable=False)
    lst.add(_data(1, 5.0, bow="14"))
    assert lst._rows[1].bow_lbl.text() == "14"
    lst.deleteLater()


def test_refresh_bows(qapp):
    lst = CrossingList(editable=False)
    lst.add(_data(1, 5.0))
    lst.add(_data(2, 6.0))
    lst.refresh_bows({1: "14", 2: None})
    assert lst._rows[1].bow_lbl.text() == "14"
    assert lst._rows[2].bow_lbl.text() == ""
    lst.deleteLater()


def test_editable_refresh_does_not_clobber_focused_field(qapp):
    lst = CrossingList(editable=True)
    lst.add(_data(1, 5.0))
    lst.show()
    qapp.processEvents()
    lst.focus_bow(1)
    qapp.processEvents()
    assert lst._rows[1].bow_edit.hasFocus()
    before = lst._rows[1].bow_edit.text()
    lst.refresh_bow(1, "99")
    assert lst._rows[1].bow_edit.text() == before
    lst.deleteLater()


def test_scratchpad_slot_and_focus(qapp):
    screen = RaceScreen()
    widget = ScratchpadWidget()
    screen.set_scratchpad(widget)
    screen.show()
    screen.begin(0.0)
    screen.add_capture(_data(1))
    qapp.processEvents()
    assert qapp.focusWidget() is widget.field
    screen.deleteLater()


def test_no_focus_steal_when_not_running(qapp):
    screen = RaceScreen()
    widget = ScratchpadWidget()
    screen.set_scratchpad(widget)
    screen.show()
    screen.activateWindow()
    other = QLineEdit(screen)
    other.show()
    other.setFocus()
    qapp.processEvents()
    assert qapp.focusWidget() is other
    screen.add_capture(_data(1))
    qapp.processEvents()
    assert qapp.focusWidget() is other
    other.deleteLater()
    screen.deleteLater()


def test_bow_column_hidden_when_disabled(qapp):
    lst = CrossingList(editable=False, show_bow=False)
    lst.add(_data(1, 5.0, bow="14"))
    assert not hasattr(lst._rows[1], "bow_lbl")
    lst.refresh_bows({1: "14"})   # must be a no-op, not an AttributeError
    lst.deleteLater()


def test_screen_minimum_width_unchanged(qapp):
    screen = RaceScreen()
    screen.set_scratchpad(ScratchpadWidget())
    assert screen.minimumSizeHint().width() <= 1920
    screen.deleteLater()
