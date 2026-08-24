"""Reusable console widgets (REDESIGN-PLAN §3, §5).

* ``KeyCap`` / ``KeyBar`` — the only race controls, shown as key caps with the
  active one highlighted green.
* ``StateBand`` — the top strip: state name, race name, health readouts, and for
  blocking states a named fix sentence.
* ``Toast`` — transient, dismissible bottom-right warning channel (replaces the
  red banner for warnings). No modal dialogs during a race (§7.5).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from . import styles
from .state import AppState


class KeyCap(QPushButton):
    """One key cap + label; clickable and never keyboard-focusable (§4).

    ``hot`` highlights the active action green. A ``None`` callback makes it a
    static (disabled) key cap used as a visual prompt.
    """

    def __init__(self, key: str, label: str, hot: bool = False,
                 callback=None, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoDefault(False)
        self.setText(f"{key}   —   {label}")
        self.setStyleSheet(styles.key_cap_style(hot))
        if callback is None:
            self.setEnabled(False)
        else:
            self.clicked.connect(callback)


class KeyBar(QWidget):
    """Horizontal strip of clickable KeyCaps, with an optional trailing note."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(40, 0, 40, 0)
        self.lay.setSpacing(14)
        self.lay.addStretch(1)
        self._note = None

    def clear(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._note = None

    def add(self, key: str, label: str, hot: bool = False, callback=None) -> None:
        cap = KeyCap(key, label, hot, callback)
        self.lay.insertWidget(self.lay.count() - 1, cap)

    def set_note(self, text: str) -> None:
        if self._note is None:
            self._note = QLabel(text)
            self._note.setProperty("mono", True)
            self._note.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
            self.lay.addWidget(self._note)
        else:
            self._note.setText(text)


class HealthReadout(QWidget):
    """Uppercase label + big colored mono value, right aligned."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        cap = QLabel(label)
        cap.setStyleSheet(
            f"color:{styles.TEXT_FAINT}; font-size:13px;"
            " letter-spacing:.12em; text-transform:uppercase;")
        cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value = QLabel("")
        self.value.setProperty("mono", True)
        self.value.setStyleSheet(
            f"color:{styles.TEXT_SECONDARY}; font-size:22px; font-weight:500;")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(cap)
        lay.addWidget(self.value)

    def set(self, value: str, color: str | None = None) -> None:
        self.value.setText(value)
        if color:
            self.value.setStyleSheet(
                f"color:{color}; font-size:22px; font-weight:500;")


class StateBand(QWidget):
    """Top 104 px band: state dot+name, race name, health readouts, fix line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(104)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(40, 0, 40, 0)
        self.lay.setSpacing(28)

        left = QHBoxLayout()
        left.setSpacing(16)
        self.dot = QLabel("")
        self.dot.setFixedSize(20, 20)
        self.dot.setStyleSheet("border-radius:10px;")
        self.name = QLabel("")
        self.name.setProperty("mono", True)
        self.name.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:40px; font-weight:600;"
            " letter-spacing:.08em;")
        left.addWidget(self.dot)
        left.addWidget(self.name)
        self.lay.addLayout(left)

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{styles.DIVIDER};")
        self.lay.addWidget(sep)

        self.race_col = QVBoxLayout()
        self.race_col.setSpacing(2)
        self.race_cap = QLabel("Race")
        self.race_cap.setStyleSheet(
            f"color:{styles.TEXT_FAINT}; font-size:14px;"
            " letter-spacing:.12em; text-transform:uppercase;")
        self.race_name = QLabel("")
        self.race_name.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:26px; font-weight:600;")
        self.race_col.addWidget(self.race_cap)
        self.race_col.addWidget(self.race_name)
        self.lay.addLayout(self.race_col)

        self._fix_label = QLabel("")
        self._fix_label.setWordWrap(True)
        self._fix_label.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:24px;")
        self.lay.addWidget(self._fix_label, 1)

        self.lay.addStretch(1)
        self.health_lay = QHBoxLayout()
        self.health_lay.setSpacing(34)
        self.lay.addLayout(self.health_lay)
        self._health: dict[str, HealthReadout] = {}

    def set_health(self, labels: list[str]) -> None:
        """Ensure a HealthReadout for each label (in order), dropping extras."""
        for key in list(self._health):
            if key not in labels:
                self._health[key].deleteLater()
                del self._health[key]
        for key in labels:
            if key not in self._health:
                r = HealthReadout(key)
                self.health_lay.addWidget(r)
                self._health[key] = r

    def set_health_value(self, key: str, value: str, color: str | None = None):
        if key in self._health:
            self._health[key].set(value, color)

    def set_state(self, state: AppState, race_name: str, fix: str = "") -> None:
        theme = styles.STATE_THEME[state.value]
        self.setStyleSheet(f"QWidget{{background:{theme['bg']};}}")
        self.dot.setStyleSheet(
            f"border-radius:10px; background:{theme['accent']};")
        self.name.setText(state.name.replace("_", " "))
        self.name.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:40px; font-weight:600;"
            f" letter-spacing:.08em; color:{theme['text']};")
        self.race_name.setText(race_name)
        self._fix_label.setText(fix)
        # Fix line replaces the race block for blocking states (§1).
        visible = not fix
        self.race_cap.setVisible(visible)
        self.race_name.setVisible(visible)
        self._fix_label.setVisible(bool(fix))


class Toast(QWidget):
    """Transient, dismissible warning anchored bottom-right. Never modal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget{{background:{styles.PANEL}; border:1px solid"
            f" {styles.AMBER}; border-radius:6px;}}"
            f" QLabel{{color:{styles.TEXT_SECONDARY}; font-size:16px;}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(16)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(640)
        lay.addWidget(self._label)
        self._close = QLabel("✕")
        self._close.setStyleSheet(
            f"color:{styles.TEXT_FAINT}; font-size:18px; padding:4px 8px;")
        lay.addWidget(self._close)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, timeout_ms: int = 6000) -> None:
        self._label.setText(text)
        self.adjustSize()
        self.show()
        self.raise_()
        if timeout_ms <= 0:
            self._timer.stop()  # persistent until dismissed/clicked
        else:
            self._timer.start(timeout_ms)

    def mousePressEvent(self, event):  # noqa: N802
        self.hide()
        super().mousePressEvent(event)

    def reposition(self, parent_w: int, parent_h: int) -> None:
        """Anchor bottom-right inside the parent window."""
        self.move(parent_w - self.width() - 24, parent_h - self.height() - 110)
