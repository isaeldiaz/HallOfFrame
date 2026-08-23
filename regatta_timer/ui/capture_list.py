"""Capture list (spec §7.3) — the primary deliverable (F3, F4, F5).

One row per capture: thumbnail, sequence, elapsed M:SS.mmm, inline editable
bow number. Newest at bottom with auto-scroll that does not steal focus from a
bow-number field being typed in. Clicking a row opens a frame review panel.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QScrollArea,
                               QSlider, QVBoxLayout, QWidget)

from ..export import format_elapsed


class FrameReviewPanel(QWidget):
    """Full-size primary image plus a slider across the saved window frames."""

    promoted = Signal(int, str)  # capture_id, primary_path

    def __init__(self, capture_id: int, frames: list, parent=None):
        super().__init__(parent)
        self.capture_id = capture_id
        self.frames = frames  # list of (path, offset_ms)
        self.frames.sort(key=lambda x: x[1])
        lay = QVBoxLayout(self)
        self.image = QLabel("no window frames")
        self.image.setAlignment(Qt.AlignCenter)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(max(0, len(self.frames) - 1))
        self.slider.valueChanged.connect(self._show)
        lay.addWidget(self.image)
        lay.addWidget(self.slider)
        if self.frames:
            self._show(0)

    def _show(self, idx: int) -> None:
        path, offset = self.frames[idx]
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if not img.isNull():
            self.image.setPixmap(QPixmap.fromImage(img))
        self.image.setToolTip(f"{offset:+.0f} ms from recorded time")


class CaptureList(QListWidget):
    capture_clicked = Signal(int)
    bow_edited = Signal(int, str)
    delete_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.itemClicked.connect(self._on_click)
        self._thumb_cache = {}
        self._bow_edits = {}  # sequence -> QLineEdit

    def add_capture(self, sequence: int, elapsed_s: float, thumb_path: str | None,
                    bow: str = "", suspect: bool = False,
                    image_flag: str | None = None) -> None:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 4, 6, 4)
        thumb = QLabel()
        thumb.setFixedSize(120, 80)
        if thumb_path:
            pm = self._load_thumb(thumb_path)
            if pm:
                thumb.setPixmap(pm)
        lay.addWidget(thumb)
        meta = QLabel(f"{sequence}\n{format_elapsed(elapsed_s)}")
        lay.addWidget(meta)
        bow_edit = QLineEdit(bow)
        bow_edit.setPlaceholderText("bow")
        bow_edit.setFixedWidth(60)
        bow_edit.editingFinished.connect(
            lambda e=bow_edit: self.bow_edited.emit(sequence, e.text()))
        self._bow_edits[sequence] = bow_edit
        lay.addWidget(bow_edit)
        if image_flag == "missing":
            lay.addWidget(QLabel("NO IMAGE"))
        elif image_flag == "approximate":
            lay.addWidget(QLabel("~"))
        if suspect:
            lay.addWidget(QLabel("?"))
        item = QListWidgetItem()
        item.setData(Qt.UserRole, sequence)
        item.setSizeHint(row.sizeHint())
        self.addItem(item)
        self.setItemWidget(item, row)
        self.scrollToBottom()

    def _load_thumb(self, path: str) -> QPixmap | None:
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        reader = QImageReader(path)
        reader.setScaledSize(reader.size().scaled(120, 80, Qt.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            return None
        pm = QPixmap.fromImage(img)
        self._thumb_cache[path] = pm
        return pm

    def update_bow(self, sequence: int, value: str) -> None:
        """Set a row's bow-field text without re-emitting (keeps the main list
        in sync when the bow is edited from elsewhere, e.g. the review dialog)."""
        edit = self._bow_edits.get(sequence)
        if edit is not None and edit.text() != value:
            edit.setText(value)

    def _on_click(self, item) -> None:
        seq = item.data(Qt.UserRole)
        self.capture_clicked.emit(int(seq))

    def contextMenuEvent(self, event):  # noqa: N802
        from PySide6.QtWidgets import QMenu
        item = self.itemAt(event.pos())
        if item is None:
            return
        seq = int(item.data(Qt.UserRole))
        menu = QMenu(self)
        act = menu.addAction("Delete (soft)")
        if menu.exec(event.globalPos()) == act:
            self.delete_requested.emit(seq)
