"""Booth audio: one 16 kHz mono WAV per race, played back around each crossing (spec §13.3.4)."""
from __future__ import annotations

import wave
from pathlib import Path

RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
BLOCK_FRAMES = 1600
BLOCK_BYTES = BLOCK_FRAMES * SAMPLE_WIDTH
QUEUE_BLOCKS = 50
AUDIO_FILENAME = "audio.wav"


def clip_bounds(t_press: float, t0: float, offset_s: float,
                before_s: float, after_s: float, total_s: float) -> tuple[float, float] | None:
    """Return (start, duration) of the clip around a press, or None if empty."""
    pos = (t_press - t0) - offset_s
    start = max(0.0, pos - before_s)
    end = min(total_s, pos + after_s)
    if end <= start:
        return None
    return (start, end - start)


def wav_duration_s(path) -> float:
    """Duration of a WAV file in seconds."""
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def read_clip(path, start_s: float, dur_s: float) -> bytes:
    """Read ``dur_s`` seconds from ``start_s`` of a WAV file."""
    with wave.open(str(path), "rb") as w:
        w.setpos(round(start_s * w.getframerate()))
        return w.readframes(round(dur_s * w.getframerate()))


class WavSink:
    """Append-only WAV writer whose header is valid after every block."""

    def __init__(self, path: Path):
        self._closed = False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(CHANNELS)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(RATE)

    def write(self, pcm: bytes) -> None:
        self._wav.writeframes(pcm)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._wav.close()
