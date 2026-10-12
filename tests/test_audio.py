"""Tests for the stdlib audio helpers (plan sheet 09)."""
from __future__ import annotations

import array
import math
import sys
import threading
import time
import wave

import pytest

from fakes import FakeClock
from hallofframe.audio import (
    BLOCK_BYTES,
    CHANNELS,
    RATE,
    SAMPLE_WIDTH,
    ArecordSource,
    AudioRecorder,
    WavSink,
    aplay_argv,
    arecord_argv,
    clip_bounds,
    play_clip,
    read_clip,
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
    assert sink.data.count(0) >= 50 * BLOCK_BYTES


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


def test_sink_oserror_reports_and_drains(tmp_path):
    source = FakeSource([b"\x01\x00" * 1600] * 5)
    errors = []
    rec = AudioRecorder(lambda: source, clock=FakeClock(0.0), sink_factory=OSErrorSink)
    rec.on_error = errors.append
    rec.start(1, tmp_path / "audio.wav")
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < 1.0
    assert len(errors) == 1


def test_stop_idempotent(tmp_path):
    source = FakeSource([b"\x01\x00" * 1600])
    rec = AudioRecorder(lambda: source, clock=FakeClock(0.0))
    rec.start(1, tmp_path / "audio.wav")
    rec.stop()
    rec.stop()


def test_arecord_argv():
    argv = arecord_argv("plughw:1,0")
    i = argv.index("-D")
    assert argv[i:i + 2] == ["-D", "plughw:1,0"]
    assert "16000" in argv
    assert "1" in argv


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
