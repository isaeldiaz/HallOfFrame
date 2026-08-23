"""Race review dialog (spec §7.3, F4).

After a race ends the operator can open this to review every recorded crossing
at once: each saved photo (the one nearest the recorded time), its elapsed time,
and an inline editable bow number — all laid out in a long scrollable pane like
the calibration dialog (spec §5.5). Non-modal (spec §7.5). Bow edits and deletes
are emitted as signals so the main window can persist them and keep the main
capture list in sync.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..export import format_elapsed


class RaceReviewDialog(QDialog):
    """Shows a race's captures stacked in a scrollable pane: photo + time + bow."""

    bow_edited = Signal(int, str)        # sequence, value
    delete_requested = Signal(int)       # sequence
    open_frames = Signal(int)            # sequence

    def __init__(self, controller, race_id: int, data_root, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.race_id = race_id
        self.data_root = Path(data_root)

        self.setWindowTitle("Race Review")
        self.resize(760, 640)

        lay = QVBoxLayout(self)

        hint = QLabel(
            "Each recorded crossing is shown below with the saved photo nearest "
            "its recorded time. Edit the bow number inline or delete a crossing; "
            "left/right arrow keys focus the next crossing.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # Scrollable pane holding one image + info row per capture.
        self.entries_host = QWidget()
        self.entries_layout = QVBoxLayout(self.entries_host)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.entries_host)
        lay.addWidget(self.scroll, 1)

        self._build()

    # --- content -----------------------------------------------------------
    def _build(self) -> None:
        while self.entries_layout.count():
            item = self.entries_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        caps = [c for c in self.controller.storage.captures_for_race(self.race_id)
                if not c["deleted"]]
        if not caps:
            self.entries_layout.addWidget(QLabel("No ends recorded"))
            self.entries_layout.addStretch(1)
            return

        self._captures = caps
        self._edits: dict[int, QLineEdit] = {}
        for cap in caps:
            self.entries_layout.addWidget(self._make_row(cap))
        self.entries_layout.addStretch(1)

    def _make_row(self, cap) -> QWidget:
        seq = cap["sequence"]
        row = QWidget()
        lay_row = QHBoxLayout(row)

        img = QLabel("NO IMAGE")
        img.setAlignment(Qt.AlignCenter)
        img.setMinimumSize(400, 300)
        path = cap["primary_image"]
        if path:
            full = self.data_root / path
            reader = QImageReader(str(full))
            image = reader.read()
            if not image.isNull():
                img.setPixmap(QPixmap.fromImage(image).scaled(
                    400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay_row.addWidget(img, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel(
            f"seq {seq:03d}  ·  {format_elapsed(cap['elapsed_s'])}"))
        bow_row = QHBoxLayout()
        bow_row.addWidget(QLabel("bow:"))
        bow_edit = QLineEdit(cap["bow_number"] or "")
        bow_edit.setFixedWidth(90)
        bow_edit.editingFinished.connect(
            lambda s=seq, e=bow_edit: self.bow_edited.emit(s, e.text()))
        bow_row.addWidget(bow_edit)
        bow_row.addStretch(1)
        right.addLayout(bow_row)

        btns = QHBoxLayout()
        open_btn = QPushButton("Open frames")
        open_btn.clicked.connect(lambda _=False, s=seq: self.open_frames.emit(s))
        btns.addWidget(open_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(lambda _=False, s=seq: self._delete(s))
        btns.addWidget(del_btn)
        btns.addStretch(1)
        right.addLayout(btns)

        right.addStretch(1)
        lay_row.addLayout(right)
        self._edits[seq] = bow_edit
        return row

    def _delete(self, sequence: int) -> None:
        self.delete_requested.emit(sequence)
        for cap in self._captures:
            if cap["sequence"] == sequence:
                self.controller.soft_delete(cap["id"])
                break
        self._build()
