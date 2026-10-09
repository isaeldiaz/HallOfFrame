# Sheet 14 — ASR config keys + Norwegian number words

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/config.py`, `hallofframe.example.toml`, `tests/test_config.py` (append), new `hallofframe/asr.py`, new `tests/test_asr.py`.
**Read first:** `config.py` (`DEFAULTS["voice"]`, `_validate`), `hallofframe.example.toml` `[voice]`, `tests/test_config.py` (style), `hallofframe/scratchpad.py` (pure-module style).

### Spec
`config.py` `DEFAULTS["voice"]`: add `"whisper_cli": "whisper-cli"`, `"utterance_gap_s": 0.4`, `"transcribe_timeout_s": 600`. Reword the `model` comment: `NB-Whisper ggml name tiny|base|small, or a path to a .bin`. Comment `transcribe_before_s/after_s`: `unused since §13.3.4 (whole-race transcription)`. `_validate`: `voice.utterance_gap_s > 0` and `voice.transcribe_timeout_s > 0` else `ConfigError`. Same three keys + comments in `hallofframe.example.toml`.

`asr.py`:
```python
"""Offline Norwegian ASR suggestions (spec §13.3.4): whisper.cpp over the race WAV, spoken numbers → capture.bow_suggested. Never writes bow_number."""
NUMBER_WORDS: dict[str, int] = {
  "null":0, "en":1, "ett":1, "én":1, "ein":1, "eitt":1, "to":2, "tre":3, "fire":4, "fem":5, "seks":6,
  "sju":7, "syv":7, "åtte":8, "ni":9, "ti":10, "elleve":11, "tolv":12, "tretten":13, "fjorten":14,
  "femten":15, "seksten":16, "sytten":17, "atten":18, "nitten":19, "tjue":20, "tyve":20,
  "tretti":30, "tredve":30, "førti":40, "femti":50, "seksti":60, "sytti":70, "åtti":80, "nitti":90, "hundre":100}
UNITS = {w: v for w, v in NUMBER_WORDS.items() if v < 10}
TENS  = {w: v for w, v in NUMBER_WORDS.items() if 20 <= v <= 90}
MAX_DIGITS = 4

def clean_word(raw: str) -> str:
    """lower-case; keep ch if ch.isalpha() or ch.isdigit() or ch == '-' (Unicode-aware: å, ø, é must survive); then drop '-'."""
def word_to_digits(raw: str) -> str | None:
    """Order: cleaned empty → None; all ASCII digits → as-is; NUMBER_WORDS hit → str(value);
    <tens><unit> prefix split (tjueen → "21", førtisju → "47"); <unit>og<tens> (enogtyve → "21"); else None."""
```

### Tests
`tests/test_config.py` (append; add `from hallofframe.config import ConfigError, load_config` if missing): `test_voice_defaults_include_asr_keys` (`config().section("voice")` has the three keys with the defaults); `test_voice_gap_must_be_positive` (write `tmp_path/"config.toml"` containing only `[voice]\nutterance_gap_s = 0\n` → `load_config(path)` raises `ConfigError`; a file with only `[voice]` is valid because `_validate` reads `raw.get("timing", {})`).
`tests/test_asr.py`: parametrized `test_word_to_digits`:
`("en","1"), ("ett","1"), ("sju","7"), ("syv","7"), ("tjue","20"), ("tyve","20"), ("null","0"), ("fjorten","14"), ("tjueen","21"), ("Tjue-en,","21"), ("enogtyve","21"), ("førtisju","47"), ("14","14"), ("1-4","14"), ("og",None), ("klar",None), ("",None)`.

**Commit:** `feat(asr): [voice] ASR keys, Norwegian number words (§13.3.4 step 14)`
