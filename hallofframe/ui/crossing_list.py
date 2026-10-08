"""Crossing list widget (spec §7.3, §13.2).

One class for both the read-only race screen and the editable review screen.
Rows are always ordered **fastest at top, slowest at bottom** (spec §7.3, decided
2026-10-06, §13.2) — there is deliberately no ``order`` argument and no config
key, so the configurable ordering that caused the confusion cannot come back.

One row per crossing: sequence, thumbnail, elapsed (mono), a word flag column,
and — only when ``editable`` — inline bow-number and elapsed-time fields.

Flags use words, not punctuation: ``NO IMAGE`` (missing), ``APPROX``
(approximate), ``DOUBLE?`` (debounce suspect).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QVBoxLayout, QWidget)

from ..render import flag_word as flag_text, format_elapsed
from . import styles
from .images import load_scaled

# Thumbnail box (w, h): the read-only race-screen list is a little larger than
# the review list, which has two edit fields competing for the row width.
THUMB_W, THUMB_H = 104, 66
EDIT_THUMB_W, EDIT_THUMB_H = 92, 58


def flag_word(image_flag: str | None, suspect: bool) -> tuple[str, str]:
    """(word, colour) for a capture's flag column."""
    word = flag_text(image_flag, suspect)
    return word, (styles.AMBER_TEXT if word else styles.TEXT_DIM)


class _Thumb(QWidget):
    """Letterboxed placeholder that shows the photo when available."""

    def __init__(self, w: int, h: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.setStyleSheet(f"background:{styles.LETTERBOX};")
        self._pm: QPixmap | None = None

    def set_thumb(self, pm: QPixmap | None) -> None:
        self._pm = pm
        self.update()

    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), styles.LETTERBOX)
        if self._pm is not None:
            x = (self.width() - self._pm.width()) // 2
            y = (self.height() - self._pm.height()) // 2
            p.drawPixmap(x, y, self._pm)
        p.end()


class _BowEdit(QLineEdit):
    """Bow-number field; Enter or Tab commits and requests advance.

    Shift+←/→ step the crossing's frame and ↑/↓ move the selection, so the
    operator is never locked out of navigation once a field has focus. These
    have to be taken in ``event()``: a focused QLineEdit consumes every key it
    sees before the parent's ``keyPressEvent`` ever runs, which is exactly why
    navigation died after Tab/Enter landed in a field.
    """

    advance = Signal(int)     # sequence (Enter/Tab)
    step_frame = Signal(int)  # +1/-1 (Shift+←/→)
    select_step = Signal(int)  # +1/-1 (↑/↓)

    def __init__(self, sequence, *a, **k):
        super().__init__(*a, **k)
        self._seq = sequence
        self.returnPressed.connect(lambda: self.advance.emit(self._seq))

    def event(self, event):
        # Tab has to be taken in event(): QWidget::event() spends it on focus
        # navigation before keyPressEvent() is ever called, so a Tab branch there
        # never runs (it moved focus to the list's scroll area instead of
        # advancing). Leaving the field emits editingFinished, which persists it.
        if event.type() == QEvent.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key_Tab, Qt.Key_Backtab):
                self.advance.emit(self._seq)
                return True
            if key == Qt.Key_Left and mods & Qt.ShiftModifier:
                self.step_frame.emit(-1)
                return True
            if key == Qt.Key_Right and mods & Qt.ShiftModifier:
                self.step_frame.emit(1)
                return True
            if key == Qt.Key_Up:
                self.select_step.emit(1)
                return True
            if key == Qt.Key_Down:
                self.select_step.emit(-1)
                return True
        return super().event(event)


class _TimeEdit(_BowEdit):
    """Editable elapsed-time field (``M:SS.mmm``); commits on editingFinished.

    Reuses ``_BowEdit`` so Tab/Enter advance and Shift+←/→/↑/↓ keep working
    while the field has focus. The raw text is emitted; the screen owns parsing
    (via :func:`parse_elapsed`) so invalid input can be reverted there.
    """

    time_committed = Signal(int, str)  # sequence, raw text

    def __init__(self, sequence, *a, **k):
        super().__init__(sequence, *a, **k)
        self.editingFinished.connect(
            lambda: self.time_committed.emit(self._seq, self.text()))

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        self.selectAll()


class _Row(QWidget):
    """One crossing row; bow and time fields appear only when *editable*."""

    bow_edited = Signal(int, str)
    time_edited = Signal(int, str)

    def __init__(self, data: dict, thumb_w: int, thumb_h: int,
                 editable: bool, parent=None):
        super().__init__(parent)
        self.sequence = data["sequence"]
        self.elapsed_s = data["elapsed_s"]
        self.editable = editable
        self._data = data
        self._base_border = "transparent"
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(28, 12, 28, 12)
        self.lay.setSpacing(20)

        self.seq_lbl = QLabel(f"{self.sequence:03d}")
        self.seq_lbl.setProperty("mono", True)
        self.seq_lbl.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:26px;"
            f" color:{styles.TEXT_DIM}; width:58px;")
        self.lay.addWidget(self.seq_lbl)

        self.thumb = _Thumb(thumb_w, thumb_h)
        self.thumb.set_thumb(
            load_scaled(data.get("image_path"), QSize(thumb_w, thumb_h)))
        self.lay.addWidget(self.thumb)

        if editable:
            self.time_edit = _TimeEdit(self.sequence,
                                       format_elapsed(self.elapsed_s))
            self.time_edit.setFixedWidth(120)
            self.time_edit.setFixedHeight(46)
            self.time_edit.setAlignment(Qt.AlignCenter)
            self.time_edit.time_committed.connect(
                lambda seq, raw: self.time_edited.emit(seq, raw))
            self.time_edit.setStyleSheet(
                f"QLineEdit{{background:#0b0f12;"
                f" border:1px solid {styles.PANEL_BORDER};"
                f" border-radius:2px; color:{styles.TEXT_PRIMARY};"
                f" font-family:'{styles.FONT_MONO}'; font-size:26px;"
                f" font-weight:500; text-align:center; padding:4px;}}")
            self.lay.addWidget(self.time_edit)
        else:
            self.time_lbl = QLabel(format_elapsed(self.elapsed_s))
            self.time_lbl.setProperty("mono", True)
            self.time_lbl.setStyleSheet(
                f"font-family:'{styles.FONT_MONO}'; font-size:34px;"
                f" font-weight:500; color:{styles.TEXT_PRIMARY};")
            self.lay.addWidget(self.time_lbl)

        self.lay.addStretch(1)

        word, colour = flag_word(data.get("image_flag"), data.get("suspect"))
        self.flag_lbl = QLabel(word)
        self.flag_lbl.setProperty("mono", True)
        self.flag_lbl.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:14px;"
            f" letter-spacing:.08em; color:{colour};")
        self.flag_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lay.addWidget(self.flag_lbl)

        if editable:
            self.bow_edit = _BowEdit(self.sequence, data.get("bow") or "")
            self.bow_edit.setPlaceholderText("bow")
            self.bow_edit.setFixedWidth(84)
            self.bow_edit.setFixedHeight(46)
            self.bow_edit.setAlignment(Qt.AlignCenter)
            self.bow_edit.editingFinished.connect(
                lambda: self.bow_edited.emit(self.sequence,
                                             self.bow_edit.text()))
            self.bow_edit.setStyleSheet(
                f"QLineEdit{{background:#0b0f12;"
                f" border:1px solid {styles.PANEL_BORDER};"
                f" border-radius:2px; color:{styles.TEXT_PRIMARY};"
                f" font-family:'{styles.FONT_MONO}'; font-size:24px;"
                " text-align:center; padding:4px;}")
            self.lay.addWidget(self.bow_edit)

    def set_highlight(self, on: bool) -> None:
        border = styles.BLUE if on else self._base_border
        self.setStyleSheet(f"QWidget{{border-left:3px solid {border};"
                           f" background:{styles.PANEL if on else 'transparent'};}}")
        self.update()


class CrossingList(QWidget):
    """Fastest-first crossing list (spec §7.3, §13.2).

    ``editable=True`` adds the inline bow and time fields (review screen);
    ``editable=False`` is the read-only race-screen/RACE_OVER variant. Order is
    fixed: :meth:`_rebuild` sorts by ``elapsed_s`` ascending, on every screen,
    with no parameter to change it.
    """

    bow_edited = Signal(int, str)          # sequence, value
    time_edited = Signal(int, str)         # sequence, raw elapsed text
    delete_requested = Signal(int)         # sequence
    selection_changed = Signal(int)        # sequence
    advance_requested = Signal(int)        # sequence (Enter/Tab in a field)
    step_frame = Signal(int)               # +1/-1 (Shift+←/→ in a field)
    select_step = Signal(int)              # +1/-1 (↑/↓ in a field)

    def __init__(self, *, editable: bool, parent=None):
        super().__init__(parent)
        self._editable = editable
        self._rows: dict[int, _Row] = {}
        self._edits: dict[int, QLineEdit] = {}
        self._selected: int | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QHBoxLayout()
        cap = QLabel("Crossings")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        header.addWidget(cap)
        header.addStretch(1)
        right = QLabel("fastest first")
        right.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        header.addWidget(right)
        hw = QWidget()
        hw.setStyleSheet(f"background:{styles.PANEL}; border-bottom:1px solid"
                         f" {styles.PANEL_BORDER};")
        hw.setLayout(header)
        hw.setFixedHeight(58)
        lay.addWidget(hw)

        self._host = QWidget()
        self._v = QVBoxLayout(self._host)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(0)
        self._v.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._host)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFocusPolicy(Qt.NoFocus)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        lay.addWidget(scroll, 1)

    # --- public API -------------------------------------------------------
    def add(self, data: dict) -> None:
        if self._editable:
            row = _Row(data, EDIT_THUMB_W, EDIT_THUMB_H, True)
            row.bow_edited.connect(self.bow_edited)
            row.time_edited.connect(self.time_edited)
            row.bow_edit.advance.connect(self.advance_requested)
            row.bow_edit.step_frame.connect(self.step_frame)
            row.bow_edit.select_step.connect(self.select_step)
            row.time_edit.advance.connect(self.advance_requested)
            row.time_edit.step_frame.connect(self.step_frame)
            row.time_edit.select_step.connect(self.select_step)
            self._edits[data["sequence"]] = row.bow_edit
        else:
            row = _Row(data, THUMB_W, THUMB_H, False)
        self._rows[data["sequence"]] = row
        self._rebuild()

    def update_thumb(self, sequence: int, path: str) -> None:
        """Populate a row's thumbnail once the deferred image is selected."""
        row = self._rows.get(sequence)
        if row is not None:
            row.thumb.set_thumb(load_scaled(
                path, QSize(row.thumb.width(), row.thumb.height())))

    def remove(self, sequence: int) -> None:
        """Drop a row from the list (used when a crossing is soft-deleted)."""
        self._edits.pop(sequence, None)
        if self._rows.pop(sequence, None) is not None:
            self._rebuild()

    def clear(self) -> None:
        self._rows.clear()
        self._edits.clear()
        self._rebuild()

    def count(self) -> int:
        """Number of live rows (replaces callers poking ``_rows``)."""
        return len(self._rows)

    def set_selected(self, sequence: int | None) -> None:
        self._selected = sequence
        for seq, row in self._rows.items():
            row.set_highlight(seq == sequence)
            if seq in self._edits:
                self._edits[seq].setStyleSheet(self._input_style(seq == sequence))

    def focus_bow(self, sequence: int) -> None:
        edit = self._edits.get(sequence)
        if edit is not None:
            edit.setFocus()
            edit.selectAll()

    def refresh_time(self, sequence: int, elapsed_s: float) -> None:
        """Reset a row's time field to the stored value (after commit/revert)."""
        row = self._rows.get(sequence)
        if row is None:
            return
        row.elapsed_s = elapsed_s
        if self._editable and hasattr(row, "time_edit"):
            row.time_edit.setText(format_elapsed(elapsed_s))

    # --- internals --------------------------------------------------------
    def _rebuild(self) -> None:
        while self._v.count():
            item = self._v.takeAt(0)
            w = item.widget()
            if w is None:
                continue
            if self._own(w):
                w.setParent(None)
            else:
                w.deleteLater()
        for row in sorted(self._rows.values(), key=lambda r: r.elapsed_s):
            self._v.addWidget(row)
        self._v.addStretch(1)

    def _own(self, w: QWidget) -> bool:
        """True if the widget is one of our persistent rows."""
        return any(w is row for row in self._rows.values())

    def _input_style(self, selected: bool) -> str:
        border = styles.BLUE if selected else styles.PANEL_BORDER
        return (f"QLineEdit{{background:#0b0f12; border:1px solid {border};"
                f" border-radius:2px; color:{styles.TEXT_PRIMARY};"
                f" font-family:'{styles.FONT_MONO}'; font-size:24px;"
                " text-align:center; padding:4px;}")
