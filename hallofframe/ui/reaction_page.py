"""Reaction-time page (spec §5.5.1): visual (finish line) and sound (gun) trials.

The Qt-free measurement core lives in :mod:`hallofframe.calibration`
(``ReactionRun``); this widget only drives the trial loop. Clock, scheduler and
the stimulus/beep factories are injected so the whole loop runs headless in
tests (no display, no aplay).
"""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QLabel, QPushButton, QRadioButton, QVBoxLayout,
                               QWidget)

from .. import calibration as cal
from ..audio import BeepPlayer


class _QtScheduled:
    """Handle from the default scheduler; ``cancel()`` stops its QTimer."""

    def __init__(self, delay_s: float, callback: Callable[[], None]):
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(callback)
        self._timer.start(max(0, int(round(delay_s * 1000))))

    def cancel(self) -> None:
        self._timer.stop()


class StimulusWindow(QWidget):
    """Full-screen black window; ``show_stimulus()`` turns it white with "GO"."""

    qt_press = Signal(float)  # Space/Return → time.monotonic() (Qt fallback only)
    aborted = Signal()        # Esc → ReactionPage.abort (the dialog is hidden)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.StrongFocus)
        self.label = QLabel("", self)
        self.label.setAlignment(Qt.AlignCenter)
        font = QFont("sans-serif", 180, QFont.Bold)
        self.label.setFont(font)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        self.blank()

    def _paint(self, bg: str, fg: str) -> None:
        self.setStyleSheet(f"background:{bg};")
        self.label.setStyleSheet(f"color:{fg}; background:{bg};")
        self.repaint()

    def present(self) -> None:
        """Full-screen, front, focused (same keep-on-top pattern as the counter)."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._keep_top = QTimer(self)
        self._keep_top.timeout.connect(self._top)
        self._keep_top.start(100)

    def _top(self) -> None:
        self.raise_()
        self.activateWindow()

    def blank(self) -> None:
        self.label.setText("wait…")
        self._paint("#000000", "#888888")

    def show_stimulus(self) -> float:
        self.label.setText("GO")
        self._paint("#ffffff", "#000000")
        return time.monotonic()

    def close(self) -> bool:  # noqa: N802
        if getattr(self, "_keep_top", None):
            self._keep_top.stop()
        return super().close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.qt_press.emit(time.monotonic())
        elif event.key() == Qt.Key_Escape:
            self.aborted.emit()
        else:
            super().keyPressEvent(event)


class ReactionPage(QWidget):
    """Visual/sound reaction measurement against the trigger button."""

    run_started = Signal()
    run_finished = Signal()
    saved = Signal(str, dict)  # (kind, stats) after update_reaction
    notify = Signal(str)

    def __init__(self, data_root, voice: dict, *, evdev: bool,
                 clock=time.monotonic, schedule: Callable | None = None,
                 run_factory=cal.ReactionRun, stimulus_factory=StimulusWindow,
                 beep_factory: Callable | None = None, parent=None):
        super().__init__(parent)
        self.data_root = data_root
        self.voice = voice or {}
        self.evdev = evdev
        self._schedule = schedule or _QtScheduled
        self._run_factory = run_factory
        self._stimulus_factory = stimulus_factory
        self._beep_factory = beep_factory or (
            lambda: BeepPlayer(str(self.voice.get("output", "default"))))
        self._clock = clock
        self.run: cal.ReactionRun | None = None
        self.stimulus = None
        self.beep: BeepPlayer | None = None
        self.stats: dict | None = None
        self._pending = None
        self._qt_connected = False
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        hint = QLabel(
            "Reaction test. Choose VISUAL (the screen flashes white — the "
            "finish-line reaction) or SOUND (a beep — the gun reaction).\n"
            "Press the trigger button as soon as you see the flash / hear the "
            "beep. Too early is a false start and the trial repeats; a miss "
            "repeats too. Esc aborts.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.kind_visual = QRadioButton("Visual — finish line")
        self.kind_audio = QRadioButton("Sound — gun start")
        self.kind_visual.setChecked(True)
        lay.addWidget(self.kind_visual)
        lay.addWidget(self.kind_audio)

        self.start_btn = QPushButton(f"Start {cal.REACTION_TRIALS} trials")
        self.start_btn.clicked.connect(self.start)
        lay.addWidget(self.start_btn)

        self.status = QLabel("")
        lay.addWidget(self.status)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)

        self.save_btn = QPushButton(f"Save to {cal.CALIBRATION_FILENAME}")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        lay.addWidget(self.save_btn)
        self.save_btn.setFocusPolicy(Qt.NoFocus)
        # Space must never "click" a focused button during a run.
        self.start_btn.setFocusPolicy(Qt.NoFocus)
        self.kind_visual.setFocusPolicy(Qt.NoFocus)
        self.kind_audio.setFocusPolicy(Qt.NoFocus)

    # --- trial loop -------------------------------------------------------
    def start(self) -> None:
        if self.run is not None:
            self.abort()
        kind = "visual" if self.kind_visual.isChecked() else "audio"
        self.run = self._run_factory(kind)
        self.stimulus = self._stimulus_factory()
        self.beep = self._beep_factory() if kind == "audio" else None
        self.stats = None
        self.result.setText("")
        self.save_btn.setEnabled(False)
        self.stimulus.present()
        # Esc reaches the stimulus window (top-level, focused), not the hidden
        # dialog, so abort has to come from the window's own key handler.
        self.stimulus.aborted.connect(self.abort)
        if not self.evdev:
            self.stimulus.qt_press.connect(self.on_press)
            self._qt_connected = True
        self.run_started.emit()
        self._schedule_trial()

    def _schedule_trial(self) -> None:
        if self.run is None:
            return
        if self.run.done:
            self._finish()
            return
        if self.beep is not None and not self.beep.prepare():
            self.notify.emit("aplay not available — install alsa-utils")
            self.abort()
            return
        self.stimulus.blank()
        self.status.setText(
            f"Trial {len(self.run.samples_ms) + 1}/{self.run.trials} — wait…")
        self._pending = self._schedule(self.run.next_delay(),
                                       self._show_stimulus)

    def _show_stimulus(self) -> None:
        if self.beep is not None:
            t = self.beep.fire()
            if t is None:
                self.notify.emit(self.beep.error() or "beep failed")
                self.abort()
                return
        else:
            t = self.stimulus.show_stimulus()
        self.run.stimulus(t)
        self._pending = self._schedule(cal.REACTION_LATE_MS / 1000.0,
                                       self._on_timeout)

    def on_press(self, t: float) -> None:
        if self.run is None or self.run.state == "idle":
            return
        self._cancel_pending()
        result = self.run.press(t)
        if result == "ok":
            self.status.setText(f"{self.run.samples_ms[-1]:.0f} ms")
        elif result == "early":
            self.status.setText("False start — again")
        elif result == "late":
            self.status.setText("Missed — again")
        self.stimulus.blank()
        self._pending = self._schedule(0.8, self._schedule_trial)

    def _on_timeout(self) -> None:
        if self.run is None:
            return
        self.run.timeout()
        self.status.setText("Missed — again")
        self.stimulus.blank()
        self._pending = self._schedule(0.8, self._schedule_trial)

    def _finish(self) -> None:
        self._close_stimulus()
        self.run_finished.emit()
        try:
            self.stats = self.run.stats()
        except ValueError as exc:
            self.notify.emit(str(exc))
            return
        self.result.setText(
            f"median {self.stats['median_ms']:.0f} ms · "
            f"IQR {self.stats['iqr_ms']:.0f} ms · n={self.stats['n']} · "
            f"false starts {self.run.false_starts} · misses {self.run.misses}")
        self.save_btn.setEnabled(True)

    # --- persistence / teardown ------------------------------------------
    def _save(self) -> None:
        if self.run is None or self.stats is None:
            return
        cal.update_reaction(self.data_root, self.run.kind, self.stats,
                            self.run.samples_ms, "evdev" if self.evdev else "qt")
        self.saved.emit(self.run.kind, self.stats)
        self.notify.emit(
            f"Saved {self.run.kind} reaction ({self.stats['median_ms']:.0f} ms).")

    def abort(self) -> None:
        had_run = self.run is not None or self.stimulus is not None
        self._cancel_pending()
        self._close_stimulus()
        self.run = None
        if had_run:
            self.run_finished.emit()

    def cancel(self) -> None:
        """Abort an in-flight run (used when skipping the phase)."""
        self.abort()

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    def _close_stimulus(self) -> None:
        if self.stimulus is not None:
            if self._qt_connected:
                try:
                    self.stimulus.qt_press.disconnect(self.on_press)
                except (RuntimeError, TypeError):
                    pass
                self._qt_connected = False
            self.stimulus.close()
            self.stimulus = None
        if self.beep is not None:
            self.beep.close()
            self.beep = None

    def closeEvent(self, event) -> None:  # noqa: N802
        self.abort()
        super().closeEvent(event)
