# Sheet 11 — controller + `build_core`: record one WAV per race

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/controller.py`, `hallofframe/main.py`, new `tests/test_controller_audio.py`.
**Read first:** `controller.py` (`start_race`, `end_race`, `resume_race`, `stop`, `enqueue`, `_warn`), `audio.py`, `main.py` `build_core`, `tests/test_controller_scratch.py` (fixture), `tests/test_e2e.py::test_build_core_prefers_the_calibrated_fps` (build_core test pattern).

### Spec
`controller.py`:
```python
# __init__: self.recorder = None; self._audio_t0 = None
def attach_recorder(self, recorder) -> None:
    recorder.on_ready = self._audio_ready
    recorder.on_error = self._warn
    self.recorder = recorder
def _audio_ready(self, race_id: int, path, t_start_mono: float) -> None:
    """Capture-thread callback → DB write on the writer thread.
    offset = t_start − t0: seconds from the gun to the first sample (Storage.set_race_audio's definition).
    comment: t_start is estimated from the first block's arrival (±0.2 s with arecord/PipeWire); replay only, never timing data."""
    rel = Path(path).relative_to(self.storage.data_root).as_posix()
    t0 = self._audio_t0
    self.enqueue(lambda: self.storage.set_race_audio(race_id, rel, t_start_mono - t0))
```
- `start_race`: after `self.store = FrameStore(...)`: `if self.recorder is not None: self._audio_t0 = self.t0; self.recorder.start(race_id, self.race_dir / AUDIO_FILENAME)`.
- `end_race`: after `mark_race_ended`: `if self.recorder is not None: self.recorder.stop()`. (A `set_race_audio` job enqueued after the drain just runs later; harmless.)
- `stop()`: `if self.recorder is not None: self.recorder.close()` before the queue stop.
- `resume_race`: do NOT start the recorder (comment: a reconstructed t0 has no audio position); log `audio_not_resumed` if logger.

`main.py`:
- `build_core`, after `controller = CaptureController(...)`:
  ```python
  voice = config.section("voice")
  if bool(voice["enabled"]):
      from .audio import ArecordSource, AudioRecorder
      controller.attach_recorder(AudioRecorder(lambda: ArecordSource(str(voice["input"]))))
  ```
- `warning` events now also come from the audio capture thread, so route them through the bridge like the other worker events: `_TriggerBridge.warning = Signal(str)`; `bridge.warning.connect(win._show_toast)`; in `_on_controller_event`: `elif kind == "warning": bridge.warning.emit(payload["message"])` (replacing the direct `win._show_toast` call).

### Tests — `tests/test_controller_audio.py`
Recorder: `AudioRecorder(lambda: FakeSource(blocks), clock=FakeClock(1000.3))` with `from test_audio import FakeSource` (tests/ is on `sys.path` under pytest; `from fakes import …` already works the same way). `attach_recorder` **overwrites** `recorder.on_ready`/`on_error`, so tests must not assign spies to them; observe through storage / the `events` list instead.

| Test | Steps | Expected |
|---|---|---|
| `test_start_race_records_and_writes_offset` | attach; `start_race(1000.0, name="Race")`; poll ≤2 s: `_queue.join()` then `get_race(rid)["audio_path"] is not None`; `end_race()` | `audio_path == "races/0001_Race/audio.wav"`; `abs(audio_t0_offset_s - 0.2) < 1e-6`; file exists; `recorder.recording is False` after end |
| `test_audio_failure_is_a_warning` | source `open()` raises `FileNotFoundError("arecord")` | `start_race` returns normally; within 2 s one `("warning", {...})` event whose message contains `"audio"`; `audio_path` NULL; `record_crossing` + `_queue.join()` still persists a capture |
| `test_no_recorder_no_op` | default controller | `audio_path` NULL after a race |
| `test_resume_does_not_record` | `resume_race(rid)` with recorder attached | `recorder.recording is False` |
| `test_build_core_attaches_recorder` | `config(transport={"enabled": False}, voice={"enabled": True})`; `try/finally: core["controller"].stop(); core["storage"].close()` | `core["controller"].recorder is not None` and not recording; default config → `recorder is None` |

**Commit:** `feat(controller): per-race audio recording via AudioRecorder (§13.3.4 step 11)`
