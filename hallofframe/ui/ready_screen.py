"""Ready screen (REDESIGN-PLAN §2, §5; mockup state 2).

Full-size framing preview (READY only), a lag chip, the finish-line nudge, the
next-race picker (keyboard-driven), and the pre-race checks. The trackpad can
die mid-session, so the finish line is nudgeable with ←/→ (0.5 %, Shift 0.1 %)
and a numeric percentage field — never drag-only (§5).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QVBoxLayout, QWidget)

from . import styles
from .preview_widget import PreviewWidget


class ReadyScreen(QWidget):
    finish_line_changed = Signal(float)
    race_selected = Signal(int)

    def __init__(self, buffer, parent=None):
        super().__init__(parent)
        self._race_names: list[str] = []
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
    def set_races(self, names: list[str], index: int = 0) -> None:
        self._race_names = names
        self.race_combo.blockSignals(True)
        self.race_combo.clear()
        self.race_combo.addItems(names)
        self._race_index = min(max(index, 0), max(0, len(names) - 1))
        if self.race_combo.count():
            self.race_combo.setCurrentIndex(self._race_index)
        self.race_combo.blockSignals(False)
        self._refresh_race()

    def _refresh_race(self) -> None:
        n = len(self._race_names)
        self._race_hint.setText(
            f"{self._race_index + 1 if n else 0} of {n} from races.xlsx"
            " · ↑/↓ or the dropdown to change")

    def current_race_name(self) -> str:
        if self._race_names:
            return self._race_names[self._race_index]
        import time
        return time.strftime("Race-%Y%m%d-%H%M%S")

    def next_race(self) -> None:
        if self._race_names:
            self.race_combo.setCurrentIndex(
                (self._race_index + 1) % len(self._race_names))

    def prev_race(self) -> None:
        if self._race_names:
            self.race_combo.setCurrentIndex(
                (self._race_index - 1) % len(self._race_names))

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
