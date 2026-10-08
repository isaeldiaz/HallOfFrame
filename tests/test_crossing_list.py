"""CrossingList tests (spec §7.3, §13.2).

The list has one fixed order — fastest at top, slowest at bottom — on every
screen, with no parameter to change it. These tests pin that down plus the
``remove``/``count`` contract and the read-only variant's lack of edit fields.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit

from hallofframe.ui.crossing_list import CrossingList

pytestmark = pytest.mark.qt


def _data(seq: int, elapsed: float, bow: str = "") -> dict:
    return {"sequence": seq, "elapsed_s": elapsed, "image_path": None,
            "image_flag": None, "suspect": False, "bow": bow}


def _display_order(lst: CrossingList) -> list[int]:
    order = []
    for i in range(lst._v.count()):
        w = lst._v.itemAt(i).widget()
        if w is not None:
            order.append(w.sequence)
    return order


def test_rows_render_fastest_first_regardless_of_insertion_order(qapp):
    lst = CrossingList(editable=False)
    # Inserted slowest, fastest, middle — display must be by elapsed ascending.
    for seq, elapsed in [(1, 30.0), (2, 10.0), (3, 20.0)]:
        lst.add(_data(seq, elapsed))
    assert _display_order(lst) == [2, 3, 1]
    lst.deleteLater()


def test_editable_rows_render_fastest_first(qapp):
    lst = CrossingList(editable=True)
    for seq, elapsed in [(1, 5.0), (2, 1.0), (3, 3.0)]:
        lst.add(_data(seq, elapsed))
    assert _display_order(lst) == [2, 3, 1]
    lst.deleteLater()


def test_refresh_time_reorders_fastest_first(qapp):
    # Correcting a time can change the crossing's rank; the list must re-sort so
    # it stays fastest-first (spec §7.3).
    lst = CrossingList(editable=True)
    for seq, elapsed in [(1, 10.0), (2, 20.0), (3, 30.0)]:
        lst.add(_data(seq, elapsed))
    assert _display_order(lst) == [1, 2, 3]
    lst.refresh_time(1, 99.0)  # crossing 1 is now the slowest
    assert _display_order(lst) == [2, 3, 1]
    lst.deleteLater()


def test_remove_and_count_agree(qapp):
    lst = CrossingList(editable=True)
    for seq in (1, 2, 3):
        lst.add(_data(seq, float(seq)))
    assert lst.count() == 3
    lst.remove(2)
    assert lst.count() == 2
    assert 2 not in lst._rows
    lst.remove(2)  # removing an absent row is a no-op
    assert lst.count() == 2
    lst.clear()
    assert lst.count() == 0
    lst.deleteLater()


def test_read_only_list_shows_no_line_edits(qapp):
    lst = CrossingList(editable=False)
    lst.add(_data(1, 5.0, bow="7"))
    assert lst.findChildren(QLineEdit) == []
    lst.deleteLater()


def test_editable_list_shows_bow_and_time_fields(qapp):
    lst = CrossingList(editable=True)
    lst.add(_data(1, 5.0, bow="7"))
    assert len(lst.findChildren(QLineEdit)) == 2  # a time and a bow field
    lst.deleteLater()


def test_row_numbers_are_time_ordered_positions_not_sequences(qapp):
    # The operator sees finishing position, not the database sequence: a row's
    # number is its fastest-first rank.
    lst = CrossingList(editable=False)
    for seq, elapsed in [(1, 30.0), (2, 10.0), (3, 20.0)]:
        lst.add(_data(seq, elapsed))
    assert lst.position_of(2) == 1
    assert lst.position_of(3) == 2
    assert lst.position_of(1) == 3
    assert lst._rows[2].seq_lbl.text() == "001"
    assert lst._rows[1].seq_lbl.text() == "003"
    lst.deleteLater()


def test_a_clone_sharing_its_parent_time_lands_right_after_it(qapp):
    # A clone copies its parent's time; ties fall back to sequence so the clone
    # takes the next position.
    lst = CrossingList(editable=False)
    lst.add(_data(1, 5.0))
    lst.add(_data(2, 5.0))  # clone of 1
    assert lst.position_of(1) == 1
    assert lst.position_of(2) == 2
    lst.deleteLater()


def test_bow_field_claims_escape_and_emits_escape(qapp):
    # Esc inside a field must release the field, not reach the application-wide
    # "back to Ready" shortcut.
    lst = CrossingList(editable=True)
    lst.add(_data(1, 5.0))
    bow = lst._rows[1].bow_edit
    got = []
    lst.escape_pressed.connect(lambda: got.append(True))
    override = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Escape, Qt.NoModifier)
    qapp.sendEvent(bow, override)
    assert override.isAccepted(), "Esc must be claimed from the shortcut map"
    qapp.sendEvent(bow, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert got == [True]
    lst.deleteLater()
