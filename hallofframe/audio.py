"""Booth audio: one 16 kHz mono WAV per race, played back around each crossing (spec §13.3.4)."""
from __future__ import annotations

import array
import math
import queue
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Protocol

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


class Source(Protocol):
    def open(self) -> None: ...
    def read(self) -> bytes: ...
    def close(self) -> None: ...


class AudioRecorder:
    """Capture-thread recorder: bounded queue of blocks, writer thread sinks them."""

    def __init__(self, source_factory, *, clock=time.monotonic, sink_factory=WavSink,
                 queue_blocks: int = QUEUE_BLOCKS):
        self._source_factory = source_factory
        self._clock = clock
        self._sink_factory = sink_factory
        self._queue_blocks = queue_blocks
        self.on_ready: Callable[[int, Path, float], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.recording = False
        self.dropped_blocks = 0
        self._source = None
        self._queue = None
        self._capture = None
        self._writer = None
        self._stop = threading.Event()

    def start(self, race_id: int, path: Path) -> None:
        """Begin recording; spawns only the audio-capture daemon thread."""
        self.recording = True
        self.dropped_blocks = 0
        self._stop.clear()
        self._capture = threading.Thread(target=self._capture_run, args=(race_id, path),
                                         name="audio-capture", daemon=True)
        self._capture.start()

    def _capture_run(self, race_id: int, path: Path) -> None:
        src = None
        try:
            src = self._source = self._source_factory()
            src.open()
        except Exception as exc:
            self._report(f"audio: {exc} — race continues without audio")
            self.recording = False
            return
        # The capture thread owns the source: it closes it in ``finally`` even if
        # stop() ran before ``self._source`` was assigned.
        q = self._queue = queue.Queue(maxsize=self._queue_blocks)
        self._writer = threading.Thread(target=self._writer_run, args=(path,),
                                        name="audio-writer", daemon=True)
        self._writer.start()
        pending_drops = 0
        try:
            first = src.read()
            t_start = self._clock() - BLOCK_FRAMES / RATE
            if self.on_ready is not None:
                try:
                    self.on_ready(race_id, path, t_start)
                except Exception:
                    pass
            q.put_nowait((0, first))
            while True:
                block = src.read()
                if block == b"" or self._stop.is_set():
                    break
                try:
                    q.put_nowait((pending_drops, block))
                    pending_drops = 0
                except queue.Full:
                    pending_drops += 1
                    self.dropped_blocks += 1
        finally:
            # Always deliver the sentinel so the writer can exit; the writer
            # never dies (it catches Exception), so this blocking put returns.
            q.put((pending_drops, b""))
            self.recording = False
            if src is not None:
                try:
                    src.close()
                except Exception:
                    pass

    def _writer_run(self, path: Path) -> None:
        sink = None
        reported = False
        try:
            sink = self._sink_factory(path)
        except Exception as exc:
            reported = True
            self._report(f"audio: {exc} — race continues without audio")
        try:
            while True:
                ndrop, block = self._queue.get()
                if sink is not None:
                    try:
                        if ndrop:
                            sink.write(b"\0" * BLOCK_BYTES * ndrop)
                        if block:
                            sink.write(block)
                    except Exception as exc:
                        if not reported:
                            reported = True
                            self._report(f"audio: {exc} — recording incomplete")
                if block == b"":
                    break
        finally:
            if sink is not None:
                try:
                    sink.close()
                except Exception:
                    pass

    def stop(self, timeout: float = 1.0) -> None:
        """Close the source and join capture then writer within a total budget."""
        self._stop.set()
        self._close_source()
        deadline = time.monotonic() + timeout
        for thread in (self._capture, self._writer):
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))
        # Re-close in case the capture thread assigned the source after our first
        # attempt (close() is idempotent on every Source).
        self._close_source()
        self.recording = False

    def _close_source(self) -> None:
        src = self._source
        if src is not None:
            try:
                src.close()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()

    def _report(self, message: str) -> None:
        if self.on_error is not None:
            try:
                self.on_error(message)
            except Exception:
                pass


def arecord_argv(device: str, binary: str = "arecord") -> list[str]:
    return [binary, "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]


class ArecordSource:
    """Source over an arecord subprocess reading fixed 100 ms blocks from stdout."""

    def __init__(self, device: str = "default", *, argv: list[str] | None = None):
        self._argv = argv if argv is not None else arecord_argv(device)
        self._proc = None
        self._closed = False

    def open(self) -> None:
        if self._proc is not None:
            self.close()
        self._proc = subprocess.Popen(self._argv, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        self._closed = False

    def read(self) -> bytes:
        if self._proc is None or self._closed:
            return b""
        buf = bytearray()
        while len(buf) < BLOCK_BYTES:
            chunk = self._proc.stdout.read(BLOCK_BYTES - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass


def aplay_argv(binary: str = "aplay", device: str = "default") -> list[str]:
    return [binary, "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", "16000",
            "-c", "1", "-"]


def play_clip(pcm: bytes, *, argv: list[str] | None = None,
              device: str = "default") -> subprocess.Popen | None:
    """Play raw PCM through aplay; the pipe is fed from a daemon thread.

    stderr is a pipe so a failed player (wrong device, card busy) can be
    reported with :func:`player_error` once it exits."""
    args = argv if argv is not None else aplay_argv(device=device)
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    except OSError:
        return None

    def feed():
        try:
            proc.stdin.write(pcm)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):  # ValueError: closed by stop
            pass

    threading.Thread(target=feed, daemon=True).start()
    return proc


def player_error(proc: subprocess.Popen) -> str | None:
    """After the player exited: None on success, else a one-line reason."""
    if proc.returncode in (None, 0):
        return None
    text = ""
    if proc.stderr is not None:
        try:
            text = proc.stderr.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    tail = lines[-1] if lines else f"exit {proc.returncode}"
    return f"aplay failed: {tail}"


def envelope(pcm: bytes, bins: int) -> list[float]:
    """RMS per bin, normalised so the loudest bin is 1.0 (all 0.0 when silent or empty).
    int16 LE samples split into `bins` equal slices (the last takes the remainder)."""
    if bins <= 0:
        return []
    samples = array.array("h")
    samples.frombytes(pcm)
    n = len(samples)
    out: list[float] = []
    for i in range(bins):
        lo = i * n // bins
        hi = (i + 1) * n // bins
        chunk = samples[lo:hi]
        if not chunk:
            out.append(0.0)
        else:
            out.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    peak = max(out)
    if peak == 0.0:
        return [0.0] * bins
    return [v / peak for v in out]


def parse_mixer_entry(entry: str) -> tuple[str, str]:
    """'Mic Boost 0%' → ('Mic Boost', '0%'); the value is the last token. ValueError if fewer than two tokens."""
    parts = entry.split()
    if len(parts) < 2:
        raise ValueError(entry)
    return " ".join(parts[:-1]), parts[-1]


def mixer_argv(card: str, control: str, value: str, binary: str = "amixer") -> list[str]:
    """[binary, '-q'] + (['-c', card] if card else []) + ['sset', control, value]."""
    argv = [binary, "-q"]
    if card:
        argv += ["-c", card]
    argv += ["sset", control, value]
    return argv


def apply_mixer(card: str, entries: list[str], *, runner=subprocess.run,
                binary: str = "amixer") -> list[str]:
    """Run one amixer sset per entry; return one message per failure (empty = all applied). Never raises."""
    messages: list[str] = []
    for entry in entries:
        try:
            control, value = parse_mixer_entry(entry)
        except ValueError:
            messages.append(f"{entry}: expected '<control> <value>'")
            continue
        try:
            res = runner(mixer_argv(card, control, value, binary=binary),
                         capture_output=True, timeout=5.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            messages.append(f"{entry}: {exc}")
            continue
        if res.returncode != 0:
            text = res.stderr.decode("utf-8", "replace")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            tail = lines[-1] if lines else f"exit {res.returncode}"
            messages.append(f"{entry}: {tail}")
    return messages


def process_clip(pcm: bytes, *, gain_db: float = 0.0, highpass_hz: float = 0.0,
                 mute: tuple[float, float] | None = None, rate: int = RATE) -> bytes:
    """Playback filter chain on int16 LE mono: high-pass → gain → mute → clip to int16."""
    if gain_db == 0.0 and highpass_hz <= 0.0 and mute is None:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm)
    n = len(samples)
    values = [float(x) for x in samples]
    if highpass_hz > 0.0:
        rc = 1.0 / (2.0 * math.pi * highpass_hz)
        dt = 1.0 / rate
        a = rc / (rc + dt)
        prev_x = 0.0
        prev_y = 0.0
        out = []
        for x in values:
            y = a * (prev_y + x - prev_x)
            out.append(y)
            prev_x = x
            prev_y = y
        values = out
    if gain_db != 0.0:
        factor = 10.0 ** (gain_db / 20.0)
        values = [v * factor for v in values]
    if mute is not None:
        start, end = mute
        lo = max(0, round(start * rate))
        hi = min(n, round(end * rate))
        for i in range(lo, hi):
            values[i] = 0.0
    out_samples = array.array("h")
    for v in values:
        out_samples.append(max(-32768, min(32767, round(v))))
    return out_samples.tobytes()


def tone_pcm(freq_hz: float = 1000.0, dur_s: float = 0.1, amplitude: int = 12000,
             rate: int = RATE) -> bytes:
    """int16 LE mono sine; round(dur_s*rate) samples."""
    n = int(round(dur_s * rate))
    out = array.array("h")
    step = 2.0 * math.pi * freq_hz / rate
    for i in range(n):
        out.append(int(round(amplitude * math.sin(step * i))))
    return out.tobytes()


class BeepPlayer:
    """One aplay per beep: prepare() spawns it (device open, waiting on stdin),
    fire() writes the tone."""

    def __init__(self, device: str = "default", *, popen=subprocess.Popen,
                 clock=time.monotonic, pcm: bytes | None = None):
        self.device = device
        self._popen = popen
        self._clock = clock
        self._pcm = tone_pcm() if pcm is None else pcm
        self._proc: subprocess.Popen | None = None

    def prepare(self) -> bool:
        """Spawn aplay, blocked on stdin; False when it cannot be started."""
        self.close()
        try:
            self._proc = self._popen(
                aplay_argv(device=self.device), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError:
            self._proc = None
            return False
        return True

    def fire(self) -> float | None:
        """Write the tone at the stimulus instant; the timestamp is taken first."""
        if self._proc is None or self._proc.stdin is None:
            return None
        t = self._clock()
        try:
            self._proc.stdin.write(self._pcm)
            self._proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            return None
        return t

    def error(self) -> str | None:
        """None while running/OK, else the aplay failure line."""
        if self._proc is None or self._proc.poll() is None:
            return None
        return player_error(self._proc)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc.stderr is not None:
            try:
                proc.stderr.close()
            except (OSError, ValueError):
                pass
