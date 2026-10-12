# Sheet 26 — iPhone microphone over the USB tunnel (HTTP WAV source) — GATED

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/audio.py` (append), `hallofframe/main.py` (`build_core`: the recorder source lambda only), `hallofframe/ui/calibration_dialog.py` (`CalibrationDialog.__init__`: the `AudioPage(...)` call only), `tests/test_audio.py` (append).
**Read first:** `hallofframe/audio.py` (`Source`, `ArecordSource`, `BLOCK_FRAMES`, `BLOCK_BYTES`, `RATE`), `hallofframe/main.py` `build_core` (the `voice` block), `hallofframe/ui/calibration_dialog.py` (`CalibrationDialog.__init__`), `hallofframe/ui/audio_page.py` (`extra_inputs`), `tests/test_audio.py` (`FakeSource`, `rms`, `test_arecord_source_fixed_blocks`).

### Gate (do this first, on the laptop with the phone connected and IP Camera Lite streaming)
The phone's microphone is not an ALSA device; it can only reach the laptop as an HTTP audio stream through the same iproxy tunnel as the video. Whether IP Camera Lite serves one, and at which path, is **not verified**. Run:
```
for p in /audio.wav /audio /audio.aac /audio.mp3 /audio.pcm; do printf '%s ' "$p"; curl -s -m 3 -o /dev/null -w '%{http_code} %{content_type}\n' "http://127.0.0.1:8081$p"; done
```
- A path answering `200` with `audio/wav`, `audio/x-wav` or `audio/wave` → proceed; put that path in `PHONE_AUDIO_PATHS` below (first).
- Only compressed formats (`aac`/`mp3`) or nothing → write `plans/bow-capture/BLOCKED-26.md` (3 lines: what you got per path, the app's own documented endpoints if its help page lists any, that decoding would need `ffmpeg`), and stop. The audio tab then simply has no iPhone entry; nothing else in R4 depends on this sheet.

### Spec

`audio.py` (append; stdlib `urllib.request`, `struct`, `urllib.parse`):
```python
PHONE_AUDIO_PATHS = ("/audio.wav", "/audio")   # candidates, verified order

def parse_wav_header(fh) -> tuple[int, int, int]:
    """Read RIFF/WAVE chunks from a non-seekable stream up to the 'data' chunk; return (channels, rate, bits). ValueError when not PCM WAV."""

def to_mono16k(pcm: bytes, channels: int, rate: int, bits: int) -> bytes:
    """Average channels, nearest-index resample to RATE, 16-bit LE out (8-bit unsigned input is centred first)."""

class HttpWavSource:
    """Source over an HTTP WAV stream (the phone mic through the tunnel); yields BLOCK_BYTES blocks at RATE mono."""
    def __init__(self, url: str, *, timeout: float = 5.0, opener=urllib.request.urlopen)
    def open(self) -> None     # opener(Request(url), timeout=timeout) → parse_wav_header; stores channels/rate/bits; any error → raise (AudioRecorder/LevelMeter report it)
    def read(self) -> bytes    # read enough source bytes for one output block, convert, return exactly BLOCK_BYTES (last partial block padded with zeros; b"" after EOF/close)
    def close(self) -> None    # idempotent; closes the response

def make_source(input_name: str) -> Source:
    """'http://…'/'https://…' → HttpWavSource, else ArecordSource."""

def probe_phone_audio(stream_url: str, *, paths=PHONE_AUDIO_PATHS, opener=urllib.request.urlopen,
                      timeout: float = 2.0) -> str | None:
    """First `scheme://host:port<path>` (derived from [stream] url) whose GET answers 200 with Content-Type audio/*; never raises."""
```
`parse_wav_header`: read 12 bytes (`RIFF`, size, `WAVE`); loop: 8-byte chunk header (`id`, `size`); `fmt ` → read `size` bytes, `struct.unpack("<HHIIHH", data[:16])` → `(format, channels, rate, byte_rate, align, bits)`; `format` must be 1 (PCM) or 0xFFFE; other chunks → read and discard `size` (+1 if odd); `data` → return. Missing `fmt ` before `data` → `ValueError("no fmt chunk")`.
`to_mono16k`: frame = `channels * bits // 8` bytes; decode with `array("h")` (16-bit) / `array("b")` on bytes −128 shifted ×256 (8-bit) / `array("i")` ≫ 16 after unpacking 3-byte samples by hand (24-bit); average channels per frame; `ratio = rate / RATE`; output length `floor(n_frames / ratio)`; `out[i] = mono[int(i * ratio)]`; clip to int16. comment: `nearest-index resampling is enough for voice; no filtering dependency`.
`read()`: source bytes per output block = `ceil(BLOCK_FRAMES * rate / RATE) * frame_bytes`; carry leftover frames between calls so block boundaries never split a frame.

`main.py` `build_core`: `AudioRecorder(lambda: make_source(str(config.section("voice")["input"])))` (import `make_source` instead of `ArecordSource`).

`calibration_dialog.py`: before creating the audio tab, `phone = probe_phone_audio(config.section("stream")["url"])` and pass `extra_inputs=[(phone, "iPhone microphone (IP Camera Lite, via USB tunnel)")] if phone else []`. The probe runs at dialog construction (idle time only; 2 s worst case per path).

### Tests

`tests/test_audio.py` (append). Helper `wav_bytes(channels, rate, bits, seconds, freq=1000.0, amp=10000)` building a full WAV in memory with `wave` (16-bit) — for 8-bit write unsigned bytes by hand. A local server: `http.server.ThreadingHTTPServer` on port 0 with a handler serving `/audio.wav` (`200`, `Content-Type: audio/x-wav`, the bytes), `/audio` (`404`) and `/audio.aac` (`200`, `audio/aac`); start it in a daemon thread in a fixture, `shutdown()` on teardown.
| Test | Steps | Expected |
|---|---|---|
| `test_parse_wav_header_skips_chunks` | WAV bytes with an extra `LIST` chunk of 7 bytes (odd → pad) inserted before `data`; `parse_wav_header(io.BytesIO(b))` | `(2, 44100, 16)` |
| `test_parse_wav_header_not_pcm` | `fmt` format `3` (float) | `pytest.raises(ValueError)` |
| `test_to_mono16k_stereo_44k` | 1 s stereo 44.1 kHz 1 kHz tone, amp 10000 | out length `== 16000 * 2` bytes; `rms(out) == pytest.approx(10000 / math.sqrt(2), rel=0.05)` |
| `test_to_mono16k_8bit` | 0.5 s mono 8 kHz 8-bit (unsigned, centred at 128, amp 100) | `len(out) == 8000 * 2`; `rms(out) == pytest.approx(100 * 256 / math.sqrt(2), rel=0.05)` |
| `test_http_wav_source_blocks` | server; `src = HttpWavSource(f"{base}/audio.wav")`; `src.open()`; read until `b""` | every block but the last `len == BLOCK_BYTES`; last `len == BLOCK_BYTES` too (zero-padded); total non-empty blocks `== 10` for a 1 s stereo 44.1 kHz file; `rms(first block) > 5000`; `src.close()` twice OK |
| `test_http_wav_source_404_raises` | `HttpWavSource(f"{base}/audio").open()` | raises (`urllib.error.HTTPError`) |
| `test_make_source` | `make_source("http://x/y")`; `make_source("default")` | `isinstance(..., HttpWavSource)`; `isinstance(..., ArecordSource)` |
| `test_probe_phone_audio` | `probe_phone_audio(f"{base}/video")` with default paths; then `paths=("/audio", "/audio.aac")`; then a closed port | `== f"{base}/audio.wav"`; `None` (aac is not accepted); `None` within 3 s |

**Commit:** `feat(audio): iPhone mic over the tunnel — HttpWavSource, make_source, probe for the Audio tab (step 26)`
