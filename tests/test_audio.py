"""Tests for the stdlib audio helpers (plan sheet 09)."""
from __future__ import annotations

import array
import io
import math
import queue
import sys
import threading
import time
import wave
from types import SimpleNamespace

import pytest

from fakes import FakeClock
from hallofframe.audio import (
    BLOCK_BYTES,
    CHANNELS,
    RATE,
    SAMPLE_WIDTH,
    ArecordSource,
    AudioRecorder,
    BeepPlayer,
    LevelMeter,
    WavSink,
    aplay_argv,
    apply_mixer,
    arecord_argv,
    clip_bounds,
    envelope,
    list_pcms,
    mixer_argv,
    mixer_controls,
    parse_mixer_contents,
    parse_mixer_entry,
    parse_pcm_list,
    play_clip,
    player_error,
    process_clip,
    read_clip,
    set_mixer,
    tone_pcm,
    wav_duration_s,
)


def write_tone_wav(path, total_s=20.0, burst=(12.0, 12.5)):
    """Write a mono 16 kHz WAV: silence with a 1 kHz burst inside ``burst``."""
    n = int(total_s * RATE)
    samples = array.array("h", [0] * n)
    lo = int(burst[0] * RATE)
    hi = int(burst[1] * RATE)
    for i in range(lo, hi):
        samples[i] = int(10000 * math.sin(2 * math.pi * 1000 * i / RATE))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of little-endian 16-bit PCM."""
    a = array.array("h")
    a.frombytes(pcm)
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a))


def test_clip_bounds_centred():
    assert clip_bounds(1014.25, 1000.0, 2.0, 3.0, 5.0, 20.0) == (9.25, 8.0)


@pytest.mark.parametrize("pos,expected", [
    (1.0, (0.0, 6.0)),
    (18.0, (15.0, 5.0)),
    (30.0, None),
    (-10.0, None),
])
def test_clip_bounds_clamps(pos, expected):
    assert clip_bounds(1000 + 2 + pos, 1000.0, 2.0, 3.0, 5.0, 20.0) == expected


def test_read_clip_contains_burst(tmp_path):
    p = tmp_path / "tone.wav"
    write_tone_wav(p)
    clip = read_clip(p, 9.25, 8.0)
    assert len(clip) == 8 * RATE * 2
    assert rms(clip[int(2.75 * RATE * 2):int(3.25 * RATE * 2)]) > 5000
    assert rms(clip[0:int(2.5 * RATE * 2)]) == 0


def test_wav_duration(tmp_path):
    p = tmp_path / "tone.wav"
    write_tone_wav(p)
    assert wav_duration_s(p) == 20.0


def test_wav_sink_header_valid_after_each_block(tmp_path):
    p = tmp_path / "sink.wav"
    sink = WavSink(p)
    block = b"\x01\x00" * 1600
    for _ in range(3):
        sink.write(block)
    assert abs(wav_duration_s(p) - 0.3) < 1e-6
    sink.close()
    assert abs(wav_duration_s(p) - 0.3) < 1e-6
    sink.close()


class FakeSource:
    """Returns queued blocks regardless of close; b"" only after the list drains."""

    def __init__(self, blocks):
        self._blocks = list(blocks)
        self._event = threading.Event()
        self.reads = 0

    def open(self):
        pass

    def read(self):
        if self._blocks:
            block = self._blocks.pop(0)
            self.reads += 1
            return block
        self._event.wait()
        return b""

    def close(self):
        self._event.set()


class GateSink:
    """Sink that blocks on a gate before recording each write."""

    def __init__(self, path):
        self.gate = threading.Event()
        self.frames = 0
        self.data = bytearray()

    def write(self, pcm):
        self.gate.wait()
        self.frames += len(pcm) // SAMPLE_WIDTH
        self.data.extend(pcm)

    def close(self):
        pass


class OSErrorSink:
    def __init__(self, path):
        pass

    def write(self, pcm):
        raise OSError("disk full")

    def close(self):
        pass


class FailingSource:
    def open(self):
        raise FileNotFoundError("arecord")

    def read(self):
        return b""

    def close(self):
        pass


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_records_blocks_and_reports_t_start(tmp_path):
    source = FakeSource([b"\x01\x00" * 1600] * 10)
    rec = AudioRecorder(lambda: source, clock=FakeClock(1000.1))
    ready = []
    rec.on_ready = lambda *args: ready.append(args)
    p = tmp_path / "audio.wav"
    rec.start(1, p)
    assert _wait_for(lambda: bool(ready))
    assert _wait_for(lambda: source.reads == 10)
    rec.stop()
    assert ready == [(1, p, 1000.0)]
    with wave.open(str(p), "rb") as w:
        assert w.getnframes() == 16000
        assert w.readframes(16000) == b"\x01\x00" * 16000


def test_capture_never_blocks_drops_become_silence(tmp_path):
    source = FakeSource([b"\x01\x00" * 1600] * 60)
    sinks = []
    rec = AudioRecorder(lambda: source, clock=FakeClock(0.0),
                        sink_factory=lambda path: sinks.append(GateSink(path)) or sinks[-1],
                        queue_blocks=5)
    p = tmp_path / "audio.wav"
    rec.start(1, p)
    assert _wait_for(lambda: source.reads == 60)
    assert rec.dropped_blocks >= 50
    sink = sinks[0]
    sink.gate.set()
    rec.stop()
    assert sink.frames == 60 * 1600
    # Dropped blocks become silence, and the silence is appended AFTER the data
    # blocks (the time axis survives) — a writer that puts zeros first, or fills
    # gaps with anything but zeros, fails this.
    data_blocks = sink.data.count(1) // 1600
    assert 0 < data_blocks <= 10
    assert sink.data[:data_blocks * BLOCK_BYTES] == b"\x01\x00" * (data_blocks * 1600)
    assert sink.data[data_blocks * BLOCK_BYTES:] == b"\x00" * (
        len(sink.data) - data_blocks * BLOCK_BYTES)


class RecordingSink:
    def __init__(self, path):
        self.data = bytearray()

    def write(self, pcm):
        self.data.extend(pcm)

    def close(self):
        pass


def test_writer_puts_silence_before_the_next_block(tmp_path):
    # Feed the writer known (ndrop, block) pairs directly: the dropped-block
    # silence must land before the block that follows it (the time axis).
    sink = RecordingSink(tmp_path / "a.wav")
    rec = AudioRecorder(lambda: None, sink_factory=lambda path: sink)
    rec._queue = queue.Queue()
    writer = threading.Thread(target=rec._writer_run, args=(tmp_path / "a.wav",),
                              daemon=True)
    writer.start()
    rec._queue.put((0, b"A" * BLOCK_BYTES))
    rec._queue.put((2, b"B" * BLOCK_BYTES))
    rec._queue.put((0, b""))
    writer.join(1.0)
    assert bytes(sink.data) == (b"A" * BLOCK_BYTES
                                + b"\0" * (2 * BLOCK_BYTES)
                                + b"B" * BLOCK_BYTES)


def test_source_open_failure_degrades(tmp_path):
    errors = []
    ready = []
    rec = AudioRecorder(FailingSource, clock=FakeClock(0.0))
    rec.on_error = errors.append
    rec.on_ready = lambda *args: ready.append(args)
    p = tmp_path / "audio.wav"
    rec.start(1, p)
    assert _wait_for(lambda: bool(errors))
    assert len(errors) == 1
    assert rec.recording is False
    assert not p.exists()
    assert ready == []
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < 1.0


class PacedSource:
    """FakeSource variant that sleeps per read so the queue can actually fill."""

    def __init__(self, count, delay=0.002):
        self._remaining = count
        self._delay = delay
        self.reads = 0
        self._event = threading.Event()

    def open(self):
        pass

    def read(self):
        if self._remaining:
            self._remaining -= 1
            self.reads += 1
            time.sleep(self._delay)
            return b"\x01\x00" * 1600
        self._event.wait()
        return b""

    def close(self):
        self._event.set()


def test_sink_oserror_reports_and_drains(tmp_path):
    # The writer raises on every write but must keep consuming; a full queue
    # otherwise wedges the capture thread on its blocking sentinel put.
    source = PacedSource(50)
    errors = []
    rec = AudioRecorder(lambda: source, clock=FakeClock(0.0),
                        sink_factory=OSErrorSink, queue_blocks=1)
    rec.on_error = errors.append
    rec.start(1, tmp_path / "audio.wav")
    assert _wait_for(lambda: source.reads == 50)
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < 1.0
    assert not rec._capture.is_alive()
    assert len(errors) == 1


def test_stop_idempotent(tmp_path):
    source = FakeSource([b"\x01\x00" * 1600])
    rec = AudioRecorder(lambda: source, clock=FakeClock(0.0))
    rec.start(1, tmp_path / "audio.wav")
    rec.stop()
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < 1.0
    assert not rec._capture.is_alive()
    assert not rec._writer.is_alive()


def test_arecord_argv():
    argv = arecord_argv("plughw:1,0")
    i = argv.index("-D")
    assert argv[i:i + 2] == ["-D", "plughw:1,0"]
    assert "16000" in argv
    assert "1" in argv


def test_aplay_argv_device():
    argv = aplay_argv(device="plughw:1,0")
    i = argv.index("-D")
    assert argv[i:i + 2] == ["-D", "plughw:1,0"]
    assert argv[-1] == "-"


def test_player_error_reports_last_stderr_line():
    argv = [sys.executable, "-c",
            "import sys; sys.stdin.buffer.read();"
            " sys.stderr.write('ALSA lib pcm.c: x\\naplay: main:830: audio open error:"
            " Device or resource busy\\n'); sys.exit(1)"]
    proc = play_clip(b"\x00\x00" * 10, argv=argv)
    assert proc is not None
    proc.wait(5)
    assert player_error(proc) == (
        "aplay failed: aplay: main:830: audio open error: Device or resource busy")
    ok = play_clip(b"\x00\x00", argv=[sys.executable, "-c", "import sys; sys.stdin.read()"])
    ok.wait(5)
    assert player_error(ok) is None


def test_arecord_source_fixed_blocks():
    argv = [sys.executable, "-c",
            "import sys; sys.stdout.buffer.write(bytes(6400+100))"]
    src = ArecordSource(argv=argv)
    src.open()
    assert src.read() == bytes(3200)
    assert src.read() == bytes(3200)
    assert src.read() == bytes(100)
    assert src.read() == b""
    src.close()


def test_arecord_missing_binary():
    src = ArecordSource(argv=["/nonexistent/arecord"])
    with pytest.raises(FileNotFoundError):
        src.open()


def test_close_unblocks_read():
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    src = ArecordSource(argv=argv)
    src.open()
    result = []
    reader = threading.Thread(target=lambda: result.append(src.read()), daemon=True)
    reader.start()
    time.sleep(0.1)
    src.close()
    reader.join(1.0)
    assert not reader.is_alive()


def test_play_clip_feeds_stdin():
    pcm = b"\x01\x00" * 1000
    n = len(pcm)
    argv = [sys.executable, "-c",
            f"import sys; sys.exit(0 if len(sys.stdin.buffer.read())=={n} else 1)"]
    proc = play_clip(pcm, argv=argv)
    assert proc is not None
    assert proc.wait(5) == 0
    assert play_clip(pcm, argv=["/nonexistent/aplay"]) is None


def test_envelope_normalised():
    samples = array.array("h", [0] * (2 * RATE))
    for i in range(RATE, 2 * RATE):
        samples[i] = int(10000 * math.sin(2 * math.pi * 1000 * i / RATE))
    env = envelope(samples.tobytes(), 4)
    assert env[0] == 0.0
    assert env[1] == 0.0
    assert abs(env[2] - 1.0) <= 1e-6
    assert abs(env[3] - 1.0) <= 1e-6


def test_envelope_empty_and_silent():
    assert envelope(b"", 5) == [0.0] * 5
    assert envelope(bytes(3200), 5) == [0.0] * 5
    assert envelope(b"\x01\x00" * 100, 0) == []


def test_parse_mixer_entry():
    assert parse_mixer_entry("Mic Boost 0%") == ("Mic Boost", "0%")
    assert parse_mixer_entry("  Capture   60%  ") == ("Capture", "60%")
    with pytest.raises(ValueError):
        parse_mixer_entry("Capture")


def test_mixer_argv():
    assert mixer_argv("1", "Mic Boost", "0%") == [
        "amixer", "-q", "-c", "1", "sset", "Mic Boost", "0%"]
    assert mixer_argv("", "Capture", "60%") == [
        "amixer", "-q", "sset", "Capture", "60%"]


def test_apply_mixer_collects_failures():
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw))
        if "Nope" in argv:
            return SimpleNamespace(
                returncode=1, stderr=b"amixer: Unable to find simple control 'Nope',0\n")
        return SimpleNamespace(returncode=0, stderr=b"")

    messages = apply_mixer("0", ["Capture 60%", "Nope 50%", "Broken"], runner=runner)
    assert messages == [
        "Nope 50%: amixer: Unable to find simple control 'Nope',0",
        "Broken: expected '<control> <value>'",
    ]
    assert [argv for argv, _ in calls] == [
        ["amixer", "-q", "-c", "0", "sset", "Capture", "60%"],
        ["amixer", "-q", "-c", "0", "sset", "Nope", "50%"],
    ]
    assert all(kw == {"capture_output": True, "timeout": 5.0} for _, kw in calls)


def test_apply_mixer_runner_oserror():
    def runner(argv, **kw):
        raise OSError("no amixer")

    messages = apply_mixer("0", ["Capture 60%"], runner=runner)
    assert len(messages) == 1
    assert messages[0].startswith("Capture 60%: ")
    assert "no amixer" in messages[0]


def _samples(value, n=100):
    return array.array("h", [value] * n).tobytes()


def test_process_clip_neutral_is_identity():
    pcm = b"\x10\x27" * 100
    assert process_clip(pcm) is pcm


def test_process_clip_gain_and_clip():
    out = process_clip(_samples(1000), gain_db=6.0206)
    assert all(x == 2000 for x in array.array("h", out))
    out = process_clip(_samples(20000), gain_db=6.0206)
    assert all(x == 32767 for x in array.array("h", out))
    out = process_clip(_samples(-20000), gain_db=6.0206)
    assert all(x == -32768 for x in array.array("h", out))


def test_process_clip_mute():
    pcm = _samples(1000, 16000)
    out = process_clip(pcm, mute=(0.25, 0.75))
    a = array.array("h")
    a.frombytes(out)
    assert all(x == 0 for x in a[4000:12000])
    assert a[3999] == 1000
    assert a[12000] == 1000
    assert len(out) == len(pcm)


def test_process_clip_highpass_removes_dc_keeps_tone():
    dc = array.array("h", [10000] * 16000)
    out = process_clip(dc.tobytes(), highpass_hz=150.0)
    a = array.array("h")
    a.frombytes(out)
    assert 9400 <= a[0] <= 9500
    assert rms(out[-6400:]) < 1.0

    tone = array.array("h", [int(10000 * math.sin(2 * math.pi * 1000 * i / RATE))
                             for i in range(16000)])
    out_t = process_clip(tone.tobytes(), highpass_hz=150.0)
    assert rms(out_t) > 0.95 * rms(tone.tobytes())


def test_tone_pcm_shape():
    pcm = tone_pcm(1000.0, 0.1)
    assert len(pcm) == 3200
    assert rms(pcm) == pytest.approx(12000 / math.sqrt(2), rel=0.02)


class _RecordingStdin(io.BytesIO):
    def close(self):
        self.written = self.getvalue()
        super().close()


class _FakeProc:
    def __init__(self):
        self.stdin = _RecordingStdin()
        self.stderr = None
        self.returncode = None
        self.terminated = 0
        self.waited = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        self.waited += 1
        return 0


def test_beep_player_prepare_fire():
    proc = _FakeProc()
    seen = {}

    def popen(args, **kwargs):
        seen["args"] = args
        return proc

    bp = BeepPlayer("plughw:1,0", popen=popen, clock=FakeClock(5.0),
                    pcm=b"\x01\x02" * 4)
    assert bp.prepare() is True
    assert seen["args"] == aplay_argv(device="plughw:1,0")
    assert bp.fire() == 5.0
    assert proc.stdin.written == b"\x01\x02" * 4
    bp.close()
    assert proc.terminated == 1


def test_beep_player_oserror():
    def popen(*args, **kwargs):
        raise OSError("no aplay")

    bp = BeepPlayer("default", popen=popen, clock=FakeClock())
    assert bp.prepare() is False
    assert bp.fire() is None


PCM_TEXT = """null
    Discard all samples (playback) or generate zero samples (capture)
default
    Default ALSA Output (currently PipeWire Media Server)
sysdefault:CARD=PCH
    HDA Intel PCH, ALC293 Analog
    Default Audio Device
plughw:CARD=Headset,DEV=0
    Logitech USB Headset, USB Audio
    Hardware device with all software conversions
"""
MIXER_TEXT = """Simple mixer control 'Master',0
  Capabilities: pvolume pswitch
  Playback channels: Mono
  Limits: Playback 0 - 87
  Mono: Playback 87 [100%] [0.00dB] [on]
Simple mixer control 'Capture',0
  Capabilities: cvolume cswitch
  Capture channels: Front Left - Front Right
  Limits: Capture 0 - 63
  Front Left: Capture 38 [60%] [12.00dB] [on]
  Front Right: Capture 38 [60%] [12.00dB] [on]
Simple mixer control 'Mic Boost',0
  Capabilities: volume
  Playback channels: Front Left - Front Right
  Capture channels: Front Left - Front Right
  Limits: 0 - 3
  Front Left: 0 [0%]
  Front Right: 0 [0%]
Simple mixer control 'Auto-Mute Mode',0
  Capabilities: enum
  Items: 'Disabled' 'Enabled'
  Item0: 'Enabled'
"""


def test_parse_pcm_list():
    assert parse_pcm_list(PCM_TEXT) == [
        ("default", "Default ALSA Output (currently PipeWire Media Server)"),
        ("sysdefault:CARD=PCH", "HDA Intel PCH, ALC293 Analog · Default Audio Device"),
        ("plughw:CARD=Headset,DEV=0",
         "Logitech USB Headset, USB Audio · Hardware device with all software conversions"),
    ]


def test_list_pcms_runner():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=PCM_TEXT)

    assert len(list_pcms("input", runner=runner)) == 3
    assert len(list_pcms("output", runner=runner)) == 3
    assert calls == [["arecord", "-L"], ["aplay", "-L"]]
    with pytest.raises(ValueError):
        list_pcms("x")


def test_list_pcms_failure_is_empty_default():
    def boom(argv, **kw):
        raise OSError("no arecord")

    def fail(argv, **kw):
        return SimpleNamespace(returncode=1, stdout="")

    assert list_pcms("input", runner=boom) == [("default", "System default")]
    assert list_pcms("input", runner=fail) == [("default", "System default")]


def test_parse_mixer_contents():
    assert parse_mixer_contents(MIXER_TEXT) == [
        {"name": "Master", "capture": False, "playback": True,
         "percent": 100, "switch": True},
        {"name": "Capture", "capture": True, "playback": False,
         "percent": 60, "switch": True},
        {"name": "Mic Boost", "capture": False, "playback": False,
         "percent": 0, "switch": None},
    ]


def test_mixer_controls_runner():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=MIXER_TEXT)

    assert len(mixer_controls("1", runner=runner)) == 3
    assert calls[-1] == ["amixer", "-c", "1", "scontents"]
    assert len(mixer_controls("", runner=runner)) == 3
    assert calls[-1] == ["amixer", "scontents"]


def test_set_mixer():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        if "Nope" in argv:
            return SimpleNamespace(
                returncode=1, stderr=b"amixer: Unable to find simple control 'Nope',0\n")
        return SimpleNamespace(returncode=0, stderr=b"")

    assert set_mixer("0", "Capture", "60%", runner=runner) is None
    message = set_mixer("0", "Nope", "50%", runner=runner)
    assert message == "Nope 50%: amixer: Unable to find simple control 'Nope',0"
    assert calls[0] == ["amixer", "-q", "-c", "0", "sset", "Capture", "60%"]


def test_level_meter_reports_rms():
    block = b"\x10\x27" * 1600
    src = FakeSource([block, block, block, b""])
    levels = []
    blocks = []
    m = LevelMeter(lambda: src, levels.append, on_block=blocks.append)
    m.start()
    assert _wait_for(lambda: not m.running)
    assert levels == [pytest.approx(10000.0)] * 3
    assert len(blocks) == 3
    m.stop()
    m.stop()


def test_level_meter_reports_rms_varying():
    # One loud sample in a 1600-sample block: RMS is 250, peak/mean-abs would
    # report something else, so this pins the actual RMS math.
    block = b"\x10\x27" + b"\x00\x00" * 1599
    src = FakeSource([block, b""])
    levels = []
    m = LevelMeter(lambda: src, levels.append)
    m.start()
    assert _wait_for(lambda: not m.running)
    assert levels == [pytest.approx(250.0)]


def test_level_meter_open_error():
    errs = []
    m = LevelMeter(lambda: FailingSource(), lambda r: None, on_error=errs.append)
    m.start()
    assert _wait_for(lambda: len(errs) == 1)
    assert m.running is False
