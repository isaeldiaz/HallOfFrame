"""Crossing list widget (REDESIGN-PLAN §3, §6).

Newest at top — removes the auto-scroll-versus-focus problem entirely. One row
per crossing: sequence, thumbnail, elapsed (mono), and a word flag column.
``CrossingLog`` is the read-only race-screen variant; ``ReviewList`` adds an
inline bow-number field and keyboard selection for the REVIEW state.

Flags use words, not punctuation: ``NO IMAGE`` (missing), ``APPROX``
(approximate), ``DOUBLE?`` (debounce suspect).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QVBoxLayout, QWidget)

from ..export import format_elapsed
from . import styles


def flag_word(image_flag: str | None, suspect: bool) -> tuple[str, str]:
    """(word, colour) for a capture's flag column."""
    if image_flag == "missing":
        return "NO IMAGE", styles.AMBER_TEXT
    if image_flag == "approximate":
        return "APPROX", styles.AMBER_TEXT
    if suspect:
        return "DOUBLE?", styles.AMBER_TEXT
    return "", styles.TEXT_DIM


def _load_pixmap(path: str | None, w: int, h: int) -> QPixmap | None:
    if not path:
        return None
    reader = QImageReader(path)
    reader.setScaledSize(reader.size().scaled(w, h, Qt.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        return None
    return QPixmap.fromImage(img)


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


class _RowBase(QWidget):
    def __init__(self, data: dict, thumb_w: int, thumb_h: int, parent=None):
        super().__init__(parent)
        self.sequence = data["sequence"]
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
        self.thumb.set_thumb(_load_pixmap(data.get("image_path"), thumb_w, thumb_h))
        self.lay.addWidget(self.thumb)

        self.time_lbl = QLabel(format_elapsed(data["elapsed_s"]))
        self.time_lbl.setProperty("mono", True)
        self.time_lbl.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:34px; font-weight:500;"
            f" color:{styles.TEXT_PRIMARY};")
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
        self._data = data

    def set_highlight(self, on: bool) -> None:
        border = styles.BLUE if on else self._base_border
        self.setStyleSheet(f"QWidget{{border-left:3px solid {border};"
                           f" background:{styles.PANEL if on else 'transparent'};}}")
        self.update()


class _BowEdit(QLineEdit):
    """Bow-number field; Enter or Tab commits and requests advance."""

    advance = Signal(int)  # sequence

    def __init__(self, sequence, *a, **k):
        super().__init__(*a, **k)
        self._seq = sequence
        self.returnPressed.connect(lambda: self.advance.emit(self._seq))

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.advance.emit(self._seq)
            return
        super().keyPressEvent(event)


class _ReviewRow(_RowBase):
    """A crossing row with an inline bow-number QLineEdit."""

    bow_edited = Signal(int, str)

    def __init__(self, data: dict, thumb_w: int, thumb_h: int, parent=None):
        super().__init__(data, thumb_w, thumb_h, parent)
        self._base_border = "transparent"
        self.bow_edit = _BowEdit(data["sequence"], data.get("bow") or "")
        self.bow_edit.setPlaceholderText("bow")
        self.bow_edit.setFixedWidth(84)
        self.bow_edit.setFixedHeight(46)
        self.bow_edit.setAlignment(Qt.AlignCenter)
        self.bow_edit.editingFinished.connect(
            lambda: self.bow_edited.emit(self.sequence, self.bow_edit.text()))
        self.bow_edit.setStyleSheet(
            f"QLineEdit{{background:#0b0f12; border:1px solid {styles.PANEL_BORDER};"
            f" border-radius:2px; color:{styles.TEXT_PRIMARY};"
            f" font-family:'{styles.FONT_MONO}'; font-size:24px;"
            " text-align:center; padding:4px;}")
        self.lay.addWidget(self.bow_edit)


class CrossingLog(QWidget):
    """Read-only newest-first list (race screen, RACE_OVER)."""

    def __init__(self, parent=None):
        super().__init__(parent)
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
        right = QLabel("newest first")
        right.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        header.addWidget(right)
        hw = QWidget()
        hw.setStyleSheet(f"background:{styles.PANEL}; border-bottom:1px solid"
                         f" {styles.PANEL_BORDER};")
        hw.setLayout(header)
        hw.setFixedHeight(58)
        lay.addWidget(hw)

        self._rows: dict[int, _RowBase] = {}
        self._host = QWidget()
        self._v = QVBoxLayout(self._host)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(0)
        self._v.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._host)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        lay.addWidget(scroll, 1)

    def add(self, data: dict) -> None:
        row = _RowBase(data, 104, 66)
        self._rows[data["sequence"]] = row
        self._rebuild()

    def update_thumb(self, sequence: int, path: str) -> None:
        """Populate a row's thumbnail once the deferred image is selected."""
        row = self._rows.get(sequence)
        if row is not None:
            row.thumb.set_thumb(_load_pixmap(path, row.thumb.width(), row.thumb.height()))

    def clear(self) -> None:
        self._rows.clear()
        self._rebuild()

    def _rebuild(self) -> None:
        live = set(self._rows)
        while self._v.count():
            item = self._v.takeAt(0)
            w = item.widget()
            if w is None:
                continue
            if self._own(w):
                w.setParent(None)
            else:
                w.deleteLater()
        for seq in sorted(self._rows, reverse=True):
            self._v.addWidget(self._rows[seq])
        self._v.addStretch(1)

    def _own(self, w: QWidget) -> bool:
        """True if the widget is one of our persistent rows."""
        return any(w is row for row in self._rows.values())


class ReviewList(CrossingLog):
    """Newest-first list with an inline bow field per row and selection."""

    bow_edited = Signal(int, str)          # sequence, value
    delete_requested = Signal(int)         # sequence
    selection_changed = Signal(int)        # sequence
    advance_requested = Signal(int)        # sequence (Enter/Tab in bow field)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected: int | None = None
        self._edits: dict[int, QLineEdit] = {}

    def focus_bow(self, sequence: int) -> None:
        edit = self._edits.get(sequence)
        if edit is not None:
            edit.setFocus()
            edit.selectAll()

    def set_selected(self, sequence: int | None) -> None:
        self._selected = sequence
        for seq, row in self._rows.items():
            row.set_highlight(seq == sequence)
            if seq in self._edits:
                self._edits[seq].setStyleSheet(
                    self._input_style(seq == sequence))

    def add(self, data: dict) -> None:
        row = _ReviewRow(data, 92, 58, self)
        row.bow_edited.connect(self._on_bow_edited)
        row.bow_edit.advance.connect(self.advance_requested)
        self._edits[data["sequence"]] = row.bow_edit
        self._rows[data["sequence"]] = row
        self._rebuild()

    def _on_bow_edited(self, seq: int, value: str) -> None:
        self.bow_edited.emit(seq, value)

    def _input_style(self, selected: bool) -> str:
        border = styles.BLUE if selected else styles.PANEL_BORDER
        return (f"QLineEdit{{background:#0b0f12; border:1px solid {border};"
                f" border-radius:2px; color:{styles.TEXT_PRIMARY};"
                f" font-family:'{styles.FONT_MONO}'; font-size:24px;"
                " text-align:center; padding:4px;}")
