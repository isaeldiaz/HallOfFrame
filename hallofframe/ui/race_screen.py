"""Race screen (REDESIGN-PLAN §3, mockup state 1) — clock first.

Left column: elapsed clock (big), crossing count, and the last-capture panel
(the capture confirmation). Right column: newest-first crossing log. The
last-capture panel's border flashes in the state accent on each press — the
red banner is retired as a general-purpose channel.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..export import format_elapsed
from . import styles
from .crossing_list import CrossingLog


def fmt_clock(secs: float) -> str:
    ms = int(secs * 1000)
    hours, rem = divmod(ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis}"
    return f"{minutes:02d}:{seconds:02d}.{millis}"


class LastCapturePanel(QWidget):
    """Large saved photo + header. Doubles as the capture confirmation flash."""

    def __init__(self, accent: str, parent=None):
        super().__init__(parent)
        self.accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QHBoxLayout()
        cap = QLabel("Last capture")
        cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:15px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        self.heading = QLabel("#---")
        self.heading.setProperty("mono", True)
        self.heading.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:22px; font-weight:500;"
            f" color:{styles.TEXT_PRIMARY};")
        header.addWidget(cap)
        header.addSpacing(18)
        header.addWidget(self.heading)
        header.addStretch(1)
        self.offset = QLabel("")
        self.offset.setProperty("mono", True)
        self.offset.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:14px;"
            f" color:{styles.TEXT_DIM}; border:1px solid #3a4a54; padding:4px 12px;")
        header.addWidget(self.offset)
        hw = QWidget()
        hw.setStyleSheet(f"background:{styles.PANEL}; border-bottom:1px solid"
                         f" {styles.PANEL_BORDER};")
        hw.setLayout(header)
        hw.setFixedHeight(54)
        lay.addWidget(hw)

        self.photo = QLabel("")
        self.photo.setAlignment(Qt.AlignCenter)
        self.photo.setStyleSheet(f"background:{styles.LETTERBOX};")
        lay.addWidget(self.photo, 1)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self._flash_active = False

    def set_accent(self, accent: str) -> None:
        self.accent = accent

    def set_capture(self, sequence: int, elapsed_s: float) -> None:
        self.heading.setText(f"#{sequence:03d} · {format_elapsed(elapsed_s)}")
        self.offset.setText("+0 ms · nearest frame")
        self._flash()

    def set_photo(self, path: str) -> None:
        from PySide6.QtGui import QImageReader, QPixmap
        reader = QImageReader(path)
        img = reader.read()
        if img.isNull():
            return
        pm = QPixmap.fromImage(img)
        # Fit into the available area, keeping aspect (mockup shows letterbox).
        area = self.photo.rect()
        if area.width() and area.height():
            pm = pm.scaled(area.size(), Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
        self.photo.setPixmap(pm)

    def _flash(self) -> None:
        self.setStyleSheet(
            f"LastCapturePanel{{border:3px solid {self.accent};}}")
        self._flash_active = True
        self._flash_timer.start(600)

    def _end_flash(self) -> None:
        self.setStyleSheet("")
        self._flash_active = False

    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.fillRect(self.rect(), styles.PANEL)
        if self._flash_active:
            p.setPen(QPen(QColor(self.accent), 3))
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.end()


class RaceScreen(QWidget):
    """Clock + count + last-capture panel (left) + crossing log (right)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t0 = 0.0
        self._running = False
        self.accent = styles.RED

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- left column ---
        left = QWidget()
        left.setStyleSheet(f"border-right:1px solid {styles.PANEL_BORDER};")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(40, 34, 40, 22)
        header.setAlignment(Qt.AlignBottom)
        self.clock = QLabel("00:00.000")
        self.clock.setProperty("mono", True)
        self.clock.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:186px; font-weight:600;"
            f" color:{styles.TEXT_PRIMARY}; letter-spacing:-0.03em;"
            " line-height:.82;")
        header.addWidget(self.clock)
        header.addSpacing(36)

        count_col = QVBoxLayout()
        count_col.setSpacing(6)
        count_cap = QLabel("Crossings")
        count_cap.setStyleSheet(
            f"color:{styles.TEXT_DIM}; font-size:14px; letter-spacing:.12em;"
            " text-transform:uppercase;")
        self.count_lbl = QLabel("0")
        self.count_lbl.setProperty("mono", True)
        self.count_lbl.setStyleSheet(
            f"font-family:'{styles.FONT_MONO}'; font-size:96px; font-weight:600;"
            f" color:{styles.TEXT_PRIMARY}; line-height:.85;")
        count_col.addWidget(count_cap)
        count_col.addWidget(self.count_lbl)
        header.addLayout(count_col)
        header.addStretch(1)
        lv.addLayout(header)

        wrap = QWidget()
        wrap.setStyleSheet(f"background:{styles.PANEL};")
        wlay = QVBoxLayout(wrap)
        wlay.setContentsMargins(40, 0, 40, 28)
        self.last_capture = LastCapturePanel(self.accent)
        wlay.addWidget(self.last_capture, 1)
        lv.addWidget(wrap, 1)

        root.addWidget(left, 1)

        # --- right column ---
        self.log = CrossingLog()
        self.log.setFixedWidth(660)
        root.addWidget(self.log)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)

    def begin(self, t0: float, accent: str = styles.RED) -> None:
        self._t0 = t0
        self._running = True
        self.accent = accent
        self.last_capture.set_accent(accent)
        self._clock_timer.start(50)

    def end(self) -> None:
        self._running = False
        self._clock_timer.stop()

    def _tick(self) -> None:
        import time
        self.set_clock(time.monotonic() - self._t0)

    def set_clock(self, secs: float) -> None:
        whole, frac = fmt_clock(secs).split(".")
        self.clock.setText(
            f"{whole}<span style='color:{styles.TEXT_DIM}'>{frac}</span>")
        self.clock.setTextFormat(Qt.RichText)

    def set_count(self, n: int) -> None:
        self.count_lbl.setText(str(n))

    def add_capture(self, data: dict) -> None:
        self.log.add(data)
        self.set_count(len(self.log._rows))
        self.last_capture.set_capture(data["sequence"], data["elapsed_s"])

    def update_image(self, sequence: int, path: str) -> None:
        self.log.update_thumb(sequence, path)
        if self.last_capture.heading.text().startswith(f"#{sequence:03d}"):
            self.last_capture.set_photo(path)

    def clear_captures(self) -> None:
        self.log.clear()
        self.set_count(0)
