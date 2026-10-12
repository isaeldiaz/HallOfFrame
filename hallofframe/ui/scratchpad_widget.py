"""Live bow-number scratchpad widget (spec §13.3 step 05).

One digits-only field and two counters; it never touches storage or the
controller, it only emits ``committed`` / ``undo_requested``.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRegularExpression, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from hallofframe import scratchpad
from hallofframe.ui import styles


class _Field(QLineEdit):
    """Digits field that claims Enter (commit) and Backspace-on-empty (undo)."""

    def __init__(self, owner: "ScratchpadWidget"):
        super().__init__(owner)
        self._owner = owner

    def event(self, event):
        etype = event.type()
        if etype == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                t = scratchpad.normalize(self.text())
                if t:
                    self._owner.committed.emit(t)
                    self.clear()
                else:
                    self._owner.flash()
                return True
            if key == Qt.Key_Backspace and self.text() == "":
                # One undo per physical press: a held key autorepeats ~30/s
                # and would otherwise soft-delete the whole typed list.
                if not event.isAutoRepeat():
                    self._owner.undo_requested.emit()
                return True
        elif etype == QEvent.ShortcutOverride:
            # A plain QLineEdit would claim Ctrl+Z as text-undo and swallow the
            # app-wide Undo-last-crossing shortcut while the field has focus.
            if event.modifiers() & Qt.ControlModifier:
                event.ignore()
                return False
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                event.accept()
                return True
        return super().event(event)


class ScratchpadWidget(QWidget):
    committed = Signal(str)       # normalized text, on Enter
    undo_requested = Signal()     # Backspace on an EMPTY field

    def __init__(self, parent=None):
        super().__init__(parent)
        self.field = _Field(self)
        self.field.setPlaceholderText("bow")
        self.field.setMaxLength(4)
        self.field.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[0-9]{0,4}"), self))
        self.field.setFixedHeight(64)
        self.field.setFocusPolicy(Qt.StrongFocus)

        self.typed_lbl = QLabel("0")
        self.cross_lbl = QLabel("0")
        for lbl in (self.typed_lbl, self.cross_lbl):
            f = QFont(styles.FONT_MONO)
            f.setPixelSize(28)
            lbl.setFont(f)

        typed_cap = QLabel("typed")
        cross_cap = QLabel("crossings")
        for cap in (typed_cap, cross_cap):
            f = QFont(styles.FONT_SANS)
            f.setPixelSize(14)
            cap.setFont(f)
            cap.setStyleSheet(f"color: {styles.TEXT_FAINT};")

        lay = QHBoxLayout(self)
        lay.addWidget(self.field)
        lay.addWidget(self.typed_lbl)
        lay.addWidget(typed_cap)
        lay.addWidget(self.cross_lbl)
        lay.addWidget(cross_cap)

        self.setStyleSheet(self._field_css(styles.PANEL_BORDER))

    @staticmethod
    def _field_css(border: str) -> str:
        return (f"QLineEdit {{ font-size: 34px; border: 2px solid {border};"
                f" border-radius: 2px; }}")

    def set_counts(self, typed: int, crossings: int) -> None:
        """Set both labels. Red on both when ``typed != crossings``."""
        self.typed_lbl.setText(str(typed))
        self.cross_lbl.setText(str(crossings))
        color = styles.RED_TEXT if typed != crossings else styles.TEXT_PRIMARY
        for lbl in (self.typed_lbl, self.cross_lbl):
            lbl.setStyleSheet(f"color: {color};")

    def mismatch(self) -> bool:
        return self.typed_lbl.text() != self.cross_lbl.text()

    def flash(self) -> None:
        """Border styles.RED for 300 ms, then back."""
        self.setStyleSheet(self._field_css(styles.RED))
        QTimer.singleShot(
            300, lambda: self.setStyleSheet(self._field_css(styles.PANEL_BORDER)))

    def focus_field(self) -> None:
        self.field.setFocus()

    def clear_field(self) -> None:
        """Drop any uncommitted text (called on state changes / after commit)."""
        self.field.clear()
