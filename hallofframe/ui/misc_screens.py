"""Armed and Race-Over screens (REDESIGN-PLAN mockup states 3, 4).

ARMED: a single prompt to press ENTER (sets t0 on the trigger device).
RACE_OVER: a summary (crossings / first / last / flagged) plus the Review
prompt. Both are non-modal states of the one window — not dialogs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..export import format_elapsed
from . import styles
from .widgets import KeyCap


class ArmedScreen(QWidget):
    def __init__(self, on_start=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 0, 40, 0)
        lay.addStretch(1)
        mid = QHBoxLayout()
        mid.setSpacing(36)
        mid.addStretch(1)
        self.keycap = KeyCap("ENTER", "Start race", hot=True, callback=on_start)
        mid.addWidget(self.keycap)
        col = QVBoxLayout()
        col.setSpacing(10)
        self.title = QLabel("Press to start the race")
        self.title.setMinimumWidth(1)
        self.title.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:46px; font-weight:600;")
        col.addWidget(self.title)
        self.sub = QLabel("")
        # Prose, and it carries the device path: wrap it so the sentence cannot
        # set the window's minimum width.
        self.sub.setWordWrap(True)
        self.sub.setMinimumWidth(1)
        self.sub.setStyleSheet(f"color:{styles.TEXT_DIM}; font-size:22px;")
        col.addWidget(self.sub)
        mid.addLayout(col)
        mid.addStretch(1)
        lay.addLayout(mid)
        lay.addStretch(1)

    def set_trigger_label(self, text: str) -> None:
        self.sub.setText(
            f"Clock starts on the kernel timestamp of that press. {text}")


class RaceOverScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 0, 40, 0)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(56)
        row.addStretch(1)
        self._stats: dict[str, QLabel] = {}
        for label in ("Crossings", "First", "Last", "Flagged"):
            col = QVBoxLayout()
            col.setSpacing(8)
            cap = QLabel(label)
            cap.setStyleSheet(
                f"color:{styles.TEXT_FAINT}; font-size:14px;"
                " letter-spacing:.12em; text-transform:uppercase;")
            val = QLabel("")
            val.setProperty("mono", True)
            val.setStyleSheet(
                f"font-family:'{styles.FONT_MONO}'; font-size:62px;"
                f" font-weight:600; color:{styles.TEXT_PRIMARY};")
            col.addWidget(cap)
            col.addWidget(val)
            row.addLayout(col)
            self._stats[label] = val
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(30)
        lay.addStretch(1)

    def set_summary(self, captures: list[dict]) -> None:
        self._stats["Crossings"].setText(str(len(captures)))
        if captures:
            self._stats["First"].setText(format_elapsed(captures[0]["elapsed_s"]))
            self._stats["Last"].setText(format_elapsed(captures[-1]["elapsed_s"]))
        else:
            self._stats["First"].setText("—")
            self._stats["Last"].setText("—")
        flagged = sum(1 for c in captures
                      if c["image_flag"] in ("approximate", "missing")
                      or c["debounce_suspect"])
        self._stats["Flagged"].setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:62px; font-weight:600;"
            f" color:{styles.AMBER_TEXT if flagged else styles.TEXT_PRIMARY};")
        self._stats["Flagged"].setText(str(flagged))
