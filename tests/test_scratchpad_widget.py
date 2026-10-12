"""ScratchpadWidget tests (spec §13.3 step 05)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton

from hallofframe.ui import styles
from hallofframe.ui.scratchpad_widget import ScratchpadWidget

pytestmark = pytest.mark.qt


def press(field, key, text=""):
    app = QApplication.instance()
    ev = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text)
    app.sendEvent(field, ev)
    app.processEvents()


def test_enter_commits_digits(qapp):
    w = ScratchpadWidget()
    got = []
    w.committed.connect(got.append)
    press(w.field, Qt.Key_1, "1")
    press(w.field, Qt.Key_4, "4")
    press(w.field, Qt.Key_Return)
    assert got == ["14"]
    assert w.field.text() == ""
    w.deleteLater()


def test_enter_on_empty_flashes_only(qapp):
    w = ScratchpadWidget()
    got = []
    w.committed.connect(got.append)
    press(w.field, Qt.Key_Return)
    assert got == []
    assert styles.RED in w.styleSheet()
    w.deleteLater()


def test_ctrl_z_not_claimed(qapp):
    w = ScratchpadWidget()
    event = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Z, Qt.ControlModifier)
    qapp.sendEvent(w.field, event)
    assert not event.isAccepted()
    w.deleteLater()


def test_return_is_claimed(qapp):
    w = ScratchpadWidget()
    event = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Return, Qt.NoModifier)
    qapp.sendEvent(w.field, event)
    assert event.isAccepted()
    w.deleteLater()


def test_letters_rejected_by_validator(qapp):
    w = ScratchpadWidget()
    w.field.insert("a")
    assert w.field.text() == ""
    w.deleteLater()


def test_backspace_on_empty_requests_undo(qapp):
    w = ScratchpadWidget()
    got = []
    w.undo_requested.connect(lambda: got.append(True))
    press(w.field, Qt.Key_Backspace)
    assert got == [True]
    w.deleteLater()


def test_backspace_autorepeat_ignored(qapp):
    w = ScratchpadWidget()
    got = []
    w.undo_requested.connect(lambda: got.append(True))
    press(w.field, Qt.Key_Backspace)
    for _ in range(5):
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier,
                       "", True)
        qapp.sendEvent(w.field, ev)
    qapp.processEvents()
    assert got == [True]
    w.deleteLater()


def test_backspace_with_text_edits(qapp):
    w = ScratchpadWidget()
    got = []
    w.undo_requested.connect(lambda: got.append(True))
    press(w.field, Qt.Key_1, "1")
    press(w.field, Qt.Key_4, "4")
    press(w.field, Qt.Key_Backspace)
    assert w.field.text() == "1"
    assert got == []
    w.deleteLater()


def test_counts_red_on_mismatch(qapp):
    w = ScratchpadWidget()
    w.set_counts(2, 3)
    assert w.typed_lbl.text() == "2"
    assert w.cross_lbl.text() == "3"
    assert w.mismatch() is True
    assert styles.RED_TEXT in w.typed_lbl.styleSheet()
    w.deleteLater()


def test_counts_plain_when_equal(qapp):
    w = ScratchpadWidget()
    w.set_counts(3, 3)
    assert w.mismatch() is False
    w.deleteLater()


def test_no_focusable_buttons(qapp):
    w = ScratchpadWidget()
    assert w.findChildren(QPushButton) == []
    w.deleteLater()
