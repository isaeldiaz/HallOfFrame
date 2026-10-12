"""Fixed race-span envelope: bars, press mark, crossing ticks, playhead (sheet 12b)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..audio import envelope
from . import styles


class AudioView(QWidget):
    """Fixed race-span envelope: a press mark, crossing ticks, and a playhead.

    The waveform is loaded once per race (the span from the first to the last
    crossing) and never changes while crossing the list; only the red press mark
    moves. The playhead is positioned in span seconds.
    """

    BINS = 200
    # Seconds into the displayed span where the operator clicked; review replays from there.
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setFocusPolicy(Qt.NoFocus)
        self._env: list[float] = []
        self._dur_s = 0.0
        self._press_s = None
        self._others: list[float] = []
        self._play_t0 = None
        self._play_offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update)

    def set_waveform(self, pcm: bytes, dur_s: float) -> None:
        """Store envelope(pcm, BINS) as the fixed span; clears marks, stops the playhead."""
        self._env = envelope(pcm, self.BINS)
        self._dur_s = dur_s
        self._press_s = None
        self._others = []
        self.stop_playhead()
        self.update()

    def set_marks(self, press_s: float | None, others_s: list[float]) -> None:
        """Set the selected press mark and the other-crossing ticks (span seconds)."""
        self._press_s = press_s
        self._others = list(others_s)
        self.update()

    def set_press(self, press_s: float | None) -> None:
        """Move only the red press mark (span seconds); the waveform stays put."""
        self._press_s = press_s
        self.update()

    def set_play_start(self, offset_s: float) -> None:
        """(Re)start the wall-clock playhead at ``offset_s`` seconds into the span."""
        self._play_offset = max(0.0, offset_s)
        self._play_t0 = time.monotonic()
        self._timer.start()
        self.update()

    def clear(self) -> None:
        """Empty state ("no waveform" caption); stops the playhead."""
        self._env = []
        self._dur_s = 0.0
        self._press_s = None
        self._others = []
        self._play_offset = 0.0
        self.stop_playhead()
        self.update()

    def stop_playhead(self) -> None:
        self._timer.stop()
        self._play_t0 = None

    def playhead_s(self) -> float | None:
        """Seconds into the span, or None when stopped / past dur_s (then the timer is stopped too)."""
        if self._play_t0 is None:
            return None
        p = self._play_offset + (time.monotonic() - self._play_t0)
        if p > self._dur_s:
            self.stop_playhead()
            return None
        return p

    def mousePressEvent(self, event):  # noqa: N802
        """Click a point on the envelope to replay the recording from there."""
        if event.button() == Qt.LeftButton and self._dur_s > 0:
            w = max(1, self.width() - 1)
            x = min(max(0.0, event.position().x()), float(w))
            self.seek_requested.emit(x / w * self._dur_s)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(styles.PANEL))
        painter.setPen(QColor(styles.PANEL_BORDER))
        painter.drawRect(0, 0, w - 1, h - 1)
        if not self._env:
            painter.setPen(QColor(styles.TEXT_FAINT))
            font = painter.font()
            font.setPixelSize(14)
            painter.setFont(font)
            painter.drawText(0, 0, w, h, Qt.AlignCenter, "no waveform")
            return
        # aplay gives no position feedback, so the playhead is wall-clock from
        # set_play_start — approximate on purpose
        painter.setPen(Qt.NoPen)
        for i in range(self.BINS):
            x0 = i * w // self.BINS
            x1 = (i + 1) * w // self.BINS
            bh = round(self._env[i] * (h - 8))
            painter.fillRect(x0, h - bh, x1 - x0, bh,
                             QColor(styles.TEXT_SECONDARY))
        if self._dur_s > 0:
            for t in self._others:
                x = round(t / self._dur_s * (w - 1))
                painter.fillRect(x, 0, 1, h, QColor(styles.AMBER))
            if self._press_s is not None:
                x = round(self._press_s / self._dur_s * (w - 1))
                painter.fillRect(x, 0, 2, h, QColor(styles.RED))
            play = self.playhead_s()
            if play is not None:
                x = round(play / self._dur_s * (w - 1))
                painter.fillRect(x, 0, 1, h, QColor(styles.BLUE))
