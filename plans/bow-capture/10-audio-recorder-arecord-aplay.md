# Sheet 10 — `audio.py` part 2: `AudioRecorder`, `ArecordSource`, `play_clip`

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/audio.py`, `tests/test_audio.py` (append).
**Read first:** `hallofframe/audio.py`, `hallofframe/transport.py` (how `iproxy` is run with `subprocess.Popen` and stopped), `tests/fakes.py`.

### Spec
```python
class Source(Protocol):
    def open(self) -> None: ...      # may raise OSError
    def read(self) -> bytes: ...     # BLOCK_BYTES, or b"" at EOF; must return promptly after close()
    def close(self) -> None: ...     # thread-safe

class AudioRecorder:
    def __init__(self, source_factory, *, clock=time.monotonic, sink_factory=WavSink, queue_blocks=QUEUE_BLOCKS)
    on_ready: Callable[[int, Path, float], None] | None   # (race_id, path, t_first_sample_mono)
    on_error: Callable[[str], None] | None                 # one line; never raises
    recording: bool; dropped_blocks: int
    def start(self, race_id: int, path: Path) -> None     # non-blocking; spawns ONLY the "audio-capture" daemon thread
    def stop(self, timeout: float = 1.0) -> None          # source.close(); join capture then writer, `timeout` is the TOTAL budget; idempotent
    def close(self) -> None                               # alias of stop()
```
Capture thread, in this order:
1. `src = self._source = source_factory(); src.open()`. On any exception: `on_error(f"audio: {exc} — race continues without audio")`, `recording = False`, return. **No writer thread and no file exist at this point.**
2. Only now start the `"audio-writer"` daemon thread (it creates the sink: `sink = sink_factory(path)`).
3. `first = src.read()`; `t_start = clock() - BLOCK_FRAMES / RATE`; `on_ready(race_id, path, t_start)`; `q.put_nowait((0, first))` — the first block is written like every other block.
4. Loop: `block = src.read()`; if `block == b""` or stop requested: break; else `try: q.put_nowait((pending_drops, block)); pending_drops = 0` / `except queue.Full: pending_drops += 1; dropped_blocks += 1`.
5. End of stream: `q.put((pending_drops, b""))` — a **blocking** put (it must wait for a slow sink), and the pending drops ride on it so the silence is still written. The empty block is the sentinel.
Writer thread: `sink = sink_factory(path)`; for each `(ndrop, block)`: write `b"\0" * BLOCK_BYTES * ndrop` then `block` (dropped blocks become silence so the time axis survives); stop when `block == b""`. On `OSError`: call `on_error` **once** (a `reported` flag), keep consuming items without writing until the sentinel. `finally: sink.close()` (wrapped in `try/except OSError`).
`comment: the capture thread never touches disk; the trigger thread never touches audio`.

```python
def arecord_argv(device: str, binary: str = "arecord") -> list[str]:
    # [binary, "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
class ArecordSource:            # Source over an arecord subprocess (stdout=PIPE, stderr=DEVNULL)
    def __init__(self, device: str = "default", *, argv: list[str] | None = None)
    # read(): b"" when not opened or already closed; else accumulate stdout.read(n) until BLOCK_BYTES; a short tail at EOF is returned as-is; then b"".
    # close(): terminate → wait(0.5) → kill; close stdout. Safe to call twice / before open.
def aplay_argv(binary: str = "aplay") -> list[str]:
    # [binary, "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1", "-"]
def play_clip(pcm: bytes, *, argv: list[str] | None = None) -> subprocess.Popen | None:
    # Popen(stdin=PIPE, stdout/stderr=DEVNULL); a daemon thread writes pcm then closes stdin (never block the GUI on the pipe);
    # the feeder swallows BrokenPipeError/OSError (the caller may kill the player mid-write). None on OSError at Popen.
```

### Tests (append to `tests/test_audio.py`)
Fakes, exact semantics:
- `FakeSource(blocks)`: `open()` no-op; `read()` pops and returns the next block **whenever blocks remain, regardless of `close()`**; only when the list is empty does it wait on an `Event` (set by `close()`) and then return `b""`; `reads` counts returned non-empty blocks.
- `GateSink(path)`: `write(pcm)` waits on an `Event` (`gate`) then appends to `self.frames` (frame count) and `self.data`; `close()` no-op. Written zeros are visible in `self.data`.

| Test | Steps | Expected |
|---|---|---|
| `test_records_blocks_and_reports_t_start` | 10 blocks of `b"\x01\x00"*1600`; `FakeClock(1000.1)`; `start(1, p)`; wait for `on_ready` (≤2 s) then `stop()` | WAV has 16000 frames, bytes identical (all 10 blocks, including the first); `on_ready == [(1, p, 1000.0)]` |
| `test_capture_never_blocks_drops_become_silence` | `queue_blocks=5`; 60 blocks of `b"\x01\x00"*1600`; `GateSink` gate closed; wait ≤2 s until `source.reads == 60`; assert `dropped_blocks >= 50`; open gate; `stop()` | `sink.frames == 60*1600`; `sink.data` contains at least `50*BLOCK_BYTES` zero bytes |
| `test_source_open_failure_degrades` | `open()` raises `FileNotFoundError("arecord")` | `on_error` once; `recording False`; `p` does not exist; `on_ready` not called; `stop()` returns promptly |
| `test_sink_oserror_reports_and_drains` | sink `write` always raises `OSError` | `on_error` exactly once; `stop()` returns within 1 s |
| `test_stop_idempotent` | `stop()` twice | no error |
| `test_arecord_argv` | `arecord_argv("plughw:1,0")` | contains `["-D","plughw:1,0"]`, `"16000"`, `"1"` |
| `test_arecord_source_fixed_blocks` | `argv=[sys.executable,"-c","import sys; sys.stdout.buffer.write(bytes(6400+100))"]` | reads: 3200, 3200, 100, `b""` |
| `test_arecord_missing_binary` | `argv=["/nonexistent/arecord"]` | `open()` raises `FileNotFoundError` |
| `test_close_unblocks_read` | stand-in `time.sleep(30)`; `close()` from another thread | `read()` returns within 1 s |
| `test_play_clip_feeds_stdin` | stand-in `sys.exit(0 if len(sys.stdin.buffer.read())==N else 1)` | `proc.wait(5) == 0`; missing binary → `None` |

**Commit:** `feat(audio): AudioRecorder, arecord source, aplay clip player (§13.3.4 step 10)`
