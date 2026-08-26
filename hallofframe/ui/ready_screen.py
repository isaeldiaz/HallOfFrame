"""Ready screen (REDESIGN-PLAN §2, §5; mockup state 2).

Full-size framing preview (READY only), a lag chip, the finish-line nudge, the
next-race picker (keyboard-driven), and the pre-race checks. The trackpad can
die mid-session, so the finish line is nudgeable with ←/→ (0.5 %, Shift 0.1 %)
and a numeric percentage field — never drag-only (§5).

The picker is a view model of rows (BEHAVIOUR §9): ``_races`` holds only roster
rows (byte-faithful, written to the CSV by WP4) while ``_rows`` holds what the
combo currently displays — race rows, an optional create row (WP5), and the
always-last ``Unlisted race`` sentinel (WP7). The combo index maps 1:1 to
``_rows``, never to ``_races``, and selection is restored by key, not position.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from ..races import RaceInfo, _norm, near_misses, parse_key
from . import styles
from .preview_widget import PreviewWidget

UNLISTED_TEXT = "Unlisted race"

# Custom item role for the skipped-row strike-through (Qt has no such standard
# role; the delegate reads it back to strike the rendered text).
_STRIKE_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class _Row:
    """One row in the picker view model (BEHAVIOUR §9).

    ``kind``: "race" (a roster row), "unlisted" (the sentinel), "create" (WP5),
    or "header" (decorative, not selectable). ``selectable`` is whether the
    operator can land on it; ``steppable`` is whether ↑/↓ will land on it — the
    sentinel and the create row are selectable but never steppable.
    """
    kind: str
    text: str
    race: RaceInfo | None = None
    key: tuple | None = None
    detail: str = ""
    selectable: bool = True
    steppable: bool = True
    dim: bool = False
    strike: bool = False
    create_race_no: str = ""
    create_heat_no: str = ""


class _ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ForegroundDelegate(QStyledItemDelegate):
    """Paint item text with the item's ``ForegroundRole`` color so recorded and
    skipped (grayed-out) races are visibly dimmed in the dropdown, and apply a
    strike-through for skipped rows. The combo's stylesheet `color:` otherwise
    forces every item to the primary color."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        data = index.data(Qt.ItemDataRole.ForegroundRole)
        option.palette.setColor(
            option.palette.ColorRole.Text,
            QColor(data) if data is not None else QColor(styles.TEXT_PRIMARY))
        strike = index.data(_STRIKE_ROLE)
        if strike:
            option.font.setStrikeOut(True)


class ReadyScreen(QWidget):
    finish_line_changed = Signal(float)
    race_selected = Signal(object)  # the selected _Row
    add_race_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, buffer, parent=None):
        super().__init__(parent)
        self._races: list[RaceInfo] = []
        self._rows: list[_Row] = []
        self._sel_row: int = 0
        self._recorded: set = set()
        self._skipped: set = set()
        self._filter_text: str = ""
        self._filter_active: bool = False
        self._filter_restore_key = None
        self._provisional: str | None = None
        self.setFocusPolicy(Qt.StrongFocus)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- left: framing preview ---
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(32, 32, 32, 32)
        lv.setSpacing(16)

        header = QHBoxLayout()
        cap = QLabel("Framing preview")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        header.addWidget(cap)
        self.lag_chip = QLabel("")
        self.lag_chip.setProperty("mono", True)
        self.lag_chip.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:14px;"
            f" color:{styles.TEXT_SECONDARY}; border:1px solid #4a6472;"
            " padding:5px 12px;")
        header.addWidget(self.lag_chip)
        header.addStretch(1)
        self.fl_label = QLabel("")
        self.fl_label.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
        header.addWidget(self.fl_label)
        # Numeric percentage field — a pointer-free way to set the finish line
        # exactly (§5). Entering % is fine: this field is focusable, not a button.
        self.fl_input = QLineEdit()
        self.fl_input.setFixedWidth(88)
        self.fl_input.setAlignment(Qt.AlignCenter)
        self.fl_input.setPlaceholderText("%")
        self.fl_input.editingFinished.connect(self._apply_pct)
        header.addWidget(self.fl_input)
        lv.addLayout(header)

        self.preview = PreviewWidget(buffer)
        self.preview.finish_line_moved.connect(self._on_line_moved)
        lv.addWidget(self.preview, 1)
        root.addWidget(left, 1)

        # --- right: next race + checks ---
        right = QWidget()
        right.setStyleSheet(f"border-left:1px solid {styles.PANEL_BORDER};")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(28, 32, 40, 32)
        rv.setSpacing(30)

        rv.addLayout(self._next_race_block())
        rv.addLayout(self._checks_block())
        rv.addStretch(1)
        root.addWidget(right)
        right.setFixedWidth(660)

    def _apply_pct(self) -> None:
        try:
            x = float(self.fl_input.text().strip().rstrip("%")) / 100.0
        except ValueError:
            return
        self.set_finish_line(max(0.0, min(1.0, x)))
        self.finish_line_changed.emit(self.preview.finish_line_x)

    def _on_line_moved(self, x: float) -> None:
        self.set_finish_line(x)
        self.finish_line_changed.emit(x)

    # --- next race picker -------------------------------------------------
    def _next_race_block(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        caprow = QHBoxLayout()
        cap = QLabel("Next race")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        caprow.addWidget(cap)
        caprow.addStretch(1)
        hint = QLabel("↑/↓ change · E rename · / find")
        hint.setProperty("mono", True)
        hint.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:13px;"
            f" color:{styles.TEXT_FAINT};")
        caprow.addWidget(hint)
        col.addLayout(caprow)

        # Filter field (WP3), hidden until `/`.
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter race number or name")
        self.filter_edit.installEventFilter(self)
        self.filter_edit.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:20px;"
            f" background:#0b0f12; border:1px solid {styles.BLUE};"
            " border-radius:2px; padding:8px 12px;")
        self.filter_edit.textChanged.connect(self._on_filter_edit)
        self.filter_edit.hide()
        col.addWidget(self.filter_edit)
        self.filter_count = QLabel("")
        self.filter_count.setStyleSheet(
            f"color:{styles.TEXT_FAINT}; font-size:14px;")
        self.filter_count.hide()
        col.addWidget(self.filter_count)

        self.race_combo = QComboBox()
        self.race_combo.setFocusPolicy(Qt.ClickFocus)
        self.race_combo.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:20px; font-weight:600;"
            f" background:{styles.PANEL}; border:1px solid #2c3942;"
            " padding:14px 16px;")
        self.race_combo.currentIndexChanged.connect(self._on_combo_index)
        self.race_combo.view().setItemDelegate(_ForegroundDelegate(self.race_combo))
        col.addWidget(self.race_combo)

        # Roster chip + hint row (F1).
        chiprow = QHBoxLayout()
        chiprow.setSpacing(10)
        self.roster_chip = QLabel("")
        self.roster_chip.setProperty("mono", True)
        self.roster_chip.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:13px;"
            f" color:{styles.TEXT_SECONDARY}; border:1px solid #2c3942;"
            " border-radius:4px; padding:5px 11px;")
        self.roster_chip.hide()
        chiprow.addWidget(self.roster_chip)
        self.roster_dup = _ClickLabel("")
        self.roster_dup.setProperty("mono", True)
        self.roster_dup.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:13px;"
            f" color:{styles.AMBER_TEXT}; border:1px solid #3a2c10;"
            f" background:#1e1608; border-radius:4px; padding:5px 11px;")
        self.roster_dup.hide()
        chiprow.addWidget(self.roster_dup)
        chiprow.addStretch(1)
        self._race_hint = QLabel("")
        self._race_hint.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
        chiprow.addWidget(self._race_hint)
        for text, sig in (("Add race…", self.add_race_clicked),
                          ("Skip", self.skip_clicked)):
            action = _ClickLabel(text)
            action.setStyleSheet(
                f"color:{styles.TEXT_DIM}; font-size:14px; padding:3px 8px;")
            action.clicked.connect(sig)
            chiprow.addWidget(action)
        col.addLayout(chiprow)
        return col

    def _checks_block(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        cap = QLabel("Pre-race checks")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        col.addWidget(cap)
        self._checks_host = QWidget()
        self._checks_v = QVBoxLayout(self._checks_host)
        self._checks_v.setContentsMargins(0, 0, 0, 0)
        self._checks_v.setSpacing(6)
        col.addWidget(self._checks_host)
        return col

    def _on_combo_index(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self._sel_row = index
            self.race_selected.emit(self._rows[index])

    # --- public API -------------------------------------------------------
    def set_races(self, races: list[RaceInfo],
                  recorded: set | None = None,
                  skipped: set | None = None) -> None:
        """Populate the picker. Races already stored in the DB (*recorded*, a set
        of normalised ``RaceInfo.key`` values) and races skipped in the roster
        (*skipped*) are grayed out (still selectable, so a race can be
        overwritten); the default selection skips them to the next available."""
        self._races = list(races)
        self._recorded = set(recorded or ())
        self._skipped = set(skipped or ())
        self._rebuild_rows()
        self._sel_row = self._first_unrecorded()
        self._sync_combo()

    def refresh_recorded(self, recorded: set | None = None,
                         skipped: set | None = None) -> None:
        """Re-apply the grayed-out styling from *recorded*/*skipped* without
        disturbing the operator's current selection (overwrite stays available)."""
        if recorded is not None:
            self._recorded = set(recorded)
        if skipped is not None:
            self._skipped = set(skipped)
        sel_key = self.selected_key()
        was_unlisted = self.selected_is_unlisted()
        self._rebuild_rows()
        if was_unlisted:
            self._sel_row = self._unlisted_index()
        else:
            self._restore_selection(sel_key)
        self._sync_combo()

    def selected_key(self):
        if 0 <= self._sel_row < len(self._rows):
            return self._rows[self._sel_row].key
        return None

    def select_first_unrecorded(self) -> None:
        """Jump the default selection to the first race not yet recorded."""
        self._sel_row = self._first_unrecorded()
        self._sync_combo()

    # --- row model --------------------------------------------------------
    def _race_matches(self, race: RaceInfo, q: str) -> bool:
        return (q in _fold(race.race_no)) or (q in _fold(race.name))

    def _rebuild_rows(self) -> None:
        q = _fold(self._filter_text)
        rows: list[_Row] = []

        # 1. Visible race rows (filtered, or all).
        if self._filter_active and q:
            matched = [r for r in self._races if self._race_matches(r, q)]
            # Heat-group completeness: a race-number match shows every heat (F2).
            # Group by the normalised race number so "0102" finds "102" and all
            # of its heats.
            num_hits = {_norm(r.race_no, drop_h=True)
                        for r in matched if q in _fold(r.race_no)}
            extra = [r for r in self._races
                     if _norm(r.race_no, drop_h=True) in num_hits]
            seen = {r.key for r in matched}
            for r in extra:
                if r.key not in seen:
                    seen.add(r.key)
                    matched.append(r)
            exact = bool(matched)
        else:
            matched = list(self._races)
            exact = True

        for r in matched:
            rows.append(self._race_row(r))

        # 2. Near misses + create row (WP5), only while filtering.
        if self._filter_active and q:
            if not exact:
                for r in near_misses(self._races, self._filter_text):
                    rows.append(self._race_row(r, detail="↵ select", dim=False))
                parsed = parse_key(self._filter_text)
                if parsed is not None:
                    rn, hn = parsed
                    label = f"Add heat H{hn} to race {rn}…" if hn else \
                        f"Add race {rn} to the roster…"
                    rows.append(_Row(
                        kind="create", text=label, selectable=True,
                        steppable=False, detail="↓ then ↵",
                        create_race_no=rn, create_heat_no=hn))

        # 3. Unlisted race sentinel, always last (WP7).
        rows.append(_Row(
            kind="unlisted", text=UNLISTED_TEXT, race=None, key=None,
            selectable=True, steppable=False, detail="arms immediately", dim=True))

        self._rows = rows

    def _race_row(self, race: RaceInfo, detail: str = "", dim: bool | None = None) -> _Row:
        if dim is None:
            dim = race.key in self._recorded or race.key in self._skipped
        steppable = race.key not in self._recorded and race.key not in self._skipped
        strike = race.key in self._skipped
        if race.key in self._recorded:
            detail = detail or "recorded"
        elif race.key in self._skipped:
            detail = detail or "skipped"
        return _Row(kind="race", text=race.display, race=race, key=race.key,
                    detail=detail, selectable=True, steppable=steppable,
                    dim=dim, strike=strike)

    def _row_index_by_key(self, key) -> int | None:
        if key is None:
            return None
        for i, row in enumerate(self._rows):
            if row.kind == "race" and row.key == key:
                return i
        return None

    def _restore_selection(self, key) -> None:
        if key is not None:
            idx = self._row_index_by_key(key)
            if idx is not None:
                self._sel_row = idx
                return
        # Key gone: fall back to the first steppable row.
        self._sel_row = self._first_unrecorded()

    def _first_unrecorded(self) -> int:
        for i, row in enumerate(self._rows):
            if row.kind == "race" and row.steppable:
                return i
        # All recorded/skipped, or no races: the unlisted sentinel.
        for i, row in enumerate(self._rows):
            if row.kind == "unlisted":
                return i
        return 0

    def _sync_combo(self) -> None:
        self.race_combo.blockSignals(True)
        self.race_combo.clear()
        for row in self._rows:
            text = f"{row.text}\t{row.detail}" if row.detail else row.text
            self.race_combo.addItem(text)
            self.race_combo.setItemData(
                self.race_combo.count() - 1,
                styles.TEXT_FAINT if row.dim else None,
                Qt.ItemDataRole.ForegroundRole)
            self.race_combo.setItemData(
                self.race_combo.count() - 1,
                row.strike, _STRIKE_ROLE)
        if self._rows:
            self._sel_row = max(0, min(self._sel_row, len(self._rows) - 1))
            self.race_combo.setCurrentIndex(self._sel_row)
        self.race_combo.blockSignals(False)
        self._refresh_race()

    def _refresh_race(self) -> None:
        total = len(self._races)
        if not self._filter_active:
            pos = 0
            row = self.current_row()
            if row.kind == "race" and row.race is not None:
                pos = self._races.index(row.race) + 1 if row.race in self._races else 0
            self._race_hint.setText(
                f"{pos} of {total} · Load roster…" if total else "Load roster…")
        else:
            n = sum(1 for r in self._rows if r.kind == "race")
            self._race_hint.setText(f"{n} match{'es' if n != 1 else ''}")

    # --- filter (WP3) -----------------------------------------------------
    def begin_filter(self) -> None:
        self._filter_restore_key = self.selected_key()
        self.filter_edit.show()
        self.filter_count.show()
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    def _on_filter_edit(self, text: str) -> None:
        self._filter_text = text
        self._filter_active = bool(text)
        sel_key = self.selected_key()
        self._rebuild_rows()
        if not self._filter_active:
            self._restore_selection(sel_key)
        else:
            self._sel_row = self._first_unrecorded()
        self._sync_combo()
        self._update_filter_count()

    def _update_filter_count(self) -> None:
        q = _fold(self._filter_text)
        n = sum(1 for r in self._rows if r.kind == "race")
        if not self._filter_active:
            self.filter_count.setText("")
            return
        if q and n:
            self.filter_count.setText(f"{n} matches")
        elif q and self._rows and any(r.kind == "unlisted" for r in self._rows):
            self.filter_count.setText("no match")

    def clear_filter(self) -> None:
        self.filter_edit.setText("")
        self.filter_edit.hide()
        self.filter_count.hide()
        self._filter_active = False
        self._filter_text = ""
        self._rebuild_rows()
        self._restore_selection(self._filter_restore_key)
        self._filter_restore_key = None
        self._sync_combo()

    def move_filter_selection(self, delta: int) -> None:
        """Move ↑/↓ within the filtered set. A deliberate ``↓`` reaches the
        create row and the unlisted sentinel; ``↑`` never lands on either, so a
        create row is reached by ``↓`` and never highlighted by default (WP5)."""
        if not self._rows:
            return
        n = len(self._rows)
        for step in range(1, n + 1):
            idx = (self._sel_row + (delta * step)) % n
            row = self._rows[idx]
            if row.steppable or (row.kind in ("create", "unlisted") and delta > 0):
                self._sel_row = idx
                self._sync_combo()
                return

    # --- selection --------------------------------------------------------
    def current_row(self) -> _Row:
        if 0 <= self._sel_row < len(self._rows):
            return self._rows[self._sel_row]
        return _Row(kind="unlisted", text=UNLISTED_TEXT, selectable=True,
                    steppable=False, dim=True)

    def current_selection(self) -> tuple[RaceInfo | None, bool]:
        """Return ``(race, is_unlisted)`` for the selected row.

        The unlisted race's provisional (timestamp) name is cached so it stays
        stable across status ticks and through arm→start; it is regenerated for
        a new unlisted race whenever the selection moves off the sentinel.
        """
        row = self.current_row()
        if row.kind == "unlisted":
            import time
            if self._provisional is None:
                self._provisional = time.strftime("Race-%Y%m%d-%H%M%S")
            return RaceInfo(name=self._provisional), True
        # Leaving the sentinel invalidates any pending provisional name.
        self._provisional = None
        return row.race, False

    def current_race(self) -> RaceInfo:
        race, _ = self.current_selection()
        if race is not None:
            return race
        import time
        return RaceInfo(name=time.strftime("Race-%Y%m%d-%H%M%S"))

    def reset_provisional(self) -> None:
        """Discard the cached provisional name so the next unlisted race gets a
        fresh timestamp (called when a race ends)."""
        self._provisional = None

    def current_race_name(self) -> str:
        return self.current_race().display

    def selected_is_unlisted(self) -> bool:
        return self.current_row().kind == "unlisted"

    def next_race(self) -> None:
        if self._filter_active:
            self.move_filter_selection(1)
        else:
            self._step_to_next(recorded_skip=True)

    def prev_race(self) -> None:
        if self._filter_active:
            self.move_filter_selection(-1)
        else:
            self._step_to_next(recorded_skip=False)

    def _step_to_next(self, recorded_skip: bool) -> None:
        if not self._rows:
            return
        n = len(self._rows)
        start = self._sel_row
        for step in range(1, n + 1):
            idx = (start + (step if recorded_skip else -step)) % n
            row = self._rows[idx]
            if row.steppable:
                self._sel_row = idx
                self._sync_combo()
                return
        # Every race is recorded/skipped: fall back to the unlisted sentinel.
        self._sel_row = self._first_unrecorded()
        self._sync_combo()

    def end_select_unlisted(self) -> None:
        self._sel_row = self._unlisted_index()
        self._sync_combo()

    def _unlisted_index(self) -> int:
        for i, row in enumerate(self._rows):
            if row.kind == "unlisted":
                return i
        return 0

    def set_roster(self, filename: str, count: int, loaded_at: str,
                   duplicates: int = 0, dup_callback=None) -> None:
        """Fill the roster chip (F1) and the optional amber duplicate chip."""
        if filename:
            self.roster_chip.setText(
                f"{filename} · {count} races · {loaded_at}")
            self.roster_chip.show()
        else:
            self.roster_chip.hide()
        if duplicates:
            self.roster_dup.setText(
                f"{duplicates} possible duplicate"
                f"{'s' if duplicates != 1 else ''}")
            self.roster_dup.show()
            if dup_callback is not None:
                self.roster_dup.clicked.connect(dup_callback)
        else:
            self.roster_dup.hide()

    def set_finish_line(self, x: float) -> None:
        self.preview.set_finish_line(x)
        self.fl_label.setText(
            f"Finish line: <span style='font-family:{styles.FONT_MONO};"
            f" color:{styles.TEXT_SECONDARY}'>{x*100:.1f} %</span>"
            " · ←/→ to nudge")
        if self.fl_input.text() != f"{x*100:.1f}":
            self.fl_input.setText(f"{x*100:.1f}")

    def set_checks(self, checks: list[dict]) -> None:
        while self._checks_v.count():
            item = self._checks_v.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for c in checks:
            self._checks_v.addWidget(self._check_row(c))

    def _check_row(self, c: dict) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 15, 18, 15)
        lay.setSpacing(16)
        mark = QLabel(c["mark"])
        mark.setProperty("mono", True)
        mark.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:20px;"
            f" color:{c['accent']}; width:20px;")
        lay.addWidget(mark)
        label = QLabel(c["label"])
        label.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:19px;")
        lay.addWidget(label, 1)
        detail = QLabel(c["detail"])
        detail.setProperty("mono", True)
        detail.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:16px;"
            f" color:{styles.TEXT_DIM};")
        lay.addWidget(detail)
        w.setStyleSheet(
            f"background:{styles.PANEL}; border-left:3px solid {c['accent']};")
        return w

    def set_lag(self, lag_s: float | None) -> None:
        if lag_s is None:
            self.lag_chip.setText("")
            self.lag_chip.hide()
        else:
            live = lag_s < 0.3
            self.lag_chip.setText(
                f"lag +{lag_s:.1f} s · {'live' if live else 'not live'}")
            self.lag_chip.setStyleSheet(
                f"font-family:'{styles.FONT_MONO}'; font-size:14px;"
                f" color:{styles.GREEN_TEXT if live else styles.AMBER_TEXT};"
                f" border:1px solid #4a6472; padding:5px 12px;")
            self.lag_chip.show()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.filter_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Up:
                self.move_filter_selection(-1)
                return True
            if event.key() == Qt.Key_Down:
                self.move_filter_selection(1)
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.clear_filter()   # Enter selects the highlighted row, never creates
                return True
            if event.key() == Qt.Key_Escape:
                self.clear_filter()   # Esc clears the filter and restores the selection
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Up:
            self.prev_race()
            return
        if event.key() == Qt.Key_Down:
            self.next_race()
            return
        if event.key() == Qt.Key_Slash:
            self.begin_filter()
            return
        if event.key() == Qt.Key_End:
            self.end_select_unlisted()
            return
        super().keyPressEvent(event)


def _fold(s) -> str:
    import unicodedata
    s = (s or "").casefold()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if not unicodedata.combining(c))
