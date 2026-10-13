"""AudioPage tests (sheet 25): injected enumeration/playback + FakeMeter."""
from __future__ import annotations

import array
import json
from unittest import mock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from hallofframe.controller import CaptureController
from hallofframe.main import build_core
from hallofframe.ui.audio_page import AudioPage
from hallofframe.ui.calibration_dialog import CalibrationDialog
from hallofframe.ui.review_screen import ReviewScreen

pytestmark = pytest.mark.qt

MIXER = [
    {"name": "Master", "capture": False, "playback": True, "percent": 100, "switch": True},
    {"name": "Capture", "capture": True, "playback": False, "percent": 60, "switch": True},
    {"name": "Mic Boost", "capture": False, "playback": False, "percent": 0, "switch": None},
]


class FakeMeter:
    """Records LevelMeter callbacks; ``feed`` invokes them from the test thread."""

    def __init__(self, source_factory, on_level, on_error=None, on_block=None):
        self.source_factory = source_factory
        self._on_level = on_level
        self._on_error = on_error
        self._on_block = on_block
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def feed(self, rms, block):
        self._on_level(rms)
        if self._on_block is not None:
            self._on_block(block)


class FakeProc:
    def __init__(self):
        self.returncode = 0
        self.stdin = None
        self.stderr = None

    def poll(self):
        return 0


class FakePlay:
    """Records ``play_clip`` calls; returns a process that reports exit 0."""

    def __init__(self):
        self.calls = []
        self.pcms = []

    def __call__(self, pcm, *, device="default"):
        self.calls.append((len(pcm), device))
        self.pcms.append(pcm)
        return FakeProc()


def fake_list_pcms(kind):
    return {
        "input": [("default", "System default"),
                  ("plughw:CARD=Headset,DEV=0", "Logitech")],
        "output": [("default", "System default"),
                   ("plughw:CARD=PCH,DEV=0", "HDA Intel PCH")],
    }[kind]


def fake_mixer_controls(card, **kw):
    return list(MIXER)


def _make(config, *, extra_inputs=(), set_mixer=None, play=None, **voice):
    base = {"enabled": True, "input": "plughw:CARD=Headset,DEV=0",
            "output": "default", "mixer_card": "0", "gain_db": 0.0,
            "highpass_hz": 0.0, "press_mute_ms": 0}
    base.update(voice)
    cfg = config(voice=base)
    if play is None:
        play = FakePlay()
    page = AudioPage(
        cfg, list_pcms=fake_list_pcms, mixer_controls=fake_mixer_controls,
        set_mixer=set_mixer or (lambda *a, **k: None), meter_factory=FakeMeter,
        source_factory=lambda device: None, play=play, extra_inputs=extra_inputs)
    return page, cfg, play


def test_populates_devices_and_mixer(qapp, config):
    page, _, _ = _make(config)
    assert page.input_combo.count() == 2
    assert page.input_combo.currentData() == "plughw:CARD=Headset,DEV=0"
    assert page.output_combo.currentData() == "default"
    assert set(page.sliders) == {"Master", "Capture", "Mic Boost"}
    assert page.sliders["Capture"].value() == 60


def test_unknown_current_input_added(qapp, config):
    page, _, _ = _make(config, input="hw:7,0")
    assert page.input_combo.itemData(0) == "hw:7,0"
    assert page.input_combo.currentData() == "hw:7,0"
    assert page.input_combo.itemText(0).endswith("(current)")


def test_extra_inputs_listed(qapp, config):
    page, _, _ = _make(
        config, extra_inputs=[("http://127.0.0.1:8081/audio.wav", "iPhone microphone")])
    assert page.input_combo.itemData(page.input_combo.count() - 1) == \
        "http://127.0.0.1:8081/audio.wav"


def test_slider_applies_debounced(qapp, config):
    calls = []

    def set_mixer(card, control, value, **kw):
        calls.append((card, control, value))
        return None

    page, _, _ = _make(config, set_mixer=set_mixer)
    page.sliders["Capture"].setValue(70)
    qapp.processEvents()
    QTest.qWait(250)
    assert calls == [("0", "Capture", "70%")]


def test_slider_error_toasts(qapp, config):
    notes = []

    def set_mixer(card, control, value, **kw):
        return f"{control} {value}: boom"

    page, _, _ = _make(config, set_mixer=set_mixer)
    page.notify.connect(notes.append)
    page.sliders["Capture"].setValue(70)
    QTest.qWait(250)
    assert notes == ["audio mixer: Capture 70%: boom"]


def test_listen_starts_meter_and_updates_bar(qapp, config):
    page, _, _ = _make(config)
    page.listen_btn.click()
    assert page.meter.running
    page.meter.feed(16383.5, b"")
    qapp.processEvents()
    assert page.meter_bar.value() == 50
    page.listen_btn.click()
    assert page.meter.running is False


def test_meter_error_unchecks(qapp, config):
    page, _, _ = _make(config)
    notes = []
    page.notify.connect(notes.append)
    page.listen_btn.click()
    page.meter._on_error("audio: boom")
    qapp.processEvents()
    assert page.listen_btn.isChecked() is False
    assert notes == ["audio: boom"]


def test_loopback_records_then_plays_filtered(qapp, config):
    page, _, play = _make(config)
    page.gain_spin.setValue(6.0206)
    page.loop_btn.click()
    assert page.meter.running
    block = b"\xe8\x03" * 1600
    for _ in range(30):
        page.meter.feed(1000.0, block)
    qapp.processEvents()
    assert len(play.calls) == 1
    length, device = play.calls[0]
    assert length == 96000
    assert device == "default"
    samples = array.array("h")
    samples.frombytes(play.pcms[0])
    assert all(x == 2000 for x in samples)
    assert page.meter.running is False
    assert page.loop_btn.isEnabled()


def test_tone_plays_on_output(qapp, config):
    page, _, play = _make(config)
    page.output_combo.setCurrentIndex(1)
    page.tone_btn.click()
    assert play.calls == [(32000, "plughw:CARD=PCH,DEV=0")]


def test_accept_writes_settings(qapp, config):
    page, cfg, _ = _make(config)
    saved = []
    page.saved.connect(saved.append)
    page.sliders["Capture"].setValue(70)
    page.highpass_spin.setValue(150)
    page.mute_spin.setValue(100)
    page.output_combo.setCurrentIndex(1)
    page.accept_btn.click()
    assert json.loads(cfg.settings_path.read_text())["voice"] == {
        "input": "plughw:CARD=Headset,DEV=0",
        "output": "plughw:CARD=PCH,DEV=0",
        "mixer_card": "0",
        "mixer": ["Master 100%", "Capture 70%", "Mic Boost 0%"],
        "gain_db": 0.0,
        "highpass_hz": 150.0,
        "press_mute_ms": 100,
    }
    assert cfg.section("voice")["output"] == "plughw:CARD=PCH,DEV=0"
    assert len(saved) == 1


def test_dialog_has_audio_tab_and_stops_meter(qapp, buffer, config):
    cfg = config(voice={"enabled": True})
    with mock.patch("hallofframe.ui.audio_page.audio.LevelMeter", FakeMeter):
        dlg = CalibrationDialog(buffer, cfg.data_root, cfg, evdev=False)
        assert [name for name, _ in dlg._pages] == ["Latency", "Reaction", "Audio"]
        assert dlg.phase_label.text() == "Phase 1 of 3 — Latency"
        dlg.audio.listen_btn.click()
        assert dlg.audio.meter.running
        dlg.close()
    assert dlg.audio.meter.running is False


def test_slider_debounce_collapses_rapid_changes(qapp, config):
    calls = []

    def set_mixer(card, control, value, **kw):
        calls.append((card, control, value))
        return None

    page, _, _ = _make(config, set_mixer=set_mixer)
    slider = page.sliders["Capture"]
    for value in range(61, 71):
        slider.setValue(value)
    assert calls == []
    QTest.qWait(250)
    assert calls == [("0", "Capture", "70%")]


def test_loopback_meter_error_recovers(qapp, config):
    page, _, _ = _make(config)
    page.loop_btn.click()
    assert page.loop_btn.isEnabled() is False
    page.meter._on_error("audio: boom")
    qapp.processEvents()
    assert page._collecting is False
    assert page.loop_btn.isEnabled()
    assert page.loop_btn.text() == "Record 3 s and play back"


def test_unlisten_during_loopback_recovers(qapp, config):
    page, _, _ = _make(config)
    page.listen_btn.click()
    page.loop_btn.click()
    assert page._collecting is True
    assert page.loop_btn.isEnabled() is False
    block = b"\x00\x00" * 100
    for _ in range(5):
        page.meter.feed(100.0, block)
    page.listen_btn.setChecked(False)
    qapp.processEvents()
    assert page._collecting is False
    assert page.loop_btn.isEnabled()
    assert page.loop_btn.text() == "Record 3 s and play back"


def test_refresh_keeps_accepted_output(qapp, config):
    page, cfg, _ = _make(config)
    page.output_combo.setCurrentIndex(1)
    page.accept_btn.click()
    accepted = cfg.section("voice")["output"]
    page.refresh()
    assert page.output_combo.currentData() == accepted


def test_dialog_finish_stops_meter(qapp, buffer, config):
    cfg = config(voice={"enabled": True})
    with mock.patch("hallofframe.ui.audio_page.audio.LevelMeter", FakeMeter):
        dlg = CalibrationDialog(buffer, cfg.data_root, cfg, evdev=False)
        dlg.skip_btn.click()  # latency -> reaction
        dlg.skip_btn.click()  # reaction -> audio
        assert dlg._index == 2
        dlg.audio.listen_btn.click()
        assert dlg.audio.meter.running
        dlg.finish_btn.click()
    assert dlg.result() == QDialog.Accepted
    assert dlg.audio.meter.running is False


def test_dialog_reject_stops_meter(qapp, buffer, config):
    cfg = config(voice={"enabled": True})
    with mock.patch("hallofframe.ui.audio_page.audio.LevelMeter", FakeMeter):
        dlg = CalibrationDialog(buffer, cfg.data_root, cfg, evdev=False)
        dlg.skip_btn.click()
        dlg.skip_btn.click()
        dlg.audio.listen_btn.click()
        assert dlg.audio.meter.running
        dlg.reject()
    assert dlg.audio.meter.running is False


def test_skip_audio_phase_stops_meter(qapp, buffer, config):
    cfg = config(voice={"enabled": True})
    with mock.patch("hallofframe.ui.audio_page.audio.LevelMeter", FakeMeter):
        dlg = CalibrationDialog(buffer, cfg.data_root, cfg, evdev=False)
        dlg.show()
        qapp.processEvents()
        dlg.skip_btn.click()  # latency -> reaction
        dlg.skip_btn.click()  # reaction -> audio
        assert dlg._index == 2
        dlg.audio.listen_btn.click()
        assert dlg.audio.meter.running
        dlg.skip_btn.click()  # audio -> finish
    assert dlg.result() == QDialog.Accepted
    assert dlg.audio.meter.running is False


def test_review_voice_is_live(qapp, data_root, config, storage, buffer):
    cfg = config(voice={"enabled": True, "gain_db": 0.0})
    ctl = CaptureController(cfg, storage, buffer)
    try:
        screen = ReviewScreen(ctl, data_root, race_id=1)
        cfg.write_overrides("voice", {"gain_db": 12.0})
        assert screen._voice["gain_db"] == 12.0
    finally:
        ctl.stop()


def test_build_core_source_reads_live_config(data_root, config):
    cfg = config(transport={"enabled": False},
                 voice={"enabled": True, "input": "a"})
    with mock.patch("hallofframe.audio.ArecordSource") as ar:
        core = build_core(cfg)
        try:
            cfg.write_overrides("voice", {"input": "b"})
            core["controller"].recorder._source_factory()
        finally:
            core["controller"].stop()
            core["storage"].close()
    ar.assert_called_with("b")
