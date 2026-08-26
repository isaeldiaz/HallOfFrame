"""About / diagnostics overlay (F1).

A non-modal, full-surface overlay over the main window — not a QDialog, not a
new AppState: the app's state machine (``ui/state.py``) is untouched, so
``derive_state()`` and its tests keep working, and nothing here can interpose
itself between the trigger and the controller.

Every value is read at display time from the live objects (config, calibration,
the installed packages) — the screen is only useful for a bug report if it is
telling the truth.

Layout mirrors the rest of the console (REDESIGN-PLAN §1, §3): a band on top,
two columns of content, a key bar at the bottom, all from ``styles`` tokens.
"""
from __future__ import annotations

import json
import platform
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from .. import __version__
from . import styles
from .widgets import KeyCap

CONTACT = "isael.diaz@gmail.com"
COPYRIGHT = "© 2026 Isael Diaz. All rights reserved."
THIRD_PARTY = ("PySide6 (LGPL-3.0) · IBM Plex Sans & Mono (SIL OFL 1.1) · "
               "python-evdev (revised BSD) · Pillow (MIT-CMU)")

KEY_REFERENCE = [
    ("Ctrl+S", "Arm"),
    ("ENTER", "Start race (t0)"),
    ("SPACE", "Record crossing"),
    ("F12", "End race"),
    ("Ctrl+Z", "Undo last crossing"),
    ("C", "Calibrate"),
    ("R", "Review crossings"),
    ("E", "Copy race as Excel"),
    ("D", "Save database CSV"),
    ("N", "Next race"),
    ("F1", "About / diagnostics"),
    ("Ctrl+Q", "Quit"),
]


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "not installed"


def build_stamp() -> str:
    """Commit + date written by the packaging step, or an honest dash.

    ``hallofframe/_build.py`` is generated at install time (INSTALL.md) with
    ``COMMIT`` and ``DATE``; a source checkout has no such file.
    """
    try:
        from .._build import COMMIT, DATE  # type: ignore
        return f"{COMMIT[:7]} · {DATE}"
    except Exception:
        return "source checkout"


def environment_rows(config) -> list[tuple[str, str]]:
    """The diagnostics grid: live values only, never hard-coded versions."""
    trig = config.section("trigger")
    device = trig["device_path"] or "Qt fallback"
    if trig["device_path"] and trig["grab_device"]:
        device += " · grabbed"
    latency = calibrated_latency(config)
    return [
        ("Python", platform.python_version()),
        ("PySide6", _pkg_version("PySide6_Essentials")),
        ("evdev", _pkg_version("evdev")),
        ("Trigger device", device),
        ("Viewing mode", config.section("timing")["viewing_mode"]),
        ("Calibrated latency",
         f"{latency:.0f} ms" if latency is not None else "not calibrated"),
    ]


def calibrated_latency(config) -> float | None:
    path = Path(config.data_root) / "calibration.json"
    if not path.exists():
        return None
    try:
        return float(json.loads(path.read_text()).get("latency_median_ms", 0.0))
    except Exception:
        return None


def path_rows(config) -> list[tuple[str, str]]:
    root = Path(config.data_root)
    return [
        ("Data directory", str(root)),
        ("Database", str(root / "regatta.db")),
        ("Config", str(root / "config.toml")),
        ("Log", str(root / "logs" / "regatta-app.jsonl")),
        ("Calibration", str(root / "calibration.json")),
    ]


# --- small style helpers (tokens only) ------------------------------------
def _caption(text: str, size: int = 13) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:{size}px;"
                      " letter-spacing:.12em; text-transform:uppercase;")
    return lab


def _mono(text: str, size: int = 20, color: str = styles.TEXT_PRIMARY) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("mono", True)
    lab.setMinimumWidth(1)
    lab.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:{size}px;"
                      f" font-weight:500; color:{color};")
    return lab


def _prose(text: str, size: int = 19, color: str = styles.TEXT_DIM) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setMinimumWidth(1)
    lab.setStyleSheet(f"color:{color}; font-size:{size}px;")
    return lab


def _divider(horizontal: bool = True) -> QFrame:
    line = QFrame()
    if horizontal:
        line.setFixedHeight(1)
    else:
        line.setFixedWidth(1)
    line.setStyleSheet(f"background:{styles.DIVIDER};")
    return line


def logo_pixmap(size: int = 132) -> QPixmap:
    """The app mark: a frame with viewfinder corners and the finish line."""
    path = Path(__file__).resolve().parent.parent / "assets" / "logo.svg"
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    if not path.exists():
        return pix
    from PySide6.QtGui import QPainter
    renderer = QSvgRenderer(str(path))
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return pix


class AboutOverlay(QWidget):
    """Full-surface About screen. ``toggle()`` from F1; Esc or F1 closes it."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget{{background:{styles.BG};}}")
        self.setFocusPolicy(Qt.StrongFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._band())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._identity_column())
        body.addWidget(_divider(horizontal=False))
        body.addWidget(self._diagnostics_column(), 1)
        lay.addLayout(body, 1)
        lay.addWidget(self._keybar())
        self.hide()

    # ------------------------------------------------------------------ band
    def _band(self) -> QWidget:
        band = QWidget()
        band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        band.setFixedHeight(104)
        theme = styles.STATE_THEME["review"]  # blue: an informational state
        band.setStyleSheet(f"QWidget{{background:{theme['bg']};}}")
        lay = QHBoxLayout(band)
        lay.setContentsMargins(40, 0, 40, 0)
        lay.setSpacing(28)

        dot = QLabel()
        dot.setFixedSize(20, 20)
        dot.setStyleSheet(f"border-radius:10px; background:{theme['accent']};")
        lay.addWidget(dot)
        name = QLabel("ABOUT")
        name.setProperty("mono", True)
        name.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:40px; font-weight:600;"
            f" letter-spacing:.08em; color:{theme['text']};")
        lay.addWidget(name)
        lay.addWidget(_divider(horizontal=False))

        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_caption("Application", 14))
        title = QLabel("HallOfFrame — Regatta Finish-Line Timer")
        title.setStyleSheet(
            f"color:{styles.TEXT_PRIMARY}; font-size:26px; font-weight:600;")
        col.addWidget(title)
        lay.addLayout(col)
        lay.addStretch(1)

        for label, value in (("Version", __version__), ("Build", build_stamp())):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            cap = _caption(label)
            cap.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val = _mono(value, 22, styles.TEXT_SECONDARY)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cell.addWidget(cap)
            cell.addWidget(val)
            lay.addLayout(cell)
        return band

    # -------------------------------------------------------------- left column
    def _identity_column(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(620)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(56, 64, 56, 64)
        lay.setSpacing(40)

        lock = QHBoxLayout()
        lock.setSpacing(28)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(132))
        mark.setFixedSize(132, 132)
        lock.addWidget(mark, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(10)
        wordmark = QLabel(
            f"HallOf<span style='color:{styles.RED_TEXT};'>Frame</span>")
        wordmark.setTextFormat(Qt.RichText)
        wordmark.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:44px; font-weight:600;")
        words.addWidget(wordmark)
        words.addWidget(_prose(
            "Millisecond finish timing from kernel timestamps.", 19))
        words.addStretch(1)
        lock.addLayout(words, 1)
        lay.addLayout(lock)
        lay.addWidget(_divider())

        for label, widget in (
            ("Copyright", _prose(COPYRIGHT, 20, styles.TEXT_SECONDARY)),
            ("Developer contact", _mono(CONTACT, 22, styles.BLUE_TEXT)),
            ("Third-party", _prose(THIRD_PARTY, 18)),
        ):
            block = QVBoxLayout()
            block.setSpacing(8)
            block.addWidget(_caption(label))
            block.addWidget(widget)
            lay.addLayout(block)

        lay.addStretch(1)
        return col

    # ------------------------------------------------------------- right column
    def _diagnostics_column(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(56, 64, 56, 64)
        lay.setSpacing(44)

        env = QVBoxLayout()
        env.setSpacing(18)
        env.addWidget(_caption("Environment"))
        grid = QGridLayout()
        grid.setSpacing(1)
        self._env_cells: dict[str, QLabel] = {}
        for i, (label, value) in enumerate(environment_rows(self.config)):
            cell = QWidget()
            cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            cell.setStyleSheet(
                f"QWidget{{background:{styles.PANEL}; border:1px solid"
                f" {styles.PANEL_BORDER};}}")
            clay = QVBoxLayout(cell)
            clay.setContentsMargins(22, 18, 22, 18)
            clay.setSpacing(6)
            clay.addWidget(_caption(label, 12))
            val = _mono(value, 20)
            clay.addWidget(val)
            self._env_cells[label] = val
            grid.addWidget(cell, i // 3, i % 3)
        env.addLayout(grid)
        lay.addLayout(env)

        two = QHBoxLayout()
        two.setSpacing(44)

        keys = QVBoxLayout()
        keys.setSpacing(18)
        keys.addWidget(_caption("Keyboard reference"))
        rows = QVBoxLayout()
        rows.setSpacing(0)
        for key, label in KEY_REFERENCE:
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row.setStyleSheet("QWidget{border-bottom:1px solid #171e23;}")
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 10, 0, 10)
            rlay.setSpacing(18)
            cap = QLabel(key)
            cap.setAlignment(Qt.AlignCenter)
            cap.setMinimumWidth(84)
            cap.setMinimumHeight(32)
            cap.setStyleSheet(
                f"font-family:'{styles.FONT_MONO}'; font-weight:600;"
                f" font-size:17px; color:{styles.TEXT_SECONDARY};"
                " border:1px solid #2c3942; border-radius:3px; padding:4px 10px;")
            rlay.addWidget(cap)
            rlay.addWidget(_prose(label, 19), 1)
            rows.addWidget(row)
        keys.addLayout(rows)
        keys.addStretch(1)
        two.addLayout(keys, 1)

        paths = QVBoxLayout()
        paths.setSpacing(18)
        paths.addWidget(_caption("Paths"))
        prows = QVBoxLayout()
        prows.setSpacing(0)
        for label, value in path_rows(self.config):
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row.setStyleSheet("QWidget{border-bottom:1px solid #171e23;}")
            rlay = QVBoxLayout(row)
            rlay.setContentsMargins(0, 10, 0, 10)
            rlay.setSpacing(5)
            rlay.addWidget(_prose(label, 15, styles.TEXT_FAINT))
            rlay.addWidget(_mono(value, 18, styles.TEXT_SECONDARY))
            prows.addWidget(row)
        paths.addLayout(prows)
        paths.addStretch(1)
        two.addLayout(paths, 1)

        lay.addLayout(two)
        lay.addStretch(1)
        return col

    # ---------------------------------------------------------------- key bar
    def _keybar(self) -> QWidget:
        bar = QWidget()
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(96)
        bar.setStyleSheet(f"QWidget{{background:{styles.PANEL};"
                          f" border-top:1px solid {styles.PANEL_BORDER};}}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(40, 0, 40, 0)
        lay.setSpacing(14)
        lay.addWidget(KeyCap("F1", "Close about", hot=True, callback=self.close_about))
        lay.addWidget(KeyCap("Esc", "Close about", callback=self.close_about))
        lay.addStretch(1)
        note = _prose("Licensed for use by the regatta timing crew. "
                      "No warranty.", 15, styles.TEXT_FAINT)
        note.setWordWrap(False)
        lay.addWidget(note)
        return bar

    # ----------------------------------------------------------------- actions
    def refresh(self) -> None:
        """Re-read the live values (calibration may have changed since open)."""
        for label, value in environment_rows(self.config):
            if label in self._env_cells:
                self._env_cells[label].setText(value)

    def open_about(self) -> None:
        self.refresh()
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()

    def close_about(self) -> None:
        self.hide()
        parent = self.parentWidget()
        if parent is not None:
            parent.setFocus()

    def toggle(self) -> bool:
        """Open or close; returns True if the overlay is now visible."""
        if self.isVisible():
            self.close_about()
            return False
        self.open_about()
        return True

    # ------------------------------------------------------------------ events
    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Escape, Qt.Key_F1):
            self.close_about()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        parent = self.parentWidget()
        if parent is not None and self.isVisible():
            self.setGeometry(parent.rect())


__all__ = ["AboutOverlay", "environment_rows", "path_rows", "build_stamp",
           "logo_pixmap", "KEY_REFERENCE", "CONTACT"]
