"""Review state (REDESIGN-PLAN §6, mockup state 5).

Full-screen, not a dialog. Selected crossing's photo large on the left with a
frame scrubber underneath (discrete ticks labelled by offset ms); the crossing
list with an inline bow field per row on the right. ``↑/↓`` select a crossing,
``Shift+←/→`` step frames, ``Tab`` saves the selected frame as the crossing's
primary photo and focuses its bow field so the bow can be typed in the same
view, then ``Enter``/``Tab`` in the field commits the bow and moves to the next
crossing. ``Del`` soft-deletes, ``Esc`` returns to READY.

Two Qt constraints shape the key handling. ``Tab`` is spent by
``QWidget::event()`` on focus navigation *before* ``keyPressEvent()`` runs, so it
has to be intercepted in ``event()``; and ``Enter``/``Space`` are application-wide
shortcuts (main_window §4) that win over the focused widget, so MainWindow
silences them while REVIEW is on screen and while a text field has focus.
"""
from __future__ import annotations

import datetime
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import styles
from .crossing_list import ReviewList
from ..export import local_hms

# Width of the crossing list. Wide enough for a row (thumbnail, mono elapsed,
# flag, bow field), but resizeEvent() keeps it under a share of the screen: a
# hard 700 px floor made the page's minimum width exceed the 1920 px panel and
# put the list and its bow fields off the right edge entirely.
PANEL_W = 700
PANEL_MIN_W = 660   # a row's natural width: below this the bow field clips
PANEL_SHARE = 0.4


class _Scrubber(QWidget):
    """Discrete per-frame tick marks, labelled by offset ms."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self._ticks: list[dict] = []  # {frame, label, offset_ms}
        self._selected = -1
        self._sel_widget = None

        # One tick per frame: at 30 fps a +/-500 ms window is ~30 ticks, ~1600
        # px. Without the scroll area that became the window's minimum width and
        # pushed the crossing list off the right edge of the screen, so the ticks
        # scroll and the widget itself is free to shrink.
        self._host = QWidget()
        self._lay = QHBoxLayout(self._host)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        self._area = QScrollArea()
        self._area.setWidget(self._host)
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.setFocusPolicy(Qt.NoFocus)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._area.setStyleSheet("QScrollArea{background:transparent;}")
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(self._area)
        self.setMinimumWidth(1)

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
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._sel_widget = None
        for i, t in enumerate(self._ticks):
            tick = self._make_tick(i, t)
            if i == self._selected:
                self._sel_widget = tick
            self._lay.addWidget(tick)
        self._lay.addStretch(1)
        # One event-loop turn later: the ticks have just been recreated and
        # their positions are only final once this rebuild has been laid out.
        QTimer.singleShot(0, self._scroll_to_selection)

    def _scroll_to_selection(self) -> None:
        """Centre the selected tick — the ticks scroll, so stepping must follow."""
        if self._sel_widget is None:
            return
        bar = self._area.horizontalScrollBar()
        centre = self._sel_widget.x() + self._sel_widget.width() // 2
        bar.setValue(centre - self._area.viewport().width() // 2)

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


class _Photo(QLabel):
    """Letterboxed frame view; rescales the frame to whatever size it is given.

    The pane has to keep the scaled pixmap and the source apart: a QLabel
    reports its pixmap's size as its minimum size, so scaling to the label's
    current rect and nothing else both ratchets the pane wider and leaves the
    photo at the size the pane happened to have when the frame was first shown.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setStyleSheet(f"background:{styles.LETTERBOX};")
        self._src: QPixmap | None = None

    def set_frame(self, pixmap: QPixmap | None, empty_text: str = "") -> None:
        self._src = pixmap
        if pixmap is None:
            self.setText(empty_text)
            return
        self._render()

    def _render(self) -> None:
        if self._src is None:
            return
        if self.width() > 1 and self.height() > 1:
            self.setPixmap(self._src.scaled(self.size(), Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._render()


class ReviewScreen(QWidget):
    identify_requested = Signal()
    merge_requested = Signal()

    def __init__(self, controller, data_root, race_id: int | None = None,
                 parent=None):
        super().__init__(parent)
        self.controller = controller
        self.race_id = race_id
        self.data_root = data_root
        self._captures: list[dict] = []
        self._selected_seq: int | None = None
        self._frame_paths: dict[int, list] = {}  # capture_id -> [frame dicts]
        self.panel: QWidget | None = None
        self._t0_wall: float | None = None

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
        self.identify_btn = QPushButton("Identify race…")
        self.identify_btn.setFocusPolicy(Qt.NoFocus)
        self.identify_btn.clicked.connect(self.identify_requested)
        self.identify_btn.hide()
        header.addWidget(self.identify_btn)
        self.merge_btn = QPushButton("Merge duplicates…")
        self.merge_btn.setFocusPolicy(Qt.NoFocus)
        self.merge_btn.clicked.connect(self.merge_requested)
        self.merge_btn.hide()
        header.addWidget(self.merge_btn)
        header.addStretch(1)
        step_hint = QLabel("Shift+←/→ to step frames · ±500 ms")
        step_hint.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
        header.addWidget(step_hint)
        lv.addLayout(header)

        start_row = QHBoxLayout()
        start_row.setSpacing(10)
        start_cap = QLabel("START")
        start_cap.setStyleSheet(
            f"color:{styles.TEXT_FAINT}; font-size:14px;"
            " letter-spacing:.12em; text-transform:uppercase;")
        start_row.addWidget(start_cap)
        self.start_edit = QLineEdit()
        self.start_edit.setProperty("mono", True)
        self.start_edit.setFixedWidth(140)
        self.start_edit.setMaxLength(8)
        self.start_edit.setPlaceholderText("HH:MM:SS")
        self.start_edit.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:20px;"
            f" color:{styles.TEXT_PRIMARY}; background:{styles.PANEL};"
            f" border:1px solid {styles.DIVIDER}; padding:4px 8px;")
        self.start_edit.editingFinished.connect(self._save_start_time)
        start_row.addWidget(self.start_edit)
        start_hint = QLabel("wall-clock start · shifts crossing times")
        start_hint.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        start_row.addWidget(start_hint)
        start_row.addStretch(1)
        lv.addLayout(start_row)

        self.photo = _Photo()
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
        # The photo column yields first on a narrow screen: the crossing list is
        # what the operator is aiming at, and nothing in this column may set a
        # floor that pushes the list past the right edge.
        left.setMinimumWidth(1)
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
        self.panel = right
        right.setFixedWidth(PANEL_W)
        root.addWidget(right)

    # --- public API -------------------------------------------------------
    def refresh_roster_actions(self, is_unlisted: bool, has_duplicates: bool) -> None:
        """Show the roster actions this race is eligible for (WP6/WP7)."""
        self.identify_btn.setVisible(is_unlisted)
        self.merge_btn.setVisible(has_duplicates)

    def load_captures(self) -> None:
        # dict(), not the sqlite3.Row it came from: _commit_selected_frame()
        # writes the new primary_image back into these rows, and a Row is
        # read-only (it raised TypeError on every Tab/Enter save).
        self._captures = [dict(c) for c in
                          self.controller.storage.captures_for_race(self.race_id)
                          if not c["deleted"]]
        row = self.controller.storage.get_race(self.race_id)
        self._t0_wall = row["t0_wall"] if row else None
        self.start_edit.setText(
            local_hms(self._t0_wall) if self._t0_wall is not None else "")
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
            self.photo.set_frame(None, "no crossing")
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
            self.photo.set_frame(None, "no crossing selected")
            self.scrubber.set_frames([], 0)
            self.offset_lbl.setText("")
            return
        frames = self._frame_paths.get(cap["id"], [])
        if not frames:
            self.photo.set_frame(None, "no window frames")
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
        img = QImageReader(path).read()
        if img.isNull():
            self.photo.set_frame(None, "image unreadable")
            return
        self.photo.set_frame(QPixmap.fromImage(img))

    # --- persistence ------------------------------------------------------
    def _save_start_time(self) -> None:
        """Commit the edited wall-clock start time, shifting crossing times."""
        text = self.start_edit.text().strip()
        parts = text.split(":")
        if len(parts) != 3:
            self.start_edit.setText(local_hms(self._t0_wall)
                                    if self._t0_wall is not None else "")
            return
        try:
            hh, mm, ss = (int(p) for p in parts)
            if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
                raise ValueError
        except ValueError:
            self.start_edit.setText(local_hms(self._t0_wall)
                                    if self._t0_wall is not None else "")
            return
        base = datetime.datetime.fromtimestamp(self._t0_wall or time.time()).date()
        new = datetime.datetime.combine(
            base, datetime.time(hh, mm, ss)).timestamp()
        if self._t0_wall is not None and self.controller.set_start_time(
                self.race_id, new):
            self._t0_wall = new
        self.start_edit.setText(local_hms(self._t0_wall)
                                if self._t0_wall is not None else "")

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

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self.panel is None:
            return
        width = min(PANEL_W, max(PANEL_MIN_W, int(self.width() * PANEL_SHARE)))
        if self.panel.width() != width:
            self.panel.setFixedWidth(width)

    # --- keyboard ---------------------------------------------------------
    def event(self, event):
        """Intercept Tab before Qt spends it on focus navigation.

        ``QWidget::event()`` hands Tab to ``focusNextPrevChild()`` and calls
        ``keyPressEvent()`` only if that fails, so a Tab branch in
        ``keyPressEvent`` can never run while the screen has a focusable child.
        """
        if (event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)):
            self._save_and_focus_bow()
            return True
        return super().event(event)

    def keyPressEvent(self, event):  # noqa: N802
        # Let focused text inputs (bow fields) handle their own keys first.
        if isinstance(self.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Up:
            self._move_selection(1)
            return
        if key == Qt.Key_Down:
            self._move_selection(-1)
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
