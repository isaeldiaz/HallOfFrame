"""Review state (REDESIGN-PLAN §6, mockup state 5).

Full-screen, not a dialog. Selected crossing's photo large on the left with a
frame scrubber underneath (discrete ticks labelled by offset ms); the crossing
list with an inline bow field per row on the right. ``↑/↓`` select a crossing,
``Shift+←/→`` step frames, ``Tab`` saves the selected frame as the crossing's
primary photo and focuses its bow field so the bow can be typed in the same
view, then ``Enter``/``Tab`` in the field commits the bow and moves to the next
crossing. ``Del`` soft-deletes, ``Esc`` returns to READY.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QVBoxLayout, QWidget)

from . import styles
from .crossing_list import ReviewList


class _Scrubber(QWidget):
    """Discrete per-frame tick marks, labelled by offset ms."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self._ticks: list[dict] = []  # {frame, label, offset_ms}
        self._selected = -1
        self._host = None
        self._lay = None

    def set_frames(self, frames: list, selected_offset_ms: float) -> None:
        self._ticks = []
        for f in sorted(frames, key=lambda x: x["offset_ms"]):
            off = float(f["offset_ms"])
            label = f"{off:+.0f}" if off >= 0 else f"{off:.0f}"
            self._ticks.append({"frame": f, "label": label, "offset_ms": off})
        if not self._ticks:
            self._selected = -1
            self._rebuild()
            return
        # select the tick nearest the crossing time (primary)
        best = min(range(len(self._ticks)),
                   key=lambda i: abs(self._ticks[i]["offset_ms"] - selected_offset_ms))
        self._selected = best
        self._rebuild()

    def _rebuild(self) -> None:
        if self._host is None:
            self._host = QWidget()
            self._lay = QHBoxLayout(self._host)
            self._lay.setContentsMargins(0, 0, 0, 0)
            self._lay.setSpacing(6)
            wrap = QVBoxLayout(self)
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.addWidget(self._host)
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, t in enumerate(self._ticks):
            self._lay.addWidget(self._make_tick(i, t))
        self._lay.addStretch(1)

    def _make_tick(self, idx: int, t: dict) -> QWidget:
        sel = idx == self._selected
        w = QWidget()
        w.setFixedHeight(56)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 4)
        label = QLabel(t["label"])
        label.setProperty("mono", True)
        label.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        label.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:12px;"
            f" color:{styles.TEXT_PRIMARY if sel else styles.TEXT_FAINT};")
        lay.addWidget(label)
        w.setStyleSheet(
            f"background:{'#243440' if sel else styles.PANEL};"
            f" border:1px solid {styles.BLUE if sel else styles.DIVIDER};")
        w._idx = idx  # type: ignore[attr-defined]
        return w

    def selected_frame(self):
        if 0 <= self._selected < len(self._ticks):
            return self._ticks[self._selected]["frame"]
        return None

    def step(self, delta: int) -> None:
        n = len(self._ticks)
        if n == 0:
            return
        self._selected = (self._selected + delta) % n
        self._rebuild()

    def mousePressEvent(self, event):  # noqa: N802
        child = self.childAt(event.pos())
        idx = getattr(child, "_idx", None)
        if idx is None:
            # childAt may hit the label; walk parents
            while child is not None and getattr(child, "_idx", None) is None:
                child = child.parentWidget()
            idx = getattr(child, "_idx", None)
        if idx is not None:
            self._selected = idx
            self._rebuild()
        super().mousePressEvent(event)


class ReviewScreen(QWidget):
    def __init__(self, controller, data_root, race_id: int | None = None,
                 parent=None):
        super().__init__(parent)
        self.controller = controller
        self.race_id = race_id
        self.data_root = data_root
        self._captures: list[dict] = []
        self._selected_seq: int | None = None
        self._frame_paths: dict[int, list] = {}  # capture_id -> [frame dicts]

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- left: photo + scrubber ---
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(32, 32, 32, 32)
        lv.setSpacing(18)

        header = QHBoxLayout()
        self.counter = QLabel("")
        self.counter.setStyleSheet(f"color:{styles.TEXT_DIM}; font-size:20px;")
        header.addWidget(self.counter)
        header.addStretch(1)
        step_hint = QLabel("Shift+←/→ to step frames · ±500 ms")
        step_hint.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
        header.addWidget(step_hint)
        lv.addLayout(header)

        self.photo = QLabel("")
        self.photo.setAlignment(Qt.AlignCenter)
        self.photo.setStyleSheet(f"background:{styles.LETTERBOX};")
        lv.addWidget(self.photo, 1)

        scrub_hdr = QHBoxLayout()
        cap = QLabel("Window frames")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        scrub_hdr.addWidget(cap)
        self.offset_lbl = QLabel("")
        self.offset_lbl.setProperty("mono", True)
        self.offset_lbl.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:20px;"
            f" color:{styles.TEXT_PRIMARY};")
        scrub_hdr.addWidget(self.offset_lbl)
        scrub_hdr.addStretch(1)
        lv.addLayout(scrub_hdr)

        self.scrubber = _Scrubber()
        lv.addWidget(self.scrubber)
        root.addWidget(left, 1)

        # --- right: crossing list with bow fields ---
        right = QWidget()
        right.setStyleSheet(f"border-left:1px solid {styles.PANEL_BORDER};")
        self.list = ReviewList()
        self.list.bow_edited.connect(self._bow_edited)
        self.list.delete_requested.connect(self._delete)
        self.list.selection_changed.connect(self._select)
        self.list.advance_requested.connect(self._advance)
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        rlay.addWidget(self.list)
        right.setFixedWidth(700)
        root.addWidget(right)

    # --- public API -------------------------------------------------------
    def load_captures(self) -> None:
        self._captures = [c for c in
                          self.controller.storage.captures_for_race(self.race_id)
                          if not c["deleted"]]
        self.list.clear()
        for c in self._captures:
            self.list.add({
                "sequence": c["sequence"],
                "elapsed_s": c["elapsed_s"],
                "image_path": str(self.data_root / c["primary_image"])
                              if c["primary_image"] else None,
                "image_flag": c["image_flag"],
                "suspect": bool(c["debounce_suspect"]),
                "bow": c["bow_number"] or "",
            })
        if self._captures:
            self._select(self._captures[0]["sequence"])

    def _select(self, sequence: int) -> None:
        self._selected_seq = sequence
        self.list.set_selected(sequence)
        idx = next((i for i, c in enumerate(self._captures)
                    if c["sequence"] == sequence), 0)
        self.counter.setText(
            f"Crossing <span style='color:{styles.TEXT_PRIMARY}'>"
            f"{sequence}</span> of {len(self._captures)}")
        self._show_capture(sequence)
        self._show_primary()

    def _show_capture(self, sequence: int) -> None:
        cap = next((c for c in self._captures if c["sequence"] == sequence), None)
        if cap is None:
            self.photo.setText("no crossing")
            self._current_capture = None
            return
        frames = self.controller.storage.frames_for_capture(cap["id"])
        self._frame_paths[cap["id"]] = [
            {"path": str(self.data_root / f["path"]), "offset_ms": f["offset_ms"],
             "id": f["id"]} for f in frames]
        self._current_capture = cap

    def _show_primary(self) -> None:
        cap = getattr(self, "_current_capture", None)
        if cap is None:
            self.photo.setText("no crossing selected")
            self.scrubber.set_frames([], 0)
            self.offset_lbl.setText("")
            return
        frames = self._frame_paths.get(cap["id"], [])
        if not frames:
            self.photo.setText("no window frames")
            self.scrubber.set_frames([], 0)
            self.offset_lbl.setText("")
            return
        # primary = frame nearest the recorded time (offset closest to 0)
        primary = min(frames, key=lambda f: abs(f["offset_ms"]))
        self.scrubber.set_frames(frames, primary["offset_ms"])
        self._show_frame(primary["path"], primary["offset_ms"])

    def _show_frame(self, path: str, offset_ms: float) -> None:
        off = float(offset_ms)
        self.offset_lbl.setText(f"{off:+.0f} ms")
        reader = QImageReader(path)
        img = reader.read()
        if img.isNull():
            self.photo.setText("image unreadable")
            return
        pm = QPixmap.fromImage(img)
        area = self.photo.rect()
        if area.width() and area.height():
            pm = pm.scaled(area.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.photo.setPixmap(pm)

    # --- persistence ------------------------------------------------------
    def _bow_edited(self, sequence: int, value: str) -> None:
        cap = next((c for c in self._captures if c["sequence"] == sequence), None)
        if cap:
            self.controller.set_bow_number(cap["id"], value or None)

    def _delete(self, sequence: int) -> None:
        cap = next((c for c in self._captures if c["sequence"] == sequence), None)
        if cap:
            self.controller.soft_delete(cap["id"])
        self._captures = [c for c in self._captures if c["sequence"] != sequence]
        self.list._rows.pop(sequence, None)
        self.list._edits.pop(sequence, None)
        self.list._rebuild()
        if self._captures:
            self._select(self._captures[0]["sequence"])

    # --- keyboard ---------------------------------------------------------
    def keyPressEvent(self, event):  # noqa: N802
        # Let focused text inputs (bow fields) handle their own keys first.
        if isinstance(self.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Up:
            self._move_selection(-1)
            return
        if key == Qt.Key_Down:
            self._move_selection(1)
            return
        if key == Qt.Key_Left and mods & Qt.ShiftModifier:
            self.scrubber.step(-1)
            f = self.scrubber.selected_frame()
            if f:
                self._show_frame(f["path"], f["offset_ms"])
            return
        if key == Qt.Key_Right and mods & Qt.ShiftModifier:
            self.scrubber.step(1)
            f = self.scrubber.selected_frame()
            if f:
                self._show_frame(f["path"], f["offset_ms"])
            return
        if key == Qt.Key_Tab:
            # Save the selected frame as primary, then drop into the current
            # crossing's bow field so the bow can be typed before advancing.
            self._save_and_focus_bow()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            # Save the selected frame and move to the next crossing's bow.
            self._save_and_advance()
            return
        if key == Qt.Key_Delete:
            if self._selected_seq is not None:
                self._delete(self._selected_seq)
            return
        super().keyPressEvent(event)

    def _move_selection(self, delta: int) -> None:
        if not self._captures:
            return
        idx = next((i for i, c in enumerate(self._captures)
                    if c["sequence"] == self._selected_seq), 0)
        idx = (idx + delta) % len(self._captures)
        self._select(self._captures[idx]["sequence"])

    def _advance(self, sequence: int) -> None:
        """Save the selected frame, then move focus to the next crossing's bow."""
        self._save_and_advance()

    def _save_and_focus_bow(self) -> None:
        """Promote the selected frame, then focus the current crossing's bow."""
        self._commit_selected_frame()
        if self._selected_seq is not None:
            self.list.focus_bow(self._selected_seq)

    def _save_and_advance(self) -> None:
        """Promote the selected frame, then advance to the next crossing."""
        self._commit_selected_frame()
        self._move_selection(1)
        if self._selected_seq is not None:
            self.list.focus_bow(self._selected_seq)

    def _commit_selected_frame(self) -> bool:
        """Promote the scrubber's selected frame to the crossing's primary."""
        cap = getattr(self, "_current_capture", None)
        if cap is None:
            return False
        f = self.scrubber.selected_frame()
        if f is None:
            return False
        path = self.controller.set_primary(cap["id"], f["id"])
        if not path:
            return False
        for c in self._captures:
            if c["id"] == cap["id"]:
                c["primary_image"] = path
        self.list.update_thumb(cap["sequence"], str(self.data_root / path))
        return True
