# Sheet 12 — review: play the clip around the selected crossing (`P`)

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/ui/review_screen.py`, `hallofframe/session.py`, `hallofframe/ui/main_window.py`, new `tests/test_review_audio.py`, `tests/test_session.py` (append one test).
**Read first:** `review_screen.py` (`__init__` header row, `load_captures`, `keyPressEvent`, `_selected_capture`, `notify`), `session.py` `_REVIEW_KEYS`, `main_window.py` `_review_time_from_press` (forwarder pattern), `audio.py` (`clip_bounds`, `read_clip`, `wav_duration_s`, `play_clip`), `tests/test_review_screen.py` lines 42–110.

### Spec
`review_screen.py`:
- Module-level import (required so tests can `mock.patch` it): `from ..audio import clip_bounds, play_clip, read_clip, wav_duration_s`.
- `__init__`: `self._voice = controller.config.section("voice")`; `self._audio_path = None; self._audio_offset = None; self._player = None`; `self.play_btn = QPushButton("Play audio (P)")`, `Qt.NoFocus`, `clicked → self.play_selected`, added to `header` right after `edit_btn`.
- `load_captures`: `row` may be `None` (mirror the existing `if row else None` pattern). `_audio_path = self.data_root / row["audio_path"]` when the row exists, the column is non-NULL and the file exists, else None; `_audio_offset = row["audio_t0_offset_s"] if row else None`; `play_btn.setEnabled(self._audio_path is not None)`; tooltip `"no recording for this race"` when disabled.
- `keyPressEvent`: `Qt.Key_P` with no modifiers → `self.play_selected(); return` (next to the `U` branch, after the focused-`QLineEdit` early return).
```python
def play_selected(self) -> None:
    """Play [press − play_before_s, press + play_after_s] of the race audio."""
    cap = self._selected_capture()
    if cap is None: return
    if self._audio_path is None or self._audio_offset is None:      # `is None`: _t0_mono can legitimately be 0.0
        self.notify.emit("No audio recording for this race"); return
    if cap.get("t0_reconstructed"):
        self.notify.emit("No audio for crossings recorded after a restart"); return
    elapsed = self._press_elapsed(cap)      # boot-independent press elapsed (existing helper, N4-safe)
    if elapsed is None:
        self.notify.emit("No audio recording for this race"); return
    bounds = clip_bounds(elapsed, 0.0, self._audio_offset,
                         float(self._voice["play_before_s"]), float(self._voice["play_after_s"]),
                         wav_duration_s(self._audio_path))
    if bounds is None:
        self.notify.emit("Crossing is outside the recording"); return
    pcm = read_clip(self._audio_path, *bounds)
    if self._player is not None and self._player.poll() is None:
        self._player.kill()
    self._player = play_clip(pcm)
    if self._player is None:
        self.notify.emit("aplay not available — install alsa-utils")
```
`session.py` `_REVIEW_KEYS`: after the `0` entry add `_k("P", "Play audio", "_review_play", shortcut=False)`.
`main_window.py`: `def _review_play(self): if self._review_screen is not None: self._review_screen.play_selected()`.

### Tests — `tests/test_review_audio.py` (qt; fixture copied from `test_review_screen.py` `review_screen_env`: race `t0=0.0`, captures `t_press = seq`; add `voice={"play_before_s": 1.0, "play_after_s": 1.0}`)
Write a tone WAV (`from test_audio import write_tone_wav, rms`) at `data_root/races/0001_R/audio.wav`, `total_s=5.0`, burst 1.5–1.7 s; `storage.set_race_audio(race_id, "races/0001_R/audio.wav", 0.5)` in the fixture; `mock.patch("hallofframe.ui.review_screen.play_clip")`.
Arithmetic for the first test: press elapsed 2.0, offset 0.5 → pos 1.5 in the file; `before=1.0` → clip starts at file-time 0.5; the burst (file 1.5–1.7) is therefore at **1.0–1.2 s into the clip**.

| Test | Steps | Expected |
|---|---|---|
| `test_p_plays_clip_around_press` | open review; `screen._select(2)`; key `P` | `play_clip` called once; `len(pcm) == 2*RATE*2`; `rms(pcm[32000:38400]) > 5000` (1.0–1.2 s); `rms(pcm[0:28800]) == 0` (0–0.9 s); `play_btn.isEnabled()` |
| `test_no_audio_notifies` | `storage.set_race_audio(race_id, None, None)` before opening review | `notify` emitted; `play_clip` not called; button disabled |
| `test_second_press_kills_previous` | mock returns obj with `poll()→None`, `kill` | second `P` calls `kill` once |
| `test_session_has_play_cap` (in `test_session.py`) | — | `P` in `KEYMAP[AppState.REVIEW]` with `shortcut False`; absent from other states |

**Commit:** `feat(review): play race audio around a crossing with P (§13.3.4 step 12)`
