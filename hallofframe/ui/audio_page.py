"""Audio calibration page (sheet 25): PCM pick, live mixer, filters, preview.

Nothing here runs on the trigger thread or opens SQLite; the meter runs on its
own thread and reaches widgets only through Qt signals. Accept writes
``settings.json`` overrides for the ``[voice]`` section.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QScrollArea, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import audio
from .audio_view import AudioView

_LOOP_BLOCKS = 30   # 30 x 100 ms = 3 s of loopback
_FULL_SCALE = 327.67


class AudioPage(QWidget):
    """Third calibration phase: choose and audition the booth audio chain."""

    level = Signal(float)          # worker → GUI (LevelMeter.on_level)
    block = Signal(bytes)          # worker → GUI (LevelMeter.on_block)
    meter_error = Signal(str)
    notify = Signal(str)
    saved = Signal(dict)           # the dict written to settings.json

    def __init__(self, config, *, list_pcms=audio.list_pcms,
                 mixer_controls=audio.mixer_controls, set_mixer=audio.set_mixer,
                 meter_factory=None, source_factory=audio.ArecordSource,
                 play=audio.play_clip, extra_inputs: list[tuple[str, str]] = (),
                 parent=None):
        super().__init__(parent)
        self.config = config
        self._list_pcms = list_pcms
        self._mixer_controls = mixer_controls
        self._set_mixer = set_mixer
        self._meter_factory = meter_factory if meter_factory is not None else audio.LevelMeter
        self._source_factory = source_factory
        self._play_fn = play
        self._extra_inputs = list(extra_inputs)
        self.voice = config.section("voice")
        self.meter = None
        self.player = None
        self.sliders: dict[str, QSlider] = {}
        self._mixer_timers: dict[str, QTimer] = {}
        self._collecting = False
        self._loop_blocks: list[bytes] = []
        self._loop_started_meter = False

        self.level.connect(self._on_level)
        self.meter_error.connect(self._on_meter_error)
        self.block.connect(self._on_block)

        lay = QVBoxLayout(self)

        devices = QGroupBox("Devices")
        dlay = QFormLayout(devices)
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        dlay.addRow("Input (arecord)", self.input_combo)
        dlay.addRow("Output (aplay)", self.output_combo)
        card_row = QHBoxLayout()
        self.card_edit = QLineEdit(str(self.voice["mixer_card"]))
        self.card_edit.setMaximumWidth(80)
        card_row.addWidget(self.card_edit)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        card_row.addWidget(self.refresh_btn)
        card_row.addStretch(1)
        dlay.addRow("Mixer card", card_row)
        lay.addWidget(devices)

        self.mixer_box = QGroupBox("Mixer")
        self.mixer_scroll = QScrollArea()
        self.mixer_scroll.setWidgetResizable(True)
        # A laptop HDA card exposes ~10 controls; cap the list so the stacked
        # page never grows the dialog past the screen.
        self.mixer_scroll.setMaximumHeight(220)
        self.mixer_host = QWidget()
        self.mixer_layout = QVBoxLayout(self.mixer_host)
        self.mixer_scroll.setWidget(self.mixer_host)
        mixer_outer = QVBoxLayout(self.mixer_box)
        mixer_outer.addWidget(self.mixer_scroll)
        lay.addWidget(self.mixer_box)

        filters = QGroupBox("Playback filters")
        flay = QFormLayout(filters)
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(-20.0, 20.0)
        self.gain_spin.setSingleStep(1.0)
        self.gain_spin.setValue(float(self.voice["gain_db"]))
        flay.addRow("Gain (dB)", self.gain_spin)
        self.highpass_spin = QSpinBox()
        self.highpass_spin.setRange(0, 500)
        self.highpass_spin.setSingleStep(10)
        self.highpass_spin.setValue(int(self.voice["highpass_hz"]))
        flay.addRow("High-pass (Hz)", self.highpass_spin)
        self.mute_spin = QSpinBox()
        self.mute_spin.setRange(0, 500)
        self.mute_spin.setSingleStep(10)
        self.mute_spin.setValue(int(self.voice["press_mute_ms"]))
        flay.addRow("Press mute (ms)", self.mute_spin)
        lay.addWidget(filters)

        preview = QGroupBox("Preview")
        playlay = QVBoxLayout(preview)
        self.meter_bar = QProgressBar()
        self.meter_bar.setRange(0, 100)
        self.meter_bar.setTextVisible(False)
        playlay.addWidget(self.meter_bar)
        buttons = QHBoxLayout()
        self.listen_btn = QPushButton("Listen")
        self.listen_btn.setCheckable(True)
        self.listen_btn.toggled.connect(self._on_listen)
        buttons.addWidget(self.listen_btn)
        self.loop_btn = QPushButton("Record 3 s and play back")
        self.loop_btn.clicked.connect(self._loopback)
        buttons.addWidget(self.loop_btn)
        self.tone_btn = QPushButton("Test tone")
        self.tone_btn.clicked.connect(self._tone)
        buttons.addWidget(self.tone_btn)
        playlay.addLayout(buttons)
        self.audio_view = AudioView()
        playlay.addWidget(self.audio_view)
        lay.addWidget(preview)

        self.accept_btn = QPushButton("Accept — write settings.json")
        self.accept_btn.clicked.connect(self._accept)
        lay.addWidget(self.accept_btn)

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(100)
        self._play_timer.timeout.connect(self._poll_player)

        self.refresh()

    # --- enumeration ------------------------------------------------------
    def refresh(self) -> None:
        """Re-list PCMs and mixer controls."""
        # Re-read the section so a value accepted since construction survives a refresh.
        self.voice = self.config.section("voice")
        self._populate_combo(self.input_combo,
                             self._list_pcms("input") + self._extra_inputs,
                             str(self.voice["input"]))
        self._populate_combo(self.output_combo, self._list_pcms("output"),
                             str(self.voice["output"]))
        self._rebuild_mixer()

    @staticmethod
    def _populate_combo(combo: QComboBox, entries, current: str) -> None:
        combo.clear()
        names = [name for name, _ in entries]
        if current and current not in names:
            combo.addItem(f"{current} — (current)", current)
        for name, desc in entries:
            combo.addItem(f"{name} — {desc}", name)
        idx = combo.findData(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _rebuild_mixer(self) -> None:
        # Stop pending debounces first: a timer that fires after its slider is
        # deleted would call setValue() on a dead C++ object.
        for timer in self._mixer_timers.values():
            timer.stop()
            timer.deleteLater()
        while self.mixer_layout.count():
            item = self.mixer_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.sliders = {}
        self._mixer_timers = {}
        controls = self._mixer_controls(self.card_edit.text().strip())
        if not controls:
            self.mixer_layout.addWidget(QLabel("No mixer controls (is amixer installed?)"))
            return
        for control in controls:
            if control.get("percent") is None:
                continue
            name = control["name"]
            role = "capture" if control["capture"] else "playback"
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{name} ({role})"))
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(control["percent"]))
            self.sliders[name] = slider
            # comment: debounced so a drag does not spawn an amixer per pixel
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(150)
            timer.timeout.connect(
                lambda n=name, s=slider: self._apply_mixer(n, s.value()))
            self._mixer_timers[name] = timer
            slider.valueChanged.connect(lambda _v, t=timer: t.start())
            row.addWidget(slider, 1)
            host = QWidget()
            host.setLayout(row)
            self.mixer_layout.addWidget(host)

    def _apply_mixer(self, name: str, value: int) -> None:
        message = self._set_mixer(self.card_edit.text().strip(), name, f"{value}%")
        if message:
            self.notify.emit(f"audio mixer: {message}")

    # --- properties -------------------------------------------------------
    @property
    def input_name(self) -> str:
        return self.input_combo.currentData()

    @property
    def output_name(self) -> str:
        return self.output_combo.currentData()

    # --- meter ------------------------------------------------------------
    def _on_listen(self, checked: bool) -> None:
        if checked:
            self._start_meter()
        else:
            self._stop_meter()

    def _start_meter(self) -> None:
        # An already-running meter must not be orphaned (also covers Listen
        # pressed during a loopback).
        if self.meter is not None and self.meter.running:
            self.meter.stop()
        # Capture the device in the GUI thread: the factory runs on the meter
        # thread and must not read a QComboBox there.
        device = self.input_name
        self.meter = self._meter_factory(
            lambda: self._source_factory(device), self.level.emit,
            on_error=self.meter_error.emit, on_block=self.block.emit)
        self.meter.start()

    def _stop_meter(self) -> None:
        if self.meter is not None:
            self.meter.stop()
        # Unchecking Listen during a loopback stops the meter, so no more blocks
        # arrive; abort the collection or the Record button stays "Recording…".
        if self._collecting:
            self._abort_loopback()

    def _on_level(self, rms: float) -> None:
        self.meter_bar.setValue(min(100, int(rms / _FULL_SCALE)))

    def _on_meter_error(self, message: str) -> None:
        if self.listen_btn.isChecked():
            self.listen_btn.setChecked(False)
        self._stop_meter()
        if self._collecting:
            self._abort_loopback()
        self.notify.emit(message)

    def _abort_loopback(self) -> None:
        """A failed source must not leave the loopback button stuck recording."""
        self._collecting = False
        self._loop_blocks = []
        self.loop_btn.setEnabled(True)
        self.loop_btn.setText("Record 3 s and play back")

    # --- loopback / tone --------------------------------------------------
    def _loopback(self) -> None:
        self._loop_blocks = []
        self._collecting = True
        self._loop_started_meter = not (self.meter is not None and self.meter.running)
        if self._loop_started_meter:
            self._start_meter()
        self.loop_btn.setEnabled(False)
        self.loop_btn.setText("Recording… 3 s")

    def _on_block(self, block: bytes) -> None:
        if not self._collecting:
            return
        self._loop_blocks.append(block)
        if len(self._loop_blocks) >= _LOOP_BLOCKS:
            self._finish_loopback()

    def _finish_loopback(self) -> None:
        self._collecting = False
        if self._loop_started_meter:
            self._stop_meter()
        pcm = audio.process_clip(b"".join(self._loop_blocks),
                                 gain_db=float(self.gain_spin.value()),
                                 highpass_hz=float(self.highpass_spin.value()))
        self.audio_view.set_waveform(pcm, 3.0)
        self.audio_view.set_marks(0.0, [])
        self._play(pcm)
        self.loop_btn.setEnabled(True)
        self.loop_btn.setText("Record 3 s and play back")

    def _tone(self) -> None:
        self._play(audio.tone_pcm(1000.0, 1.0))

    def _play(self, pcm: bytes) -> None:
        self._stop_player()
        self.player = self._play_fn(pcm, device=self.output_name)
        if self.player is None:
            self.notify.emit("aplay not available — install alsa-utils")
            return
        self._play_timer.start()

    def _poll_player(self) -> None:
        proc = self.player
        if proc is None:
            self._play_timer.stop()
            return
        if proc.poll() is None:
            return
        self._play_timer.stop()
        self.player = None
        error = audio.player_error(proc)
        self._close_pipes(proc)
        if error:
            self.notify.emit(error)

    def _stop_player(self) -> None:
        self._play_timer.stop()
        proc = self.player
        self.player = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        self._close_pipes(proc)

    @staticmethod
    def _close_pipes(proc) -> None:
        for pipe in (proc.stdin, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass

    # --- persist / teardown ----------------------------------------------
    def values(self) -> dict:
        """The dict Accept writes."""
        return {
            "input": self.input_name,
            "output": self.output_name,
            "mixer_card": self.card_edit.text().strip(),
            "mixer": [f"{name} {slider.value()}%"
                      for name, slider in self.sliders.items()],
            "gain_db": float(self.gain_spin.value()),
            "highpass_hz": float(self.highpass_spin.value()),
            "press_mute_ms": int(self.mute_spin.value()),
        }

    def _accept(self) -> None:
        values = self.values()
        self.config.write_overrides("voice", values)
        self.saved.emit(values)
        self.notify.emit("Audio settings written to settings.json")

    def stop(self) -> None:
        """Stop meter + player; idempotent (dialog close)."""
        self._stop_meter()
        self._stop_player()

    def cancel(self) -> None:
        """Skipping the phase stops the meter/player."""
        self.stop()
