# Sheet 12b — review: envelope view of the played clip with a press mark

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/audio.py` (append one function), new `hallofframe/ui/audio_view.py`, `hallofframe/ui/review_screen.py` (`__init__` left column, `_select`, `play_selected`), new `tests/test_audio_view.py`, `tests/test_audio.py` (append), `tests/test_review_audio.py` (append).
**Read first:** `hallofframe/audio.py`, `hallofframe/ui/review_screen.py` (`__init__` left column around `self.scrubber`, `_select`, `_press_elapsed`, `play_selected` from sheet 12), `hallofframe/ui/styles.py` (tokens), `tests/test_review_audio.py` (fixture + arithmetic note), `tests/test_audio.py` (`write_tone_wav`, `rms`).

### Goal
When operator 2 presses `P`, show *what* is being played: the clip's loudness envelope, a red mark where the trigger was pressed, faint ticks where the other crossings of the race fall inside the clip, and a moving playhead. Digit calls in a quiet booth are visible as separate bursts, so a burst of three boats reads as three ticks and three bumps. No word segmentation (that is R3's job). Nothing here touches timing data; the view only renders bytes already read for playback.

### Spec

`audio.py` (append; stdlib only):
```python
def envelope(pcm: bytes, bins: int) -> list[float]:
    """RMS per bin, normalised so the loudest bin is 1.0 (all 0.0 when silent or empty).
    int16 LE samples split into `bins` equal slices (the last takes the remainder)."""
```
`bins <= 0` → `[]`. Use `array("h")` + `math.sqrt` (no `audioop`).

`ui/audio_view.py`:
```python
class AudioView(QWidget):
    """Envelope of the clip last played, a press mark, other-crossing ticks, a playhead."""
    BINS = 200
    def __init__(self, parent=None): ...
        # setFixedHeight(72); setFocusPolicy(Qt.NoFocus)
        # state: self._env: list[float] = []; self._dur_s = 0.0; self._press_s = None; self._others: list[float] = []
        #        self._play_t0 = None (time.monotonic() at set_clip); self._timer = QTimer(self), interval 50 ms, → self.update()
    def set_clip(self, pcm: bytes, dur_s: float, press_s: float, others_s: list[float]) -> None:
        """Store envelope(pcm, BINS) and the marks (seconds into the clip); start the playhead."""
    def clear(self) -> None:
        """Empty state ("no clip played" caption); stops the playhead."""
    def stop_playhead(self) -> None
    def playhead_s(self) -> float | None:
        """Seconds since set_clip, or None when stopped / past dur_s (then the timer is stopped too)."""
```
`paintEvent` (comment: `aplay gives no position feedback, so the playhead is wall-clock from set_clip — approximate on purpose`):
- `setRenderHint(Antialiasing, False)`; background `styles.PANEL`, 1 px border `styles.PANEL_BORDER`.
- Empty state: caption `"no clip played"` centred, `styles.TEXT_FAINT`, 14 px.
- Bars: width `w = width()`, height `h = height()`; bin `i` spans `x0 = i*w//BINS .. x1 = (i+1)*w//BINS`; bar height `round(env[i] * (h - 8))`, drawn bottom-up with `fillRect`, colour `styles.TEXT_SECONDARY`.
- Time → x: `x(t) = round(t / dur_s * (w - 1))` (guard `dur_s > 0`).
- Other-crossing ticks: `fillRect(x(t), 0, 1, h)` in `styles.AMBER` for each `t` in `_others`.
- Press mark: `fillRect(x(press_s), 0, 2, h)` in `styles.RED`. Painted **after** the bars and ticks so it is always on top.
- Playhead: when `playhead_s()` is not None: `fillRect(x(p), 0, 1, h)` in `styles.BLUE`, painted last.

`review_screen.py`:
- Import `from .audio_view import AudioView`.
- `__init__`: `self.audio_view = AudioView()`; `lv.addWidget(self.audio_view)` immediately after `lv.addWidget(self.scrubber)`.
- `_select(sequence)`: first line `self.audio_view.clear()` (a clip of another crossing must never stay on screen).
- `play_selected`, right after `pcm = read_clip(self._audio_path, *bounds)` and **before** `play_clip` (the view must show even when `aplay` is missing):
  ```python
  start_s, dur_s = bounds
  press_in_clip = (elapsed - self._audio_offset) - start_s
  others = []
  for c in self._captures:
      if c["sequence"] == cap["sequence"] or c.get("deleted"):
          continue
      e = self._press_elapsed(c)
      if e is None:
          continue
      p = (e - self._audio_offset) - start_s
      if 0.0 <= p <= dur_s:
          others.append(p)
  self.audio_view.set_clip(pcm, dur_s, press_in_clip, others)
  ```
  (`elapsed` and `bounds` are the locals sheet 12 already computes.)
- No keymap change, no `main_window.py` change, no new config.

### Tests

`tests/test_audio.py` (append):
| Test | Input | Expected |
|---|---|---|
| `test_envelope_normalised` | 1 s silence + 1 s tone (amplitude 10000, built like `write_tone_wav`), `bins=4` | `[0.0, 0.0, 1.0, 1.0]` (±1e-6 on the 1.0s) |
| `test_envelope_empty_and_silent` | `envelope(b"", 5)`; `envelope(bytes(3200), 5)`; `envelope(b"\x01\x00"*100, 0)` | `[0.0]*5`; `[0.0]*5`; `[]` |

`tests/test_audio_view.py` (qt; `pytestmark = pytest.mark.qt`):
Build `pcm`: 1 s silence + 1 s tone as above, `dur_s = 2.0`.
| Test | Steps | Expected |
|---|---|---|
| `test_set_clip_stores_state` | `v = AudioView(); v.set_clip(pcm, 2.0, 1.0, [0.4, 1.7])` | `len(v._env) == AudioView.BINS`; `v._dur_s == 2.0`; `v._press_s == 1.0`; `v._others == [0.4, 1.7]`; `v._timer.isActive()`; `v.playhead_s()` is a float `>= 0.0` |
| `test_clear_stops_playhead` | after `set_clip`: `v.clear()` | `v._env == []`; `not v._timer.isActive()`; `v.playhead_s() is None` |
| `test_press_mark_is_red` | `v.resize(401, 72); v.set_clip(pcm, 2.0, 1.0, []); v.stop_playhead(); img = v.grab().toImage()` | `img.pixelColor(200, 36).name() == styles.RED` (x = round(1.0/2.0*400) = 200; the 2 px mark covers x=200..201) |
| `test_other_tick_is_amber` | same with `others=[0.5]`, press at `1.0` | `img.pixelColor(100, 36).name() == styles.AMBER` |
| `test_empty_paint_smoke` | `v.resize(401, 72); img = v.grab()` | `not img.isNull()` |
(`QColor.name()` returns lowercase `#rrggbb`; compare against `styles.RED.lower()`.)

`tests/test_review_audio.py` (append; same fixture, `play_clip` mocked). Arithmetic: seq 2 press elapsed 2.0, offset 0.5, clip 0.5–2.5 s of the file → press at **1.0 s** into the clip; seq 1 lands at **0.0**, seq 3 at **2.0** (both inside `[0, dur]`).
| Test | Steps | Expected |
|---|---|---|
| `test_p_fills_audio_view` | open review; `screen._select(2)`; key `P` | `screen.audio_view._dur_s == 2.0`; `_press_s == 1.0` (±1e-6); `_others == [0.0, 2.0]` (±1e-6 each); `len(_env) == AudioView.BINS` |
| `test_view_shown_even_without_aplay` | `play_clip` mock returns `None`; `P` | `notify` emitted (aplay message) AND `screen.audio_view._env != []` |
| `test_select_clears_audio_view` | after `P` on seq 2: `screen._select(1)` | `screen.audio_view._env == []` |

**Commit:** `feat(review): audio envelope view with press mark and playhead (§13.3.4 step 12b)`
