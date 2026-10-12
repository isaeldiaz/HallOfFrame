# Sheet 21 — reaction: Qt-free trial run, calibration.json fields, Δ uses the measured value

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/calibration.py`, `hallofframe/controller.py` (`_compute_delta`, `_load_latency`, `calibration_status` only), `hallofframe/ui/about_screen.py` (`environment_rows` + one helper), `tests/test_calibration.py` (append), `tests/test_reaction.py` (create).
**Read first:** `hallofframe/calibration.py` (all), `hallofframe/controller.py` lines 331–343 (`_compute_delta`) and 787–830 (`_load_latency`, `calibration_status`), `hallofframe/ui/about_screen.py` lines 60–85, `tests/test_calibration.py` (all), `tests/conftest.py` (`config`, `storage`, `buffer` fixtures).

### Goal
Measure the operator's reaction time instead of guessing it. This sheet is the Qt-free core: a trial state machine (`ReactionRun`), the stats, the file format inside `calibration.json`, and the Δ formula reading the measured value. The dialog comes in sheets 22–23.

Two kinds: `visual` (the operator reacts to something appearing on screen — the finish-line reaction) and `audio` (reacts to a beep — the gun-start reaction). Only `visual` feeds Δ (`Δ = R − L` in water mode, `Δ = R` in screen mode). `audio` is stored and displayed only; elapsed times are never corrected for reaction (the press/t0 cancellation argument) — do not wire it into timing.

### Spec

`calibration.py` (append; stdlib `random`, `statistics`):
```python
REACTION_TRIALS = 10
REACTION_MIN = 6
REACTION_EARLY_MS = 80.0      # faster than this is a guess, not a reaction
REACTION_LATE_MS = 1500.0     # slower than this is a miss
REACTION_DELAY_S = (1.5, 4.0) # random wait before the stimulus
REACTION_KINDS = ("visual", "audio")

def reaction_stats(samples_ms: list[float]) -> dict:
    """{'median_ms','iqr_ms','mean_ms','n'}; ValueError when n < REACTION_MIN."""
```
`iqr_ms` as in `compute_latency` (`statistics.quantiles(n=4)`, `q[2]-q[0]`, 0.0 on exception).

```python
class ReactionRun:
    """One measurement of `trials` reactions. kind in REACTION_KINDS (ValueError otherwise)."""
    def __init__(self, kind: str, trials: int = REACTION_TRIALS, rng=random.uniform):
        self.kind, self.trials = kind, trials
        self.samples_ms: list[float] = []
        self.false_starts = 0
        self.misses = 0
        self.state = "idle"      # idle | waiting | shown
        self.t_stim: float | None = None
    def next_delay(self) -> float    # state -> "waiting"; returns rng(*REACTION_DELAY_S)
    def stimulus(self, t: float)     # state -> "shown"; t_stim = t
    def press(self, t: float) -> str
    def timeout(self) -> str         # only valid in "shown": misses += 1, state idle, returns "late"
    @property
    def done(self) -> bool           # len(samples_ms) >= trials
    def stats(self) -> dict          # reaction_stats(self.samples_ms)
```
`press(t)`: `idle` → return `"ignored"` (no change). `waiting` → `false_starts += 1`, state idle, return `"early"`. `shown` → `rt = (t - t_stim) * 1000`; `rt < REACTION_EARLY_MS` → `false_starts += 1`, state idle, `"early"`; `rt > REACTION_LATE_MS` → `misses += 1`, state idle, `"late"`; else append `rt`, state idle, `"ok"`. `timeout()` in any other state returns `"ignored"`.

File format — `calibration.json` gains one optional object, written beside the latency keys:
```json
"reaction": {
  "visual": {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1, "n": 10,
             "samples_ms": [...], "press_source": "evdev", "measured_at": "2026-10-12T09:00:00Z"},
  "audio":  {...same keys...}
}
```
```python
def update_reaction(data_root, kind: str, stats: dict, samples_ms: list[float],
                    press_source: str, measured_at: str | None = None) -> Path:
    """Merge reaction[kind] into calibration.json (creating the file with only `reaction` when absent)."""
```
Read the existing JSON (unreadable/non-object → treat as `{}`), set `data.setdefault("reaction", {})[kind] = {...}` (`measured_at` default as in `write_calibration`), write with `indent=2` + newline. `press_source` ∈ `("evdev", "qt")`, `kind` ∈ `REACTION_KINDS`, else `ValueError`.

`write_calibration`: before writing, load the existing file the same tolerant way and, if it has a `"reaction"` key, copy it into `cal` so a latency re-run never discards a reaction measurement. comment: `latency and reaction are measured separately; each write preserves the other`.

`Calibration` dataclass — add three fields (keep order; all have defaults so existing positional constructions still work):
```python
    has_latency: bool = True            # False when the file holds only reaction data
    reaction_visual_ms: float | None = None
    reaction_audio_ms: float | None = None
```
`load`: `has_latency = "latency_median_ms" in data`; `reaction_visual_ms = float(data["reaction"]["visual"]["median_ms"])` when that path exists and is numeric, else None; same for audio. A malformed `reaction` object must not make `load` return None — it only yields None for the two reaction fields.

```python
def reaction_offset_ms(config) -> tuple[float, str]:
    """(R in ms, source): the measured visual reaction when present, else [timing] reaction_offset_ms ("config")."""
```
Source strings: `"measured"` / `"config"`. `Calibration.load(config.data_root)`; measured wins whenever `reaction_visual_ms is not None`.

`controller.py`:
- `_compute_delta`: replace `reaction_ms = float(timing["reaction_offset_ms"])` with `reaction_ms, _source = reaction_offset_ms(self.config)` (import from `.calibration`). Both branches unchanged otherwise.
- `_load_latency` and `calibration_status`: after the `cal is None` checks add `if not cal.has_latency:` → raise / return `(False, f"{CALIBRATION_FILENAME} has no latency — run Calibrate (latency)")`.

`about_screen.py` `environment_rows`: append two rows after "Calibrated latency":
- `("Reaction (visual)", f"{r:.0f} ms ({source})")` from `reaction_offset_ms(config)`.
- `("Reaction (sound)", f"{a:.0f} ms" if a is not None else "not measured")` where `a = cal.reaction_audio_ms` (None when no file).

### Tests

`tests/test_reaction.py` (create; `from hallofframe.calibration import ...`; `from unittest import mock`):
| Test | Steps | Expected |
|---|---|---|
| `test_reaction_stats` | `reaction_stats([200, 210, 220, 230, 240, 250, 260, 270])` | `median_ms == 235`; `iqr_ms == pytest.approx(37.5)`; `mean_ms == 235`; `n == 8` |
| `test_reaction_stats_too_few` | `reaction_stats([200]*5)` | `pytest.raises(ValueError)` |
| `test_run_bad_kind` | `ReactionRun("tactile")` | `pytest.raises(ValueError)` |
| `test_run_ok_press` | `run = ReactionRun("visual", trials=2, rng=lambda a, b: 2.0)`; `run.next_delay() == 2.0` and `run.state == "waiting"`; `run.stimulus(10.0)`; `run.press(10.25)` | returns `"ok"`; `samples_ms == [250.0]`; `state == "idle"`; `done is False`; repeat (`next_delay`, `stimulus(20.0)`, `press(20.3)`) → `done is True`, `samples_ms == [250.0, 300.0]` |
| `test_run_early_while_waiting` | `next_delay()`; `press(1.0)` | `"early"`; `false_starts == 1`; `samples_ms == []`; `state == "idle"` |
| `test_run_early_after_stimulus` | `next_delay()`; `stimulus(5.0)`; `press(5.05)` | `"early"`; `false_starts == 1`; `samples_ms == []` |
| `test_run_late_and_timeout` | `next_delay()`; `stimulus(5.0)`; `press(7.0)` → `"late"`, `misses == 1`; `next_delay()`; `stimulus(9.0)`; `timeout()` → `"late"`, `misses == 2`; `timeout()` again → `"ignored"`; `press(9.5)` → `"ignored"` | as listed |
| `test_update_reaction_creates_and_merges` | `update_reaction(data_root, "visual", {"median_ms": 231.0, "iqr_ms": 28.0, "mean_ms": 236.1, "n": 10}, [1.0]*10, "evdev", measured_at="2026-10-12T09:00:00Z")`; then `update_reaction(data_root, "audio", {"median_ms": 180.0, "iqr_ms": 10.0, "mean_ms": 181.0, "n": 8}, [2.0]*8, "qt")` | file JSON: `reaction.visual.median_ms == 231.0`, `reaction.visual.press_source == "evdev"`, `reaction.visual.measured_at == "2026-10-12T09:00:00Z"`, `reaction.visual.samples_ms == [1.0]*10`; `reaction.audio.median_ms == 180.0`; no `latency_median_ms` key |
| `test_update_reaction_rejects_bad_args` | `update_reaction(data_root, "x", {...}, [], "evdev")`; `update_reaction(data_root, "visual", {...}, [], "mouse")` | `pytest.raises(ValueError)` both |
| `test_write_calibration_preserves_reaction` | `update_reaction(... "visual" ... median 231.0 ...)`; then `write_calibration(data_root, 94.0, 3.0, [94.0]*4, "water", "1440x1080", 30, "", 12345)` | file has `latency_median_ms == 94.0` AND `reaction.visual.median_ms == 231.0` |
| `test_load_reaction_fields` | file written by the previous two calls (`write_calibration` then `update_reaction` visual 231 + audio 180) | `cal.has_latency is True`; `cal.reaction_visual_ms == 231.0`; `cal.reaction_audio_ms == 180.0` |
| `test_load_reaction_only_file` | only `update_reaction(... visual ...)` | `cal is not None`; `cal.has_latency is False`; `cal.latency_median_ms == 0.0`; `cal.reaction_visual_ms == 231.0`; `cal.reaction_audio_ms is None` |
| `test_load_malformed_reaction_ignored` | `calibration_path(data_root).write_text(json.dumps({"latency_median_ms": 94.0, "reaction": "garbage"}))` | `cal.has_latency is True`; both reaction fields `None` |
| `test_reaction_offset_from_config` | `cfg = config(timing={"reaction_offset_ms": 120.0})`; no file | `reaction_offset_ms(cfg) == (120.0, "config")` |
| `test_reaction_offset_measured_wins` | same cfg; `update_reaction(cfg.data_root, "visual", ...231...)` | `(231.0, "measured")` |
| `test_delta_screen_mode_uses_measured` | `cfg = config(timing={"viewing_mode": "screen", "reaction_offset_ms": 120.0})`; `ctl = CaptureController(cfg, storage, buffer)`; `ctl._compute_delta()`; then `update_reaction(... visual 231 ...)`; `ctl._compute_delta()` | `pytest.approx(0.120)` then `pytest.approx(0.231)`; `ctl.stop()` in `finally` |
| `test_delta_water_mode_uses_measured` | cfg water, `reaction_offset_ms` 0; `update_reaction(... visual 231 ...)`; `with mock.patch("hallofframe.controller._load_latency", return_value=94.0): d = ctl._compute_delta()` | `d == pytest.approx(0.137)` |
| `test_status_reaction_only_file` | `update_reaction(... visual ...)` only; `calibration_status(cfg, buffer)` | `(False, "calibration.json has no latency — run Calibrate (latency)")` |
| `test_load_latency_reaction_only_raises` | same file; `_load_latency(cfg, buffer)` | `pytest.raises(CalibrationError)` with `"has no latency"` in the message |
| `test_about_rows_reaction` | `environment_rows(cfg)` with no file; then with visual 231 + audio 180 | contains `("Reaction (visual)", "0 ms (config)")` and `("Reaction (sound)", "not measured")`; then `("Reaction (visual)", "231 ms (measured)")` and `("Reaction (sound)", "180 ms")` |

`tests/test_calibration.py` (append): `test_load_valid_defaults_reaction_none` — the existing `test_load_valid` payload → `cal.has_latency is True`, `cal.reaction_visual_ms is None`, `cal.reaction_audio_ms is None`.

**Commit:** `feat(calibration): reaction-time run + calibration.json reaction fields; Δ uses the measured visual reaction (step 21)`
