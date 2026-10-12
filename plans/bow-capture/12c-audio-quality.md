# Sheet 12c — audio quality: mixer settings at startup, playback filter chain

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/audio.py` (append), `hallofframe/config.py` (`[voice]` defaults only), `hallofframe/main.py` (`build_core` + one loop in `main`), `hallofframe/ui/review_screen.py` (`play_selected` only), `tests/test_audio.py` (append), `tests/test_controller_audio.py` (append), `tests/test_review_audio.py` (append), `hallofframe.example.toml` (`[voice]`), `hallofframe-finish-timer-spec.md` (§8 `[voice]` block and the §13.3.4 "Implemented" note only), `AGENTS.md` (the "Audio (§13.3.4)" bullet only), `TESTING.md` (T14 only).
**Read first:** `hallofframe/audio.py` (`RATE`, `read_clip`, `aplay_argv`, `play_clip`, `player_error`), `hallofframe/config.py` `DEFAULTS["voice"]`, `hallofframe/main.py` `build_core` (the `voice` block) and `main` (from `core = build_core(config)` to `bridge.warning.connect(win._show_toast)`), `hallofframe/ui/review_screen.py` `play_selected`, `tests/test_audio.py` (`write_tone_wav`, `rms`, `test_aplay_argv_device`, `test_player_error_reports_last_stderr_line`), `tests/test_controller_audio.py` `test_build_core_attaches_recorder`, `tests/test_review_audio.py` (fixture + arithmetic note).

### Goal
The recorder captures whatever ALSA hands it; on the bench that is hiss (capture gain pinned at 100 %, mic boost on) and a very loud trigger-button click next to the mic. Two layers, both configured in `[voice]`, neither touching the recorded WAV or timing data:

1. **Hardware capture settings applied once at app start** — a list of `amixer sset` entries. This is a convenience wrapper around what the operator would otherwise type in `alsamixer`; it removes the hiss at the source.
2. **A playback-side filter chain** in review — high-pass (hum, desk thumps), make-up gain, a mute window around `t_press` (the button click), and int16 clipping after gain. Pure Python over ≤ 8 s of 16 kHz PCM (a few tens of ms, review only, never the trigger path). The file stays raw so R3's ASR sees the untouched signal.

### Spec

`config.py` `DEFAULTS["voice"]` — add after `"output"`:
```python
        "mixer_card": "",      # amixer -c <card>; "" = default card ("amixer -c 0 scontrols" lists controls)
        "mixer": [],           # ["Capture 60%", "Mic Boost 0%"]: amixer sset entries applied at app start
        "gain_db": 0.0,        # playback make-up gain (dB); clipped to int16 after gain
        "highpass_hz": 0.0,    # playback one-pole high-pass; 0 disables (150 cuts hum/thumps)
        "press_mute_ms": 0,    # playback: mute ±press_mute_ms/2 around t_press (button click); 0 disables
```

`audio.py` (append; stdlib only — `array`, `math`, `subprocess`):
```python
def parse_mixer_entry(entry: str) -> tuple[str, str]:
    """'Mic Boost 0%' → ('Mic Boost', '0%'); the value is the last token. ValueError if fewer than two tokens."""

def mixer_argv(card: str, control: str, value: str, binary: str = "amixer") -> list[str]:
    """[binary, '-q'] + (['-c', card] if card else []) + ['sset', control, value]."""

def apply_mixer(card: str, entries: list[str], *, runner=subprocess.run,
                binary: str = "amixer") -> list[str]:
    """Run one amixer sset per entry; return one message per failure (empty = all applied). Never raises."""
```
`apply_mixer` per entry: `parse_mixer_entry` (`ValueError` → `f"{entry}: expected '<control> <value>'"`), then `res = runner(argv, capture_output=True, timeout=5.0)`; `OSError`/`subprocess.TimeoutExpired` → `f"{entry}: {exc}"`; `res.returncode != 0` → `f"{entry}: {tail}"` where `tail` is the last non-empty line of `res.stderr.decode("utf-8", "replace")`, or `f"exit {res.returncode}"` when stderr is empty. Continue with the next entry after a failure.

```python
def process_clip(pcm: bytes, *, gain_db: float = 0.0, highpass_hz: float = 0.0,
                 mute: tuple[float, float] | None = None, rate: int = RATE) -> bytes:
    """Playback filter chain on int16 LE mono: high-pass → gain → mute → clip to int16."""
```
- Fast path: when `gain_db == 0.0 and highpass_hz <= 0.0 and mute is None` return `pcm` itself (same object).
- Decode with `array("h")`; work in floats.
- High-pass (when `highpass_hz > 0`): one-pole, `rc = 1 / (2π·highpass_hz)`, `dt = 1 / rate`, `a = rc / (rc + dt)`, `y[n] = a · (y[n−1] + x[n] − x[n−1])` with `x[−1] = y[−1] = 0`.
- Gain: multiply by `10 ** (gain_db / 20)`.
- Mute (when given, seconds into the clip): `lo = max(0, round(start·rate))`, `hi = min(n, round(end·rate))`, samples `[lo, hi)` set to 0.
- Clip: `max(-32768, min(32767, round(v)))`; encode with `array("h")` and return bytes.

`main.py`:
- `build_core`: inside the existing `if bool(voice["enabled"]):` block, after `attach_recorder(...)`:
  ```python
  mixer_errors: list[str] = []
  if voice["mixer"]:
      from .audio import apply_mixer
      mixer_errors = apply_mixer(str(voice["mixer_card"]), [str(e) for e in voice["mixer"]])
  ```
  (`mixer_errors = []` is initialised before the `if bool(voice["enabled"])`, so it exists on every path.) Add `"mixer_errors": mixer_errors` to the returned dict.
- `main`: right after `bridge.warning.connect(win._show_toast)`:
  ```python
  for err in core["mixer_errors"]:
      logger.warning("audio", "mixer_failed", detail=err)
      win._show_toast(f"audio mixer: {err}")
  ```
  comment: `mixer runs once at start-up on the main thread, before any race`.

`review_screen.py` `play_selected`: right after `pcm = read_clip(self._audio_path, *bounds)` (and its `except`), **before** the sheet-12b `self.audio_view.set_clip(...)` block and before `play_clip`:
```python
start_s, dur_s = bounds
press_in_clip = (elapsed - self._audio_offset) - start_s
mute_ms = float(self._voice["press_mute_ms"])
mute = None
if mute_ms > 0:
    half = mute_ms / 2000.0
    mute = (press_in_clip - half, press_in_clip + half)
pcm = process_clip(pcm, gain_db=float(self._voice["gain_db"]),
                   highpass_hz=float(self._voice["highpass_hz"]), mute=mute)
```
Import `process_clip` in the existing `from ..audio import ...` line (tests `mock.patch("hallofframe.ui.review_screen.process_clip")`). If sheet 12b is already merged, its block already computes `start_s, dur_s, press_in_clip`; do not compute them twice — place this code above 12b's block and drop 12b's duplicate lines. The envelope view then shows the processed clip (what the operator hears).

Docs (one line each, no new sections): `hallofframe.example.toml` `[voice]` — the five keys with the comments above (`mixer` as `mixer = []` with an example in the comment); spec §8 `[voice]` block — same five lines; spec §13.3.4 "Implemented" note — append `; [voice] mixer/mixer_card apply amixer sset at start-up, gain_db/highpass_hz/press_mute_ms filter the review playback only (file stays raw)`; `AGENTS.md` "Audio (§13.3.4)" bullet — append `Capture gain is set once at start-up from [voice] mixer (audio.apply_mixer, errors → toast); review playback runs audio.process_clip (high-pass → gain → press mute → int16 clip) on the clip bytes only.`; `TESTING.md` T14 — add: `With [voice] mixer = ["Capture 60%", "Mic Boost 0%"] (names from amixer -c <card> scontrols) the hiss drops; a bad control name shows an "audio mixer: …" toast at start-up. With press_mute_ms = 100 the button click is silent in P playback; highpass_hz = 150 removes hum.`

### Tests

`tests/test_audio.py` (append; import `apply_mixer, mixer_argv, parse_mixer_entry, process_clip`; `from types import SimpleNamespace`):
| Test | Input | Expected |
|---|---|---|
| `test_parse_mixer_entry` | `"Mic Boost 0%"`; `"  Capture   60%  "`; `"Capture"` | `("Mic Boost", "0%")`; `("Capture", "60%")`; `pytest.raises(ValueError)` |
| `test_mixer_argv` | `mixer_argv("1", "Mic Boost", "0%")`; `mixer_argv("", "Capture", "60%")` | `["amixer", "-q", "-c", "1", "sset", "Mic Boost", "0%"]`; `["amixer", "-q", "sset", "Capture", "60%"]` |
| `test_apply_mixer_collects_failures` | fake `runner(argv, **kw)` appending `argv` to a list and returning `SimpleNamespace(returncode=0, stderr=b"")` for `"Capture 60%"`, `SimpleNamespace(returncode=1, stderr=b"amixer: Unable to find simple control 'Nope',0\n")` for `"Nope 50%"`; entries `["Capture 60%", "Nope 50%", "Broken"]`, card `"0"` | returns `["Nope 50%: amixer: Unable to find simple control 'Nope',0", "Broken: expected '<control> <value>'"]`; recorded argvs `== [["amixer","-q","-c","0","sset","Capture","60%"], ["amixer","-q","-c","0","sset","Nope","50%"]]`; every call had `capture_output=True` and `timeout=5.0` |
| `test_apply_mixer_runner_oserror` | runner raises `OSError("no amixer")`; entries `["Capture 60%"]` | one message, starts with `"Capture 60%: "` and contains `"no amixer"` |
| `test_process_clip_neutral_is_identity` | `pcm = b"\x10\x27" * 100` | `process_clip(pcm) is pcm` |
| `test_process_clip_gain_and_clip` | 100 samples of `1000`, `gain_db=6.0206`; 100 of `20000` same gain; 100 of `-20000` same gain | every output sample `== 2000`; `== 32767`; `== -32768` |
| `test_process_clip_mute` | 16000 samples of `1000`, `mute=(0.25, 0.75)` | samples `4000..11999 == 0`; sample `3999 == 1000`; sample `12000 == 1000`; `len(out) == len(pcm)` |
| `test_process_clip_highpass_removes_dc_keeps_tone` | DC: 16000 samples of `10000`, `highpass_hz=150.0` → `out`; tone: 16000 samples `int(10000·sin(2π·1000·i/RATE))`, same filter → `out_t` | `9400 <= out[0] <= 9500` (a = rc/(rc+dt) ≈ 0.9444 → first sample ≈ 9444); `rms(out[-6400:]) < 1.0` (last 3200 samples); `rms(out_t) > 0.95 * rms(tone)` (|H| at 1 kHz ≈ 0.989) |

`tests/test_controller_audio.py` (append):
| Test | Steps | Expected |
|---|---|---|
| `test_build_core_applies_mixer` | `cfg = config(transport={"enabled": False}, voice={"enabled": True, "mixer": ["Capture 60%"], "mixer_card": "0"})`; `with mock.patch("hallofframe.audio.apply_mixer", return_value=["Capture 60%: boom"]) as am: core = build_core(cfg)` (same `finally` teardown as `test_build_core_attaches_recorder`) | `am.assert_called_once_with("0", ["Capture 60%"])`; `core["mixer_errors"] == ["Capture 60%: boom"]` |
| `test_build_core_skips_mixer_when_off` | voice enabled with `mixer=[]`; and voice disabled with `mixer=["Capture 60%"]`; same patch | `apply_mixer` never called; `core["mixer_errors"] == []` both times |

`tests/test_review_audio.py` (append to `TestReviewAudio`; add `voice={... , "gain_db": 6.0206, "highpass_hz": 0.0, "press_mute_ms": 100}` to the fixture's `voice` dict — these are new keys, no existing expected value changes; the existing `test_p_plays_clip_around_press` keeps passing because +6 dB leaves silence at 0 and the burst RMS above 5000). Arithmetic: seq 2 press elapsed 2.0, offset 0.5, clip starts at file-time 0.5 → press at **1.0 s** into the clip; `press_mute_ms = 100` → mute `(0.95, 1.05)`.
| Test | Steps | Expected |
|---|---|---|
| `test_p_processes_clip_with_config` | `screen._select(2)`; patch `hallofframe.ui.review_screen.process_clip` with `side_effect=lambda pcm, **kw: pcm` and patch `play_clip`; key `P` | `process_clip` called once; `kw = process_clip.call_args.kwargs`; `kw["gain_db"] == 6.0206`; `kw["highpass_hz"] == 0.0`; `kw["mute"] == pytest.approx((0.95, 1.05), abs=1e-6)`; `play_clip.call_args[0][0] is process_clip.call_args[0][0]` |
| `test_p_mutes_the_press` | `screen._select(2)`; patch `play_clip` only; key `P`; `pcm = play.call_args[0][0]` | `rms(pcm[30400:33600]) == 0` (0.95–1.05 s, the mute); `rms(pcm[33600:38400]) > 10000` (1.05–1.2 s: burst RMS 7071 × 2 by +6 dB ≈ 14142); `rms(pcm[0:28800]) == 0` (0–0.9 s) |

**Commit:** `feat(audio): [voice] mixer at start-up, playback high-pass/gain/press-mute (§13.3.4 step 12c)`
