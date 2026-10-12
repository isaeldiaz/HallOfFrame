"""Per-race audio recording through the controller (sheet 11, §13.3.4)."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from fakes import FakeClock, FakeScheduler
from hallofframe.audio import AudioRecorder
from hallofframe.controller import CaptureController
from hallofframe.main import build_core
from test_audio import FakeSource


@pytest.fixture
def audio_env(data_root, config, storage, buffer):
    env = SimpleNamespace()
    env.data_root = data_root
    env.config = config(timing={"image_mode": "off"})
    env.storage = storage
    env.buffer = buffer
    env.scheduler = FakeScheduler()
    env.controller = CaptureController(env.config, env.storage, env.buffer,
                                       scheduler=env.scheduler)
    env.events = []
    env.controller.events = lambda k, p: env.events.append((k, p))
    yield env
    env.controller.stop()


def attach(env, blocks):
    rec = AudioRecorder(lambda: FakeSource(blocks), clock=FakeClock(1000.3))
    env.controller.attach_recorder(rec)
    return rec


def test_start_race_records_and_writes_offset(audio_env):
    env = audio_env
    rec = attach(env, [b"\x01\x00" * 1600] * 10)
    rid = env.controller.start_race(1000.0, name="Race")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        env.controller._queue.join()
        if env.storage.get_race(rid)["audio_path"] is not None:
            break
        time.sleep(0.01)
    row = env.storage.get_race(rid)
    assert row["audio_path"] == "races/0001_Race/audio.wav"
    assert abs(row["audio_t0_offset_s"] - 0.2) < 1e-6
    env.controller.end_race()
    assert (env.data_root / row["audio_path"]).exists()
    assert rec.recording is False


def test_audio_failure_is_a_warning(audio_env):
    env = audio_env

    class Failing:
        def open(self):
            raise FileNotFoundError("arecord")

        def read(self):
            return b""

        def close(self):
            pass

    rec = AudioRecorder(Failing, clock=FakeClock(1000.3))
    env.controller.attach_recorder(rec)
    rid = env.controller.start_race(1000.0, name="Race")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(k == "warning" and "audio" in p.get("message", "")
               for k, p in env.events):
            break
        time.sleep(0.01)
    warnings = [p for k, p in env.events if k == "warning"]
    assert len(warnings) == 1
    assert "audio" in warnings[0]["message"]
    assert env.storage.get_race(rid)["audio_path"] is None
    env.controller.record_crossing(1001.0)
    env.controller._queue.join()
    assert len(env.storage.captures_for_race(rid)) == 1


def test_no_recorder_no_op(audio_env):
    env = audio_env
    rid = env.controller.start_race(1000.0, name="Race")
    env.controller.record_crossing(1001.0)
    env.controller._queue.join()
    env.controller.end_race()
    assert env.storage.get_race(rid)["audio_path"] is None


def test_resume_does_not_record(audio_env):
    env = audio_env
    rid = env.storage.create_race(
        name="R", t0_monotonic=1000.0, t0_wall=time.time(),
        start_mode="direct", radio_delay_ms=0.0, delta_used=0.0,
        viewing_mode="screen")
    rec = attach(env, [b"\x01\x00" * 1600] * 3)
    env.controller.resume_race(rid)
    assert rec.recording is False


def test_build_core_attaches_recorder(data_root, config):
    cfg = config(transport={"enabled": False}, voice={"enabled": True})
    core = build_core(cfg)
    try:
        assert core["controller"].recorder is not None
        assert core["controller"].recorder.recording is False
    finally:
        core["controller"].stop()
        core["storage"].close()

    core2 = build_core(config(transport={"enabled": False}))
    try:
        assert core2["controller"].recorder is None
    finally:
        core2["controller"].stop()
        core2["storage"].close()
