"""Tests for the stdlib audio helpers (plan sheet 09)."""
from __future__ import annotations

import array
import math
import wave

import pytest

from hallofframe.audio import (
    CHANNELS,
    RATE,
    SAMPLE_WIDTH,
    WavSink,
    clip_bounds,
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
