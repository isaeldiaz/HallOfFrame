"""Race review dialog (spec §7.3, F4).

After a race ends the operator can open this to cycle through every recorded
crossing — the saved photo nearest each recorded time, its elapsed time, and an
inline editable bow number — without blocking the rest of the app (non-modal,
spec §7.5). Bow edits and deletes are emitted as signals so the main window can
persist them and keep the main capture list in sync.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSlider, QVBoxLayout)

from ..export import format_elapsed


class RaceReviewDialog(QDialog):
    """Cycles through a race's captures: photo + recorded time + editable bow."""

    bow_edited = Signal(int, str)        # sequence, value
    delete_requested = Signal(int)       # sequence
    open_frames = Signal(int)            # sequence

    def __init__(self, controller, race_id: int, data_root, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.race_id = race_id
        self.data_root = Path(data_root)
        self._captures: list = []
        self._index = 0
        self._selected = 0  # slider index

        self.setWindowTitle("Race Review")
        self.resize(720, 560)

        lay = QVBoxLayout(self)

        self.counter = QLabel("")
        self.counter.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.counter)

        self.image = QLabel("no image")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(480, 360)
        lay.addWidget(self.image, 1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self.slider)

        self.seq_label = QLabel("")
        self.seq_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.seq_label)

        bow_row = QHBoxLayout()
        bow_row.addStretch(1)
        bow_row.addWidget(QLabel("bow:"))
        self.bow_edit = QLineEdit()
        self.bow_edit.setFixedWidth(80)
        self.bow_edit.editingFinished.connect(self._emit_bow)
        bow_row.addWidget(self.bow_edit)
        bow_row.addStretch(1)
        lay.addLayout(bow_row)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Prev")
        self.prev_btn.clicked.connect(self._prev)
        nav.addWidget(self.prev_btn)
        nav.addStretch(1)
        open_btn = QPushButton("Open frames")
        open_btn.clicked.connect(self._open_frames)
        nav.addWidget(open_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete)
        nav.addWidget(del_btn)
        nav.addStretch(1)
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)

        self._reload()
        if self._captures:
            self._index = 0
            self._show_index()
        else:
            self.counter.setText("No ends recorded")

    # --- navigation --------------------------------------------------------
    def _reload(self) -> None:
        self._captures = [
            c for c in self.controller.storage.captures_for_race(self.race_id)
            if not c["deleted"]]
        if self._captures:
            self._index = max(0, min(self._index, len(self._captures) - 1))
            self.slider.setMinimum(0)
            self.slider.setMaximum(len(self._captures) - 1)

    def _show_index(self) -> None:
        if not self._captures:
            self.counter.setText("No ends recorded")
            self.image.setText("no image")
            return
        cap = self._captures[self._index]
        seq = cap["sequence"]
        self.counter.setText(f"{self._index + 1} / {len(self._captures)}")
        self.seq_label.setText(f"seq {seq:03d}  ·  {format_elapsed(cap['elapsed_s'])}")

        # Avoid re-entrant slider churn while navigating programmatically.
        self.slider.blockSignals(True)
        self.slider.setValue(self._index)
        self.slider.blockSignals(False)

        self.bow_edit.blockSignals(True)
        self.bow_edit.setText(cap["bow_number"] or "")
        self.bow_edit.blockSignals(False)

        path = cap["primary_image"]
        if not path:
            self.image.setText("NO IMAGE")
            return
        full = self.data_root / path
        reader = QImageReader(str(full))
        img = reader.read()
        if img.isNull():
            self.image.setText("NO IMAGE")
            return
        self.image.setPixmap(QPixmap.fromImage(img))

    def _on_slider(self, value: int) -> None:
        self._index = value
        self._show_index()

    def _prev(self) -> None:
        if self._captures:
            self._index = (self._index - 1) % len(self._captures)
            self._show_index()

    def _next(self) -> None:
        if self._captures:
            self._index = (self._index + 1) % len(self._captures)
            self._show_index()

    def _emit_bow(self) -> None:
        if not self._captures:
            return
        cap = self._captures[self._index]
        self.bow_edited.emit(cap["sequence"], self.bow_edit.text())

    def _open_frames(self) -> None:
        if self._captures:
            self.open_frames.emit(self._captures[self._index]["sequence"])

    def _delete(self) -> None:
        if not self._captures:
            return
        cap = self._captures[self._index]
        self.delete_requested.emit(cap["sequence"])
        self.controller.soft_delete(cap["id"])
        self._captures.pop(self._index)
        if not self._captures:
            self._index = 0
        else:
            self._index = max(0, self._index - 1)
        self._reload()
        self._show_index()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp):
            self._prev()
            return
        if event.key() in (Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown):
            self._next()
            return
        super().keyPressEvent(event)
