"""Ready screen (REDESIGN-PLAN §2, §5; mockup state 2).

Full-size framing preview (READY only), a lag chip, the finish-line nudge, the
next-race picker (keyboard-driven), and the pre-race checks. The trackpad can
die mid-session, so the finish line is nudgeable with ←/→ (0.5 %, Shift 0.1 %)
and a numeric percentage field — never drag-only (§5).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from ..races import RaceInfo
from . import styles
from .preview_widget import PreviewWidget


class _ForegroundDelegate(QStyledItemDelegate):
    """Paint item text with the item's ``ForegroundRole`` color so recorded
    (grayed-out) races are visibly dimmed in the dropdown. The combo's
    stylesheet `color:` otherwise forces every item to the primary color and
    ignores the model's foreground role."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        data = index.data(Qt.ItemDataRole.ForegroundRole)
        option.palette.setColor(
            option.palette.ColorRole.Text,
            QColor(data) if data is not None else QColor(styles.TEXT_PRIMARY))


class ReadyScreen(QWidget):
    finish_line_changed = Signal(float)
    race_selected = Signal(int)

    def __init__(self, buffer, parent=None):
        super().__init__(parent)
        self._races: list[RaceInfo] = []
        self._race_index = 0
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
        cap = QLabel("Next race")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        col.addWidget(cap)

        self.race_combo = QComboBox()
        self.race_combo.setFocusPolicy(Qt.ClickFocus)
        self.race_combo.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:20px; font-weight:600;"
            f" background:{styles.PANEL}; border:1px solid #2c3942;"
            " padding:14px 16px;")
        self.race_combo.currentIndexChanged.connect(self._on_combo_index)
        self.race_combo.view().setItemDelegate(_ForegroundDelegate(self.race_combo))
        col.addWidget(self.race_combo)

        hint = QLabel("")
        hint.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
        col.addWidget(hint)
        self._race_hint = hint
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
        if index >= 0:
            self._race_index = index
            self.race_selected.emit(index)

    # --- public API -------------------------------------------------------
    def set_races(self, races: list[RaceInfo],
                  recorded: set[tuple[str, str, str]] | None = None,
                  index: int | None = None) -> None:
        """Populate the dropdown. Races already stored in the DB (*recorded*, a
        set of ``(race_no, heat_no, name)`` triples) are grayed out (still
        selectable, so a race can be overwritten) and the default selection
        skips them to the next not-yet-recorded race."""
        self._races = list(races)
        self._recorded = set(recorded or ())
        self.race_combo.blockSignals(True)
        self.race_combo.clear()
        self.race_combo.addItems([r.display for r in self._races])
        self._apply_gray()
        if index is None:
            index = self._first_unrecorded()
        self._race_index = min(max(index, 0), max(0, len(self._races) - 1))
        if self.race_combo.count():
            self.race_combo.setCurrentIndex(self._race_index)
        self.race_combo.blockSignals(False)
        self._refresh_race()

    def refresh_recorded(self, recorded: set[tuple[str, str, str]] | None = None) -> None:
        """Re-apply the grayed-out styling from *recorded* without disturbing the
        operator's current selection. Call this whenever the set of recorded
        races changes (e.g. a race just finished), so a completed race turns gray
        immediately while the dropdown keeps its place."""
        self._recorded = set(recorded or ())
        self._apply_gray()

    def select_first_unrecorded(self) -> None:
        """Jump the default selection to the first race not yet recorded."""
        if not self._races:
            return
        self.race_combo.setCurrentIndex(self._first_unrecorded())

    def _apply_gray(self) -> None:
        for i, race in enumerate(self._races):
            self.race_combo.setItemData(
                i, styles.TEXT_FAINT if race.key in self._recorded else None,
                Qt.ItemDataRole.ForegroundRole)

    def _first_unrecorded(self) -> int:
        for i, race in enumerate(self._races):
            if race.key not in self._recorded:
                return i
        return 0

    def _refresh_race(self) -> None:
        n = len(self._races)
        self._race_hint.setText(
            f"{self._race_index + 1 if n else 0} of {n} from races.csv"
            " · ↑/↓ or the dropdown to change")

    def current_race(self) -> RaceInfo:
        if self._races:
            return self._races[self._race_index]
        import time
        return RaceInfo(name=time.strftime("Race-%Y%m%d-%H%M%S"))

    def current_race_name(self) -> str:
        return self.current_race().display

    def next_race(self) -> None:
        self._step_to_next(recorded_skip=True)

    def prev_race(self) -> None:
        self._step_to_next(recorded_skip=False)

    def _step_to_next(self, recorded_skip: bool) -> None:
        if not self._races:
            return
        n = len(self._races)
        start = self._race_index
        for step in range(1, n + 1):
            idx = (start + (step if recorded_skip else -step)) % n
            if self._races[idx].key not in self._recorded:
                self.race_combo.setCurrentIndex(idx)
                return
        # Every race is recorded: fall back to the default (first unrecorded).
        self.race_combo.setCurrentIndex(self._first_unrecorded())

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

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Up:
            self.prev_race()
            return
        if event.key() == Qt.Key_Down:
            self.next_race()
            return
        super().keyPressEvent(event)
