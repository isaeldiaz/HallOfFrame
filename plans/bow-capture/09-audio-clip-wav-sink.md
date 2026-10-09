# Sheet 09 — `audio.py` part 1: constants, clip maths, WAV sink (pure stdlib)

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** new `hallofframe/audio.py`, new `tests/test_audio.py`.
**Read first:** `hallofframe/framestore.py` lines 1–40 (module style), `tests/fakes.py` (`FakeClock`).

### Goal
Everything here is stdlib-only (`wave`, `array`, `pathlib`) and runs on Windows/CI. No threads yet.

### Spec
```python
"""Booth audio: one 16 kHz mono WAV per race, played back around each crossing (spec §13.3.4)."""
RATE = 16000; CHANNELS = 1; SAMPLE_WIDTH = 2
BLOCK_FRAMES = 1600                     # 100 ms
BLOCK_BYTES = BLOCK_FRAMES * SAMPLE_WIDTH
QUEUE_BLOCKS = 50                       # 5 s buffered before blocks are dropped
AUDIO_FILENAME = "audio.wav"

def clip_bounds(t_press: float, t0: float, offset_s: float,
                before_s: float, after_s: float, total_s: float) -> tuple[float, float] | None:
    """pos = (t_press - t0) - offset_s is the press position in the recording.
    start = max(0, pos - before_s); end = min(total_s, pos + after_s).
    Returns (start, end - start), or None when end <= start."""
def wav_duration_s(path) -> float          # wave.open: nframes / framerate
def read_clip(path, start_s: float, dur_s: float) -> bytes   # wave.setpos(round(start*rate)); readframes(round(dur*rate))

class WavSink:
    """Append-only WAV writer. Uses writeframes() (not writeframesraw) so the header is valid after every block."""
    def __init__(self, path: Path): ...  # path.parent.mkdir(parents=True, exist_ok=True); wave.open(str(path), "wb"); set channels/width/rate
    def write(self, pcm: bytes) -> None
    def close(self) -> None               # idempotent
```

### Tests — `tests/test_audio.py`
Helper `write_tone_wav(path, total_s=20.0, burst=(12.0, 12.5))`: silence, 1 kHz burst amplitude 10000 inside `burst`, built with `array("h")` + `wave`. Helper `rms(pcm: bytes) -> float` via `array("h")` + `math.sqrt(sum(x*x)/n)` (no `audioop`: removed in Python 3.13).

| Test | Input | Expected |
|---|---|---|
| `test_clip_bounds_centred` | `clip_bounds(1014.25, 1000.0, 2.0, 3.0, 5.0, 20.0)` | `(9.25, 8.0)` |
| `test_clip_bounds_clamps` | `clip_bounds(1000 + 2 + pos, 1000.0, 2.0, 3.0, 5.0, 20.0)` for pos 1.0 → `(0.0, 6.0)`; pos 18.0 → `(15.0, 5.0)`; pos 30 → `None`; pos −10 → `None` |
| `test_read_clip_contains_burst` | tone wav; `read_clip(p, 9.25, 8.0)` | `len == 8*RATE*2`; `rms(clip[2.75 s:3.25 s]) > 5000`; `rms(clip[0:2.5 s]) == 0` |
| `test_wav_duration` | tone wav | `20.0` |
| `test_wav_sink_header_valid_after_each_block` | `WavSink`; write 3 blocks; **before** close, `wav_duration_s(path)` | `0.3` (±1e-6); after `close()` still `0.3`; `close()` twice OK |

**Commit:** `feat(audio): clip maths and WAV sink (§13.3.4 step 09)`
