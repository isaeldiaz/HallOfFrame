# Sheet 22 — reaction: beep player, stimulus window, `ReactionPage` widget

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/audio.py` (append), `hallofframe/ui/reaction_page.py` (create), `tests/test_audio.py` (append), `tests/test_reaction_page.py` (create).
**Read first:** `hallofframe/calibration.py` (`ReactionRun`, `REACTION_*`, `update_reaction`, `reaction_stats`), `hallofframe/audio.py` (`RATE`, `aplay_argv`, `play_clip`, `player_error`), `hallofframe/ui/calibration_dialog.py` (`FullScreenCounter` — copy its `present`/`_top`/`close` pattern), `hallofframe/ui/styles.py` (`BG`, `TEXT_PRIMARY`), `tests/fakes.py` (`FakeScheduler`, `FakeClock`), `tests/test_audio.py` (`rms`, `test_play_clip_feeds_stdin`), `tests/test_review_audio.py` (fixture style: `qapp`, `processEvents`).

### Goal
The operator-facing reaction test. The page is driven by **injected** clock/scheduler/stimulus/beep so the full trial loop runs in tests with no display and no `aplay`. Presses arrive through `on_press(t)` — in the app that is the evdev kernel timestamp (sheet 23); in Qt fallback the stimulus window's own key event (`time.monotonic()`), flagged `press_source="qt"`.

Stimulus timing: visual → `time.monotonic()` taken right after a synchronous `repaint()` (one vsync of display lag remains inside the measured R; it is the same lag a screen-watching operator lives with). Sound → `aplay` is spawned at the start of each trial (device open, blocked on stdin) and the beep bytes are written at the stimulus; `t_stim` is taken immediately before the write.

### Spec

`audio.py` (append):
```python
def tone_pcm(freq_hz: float = 1000.0, dur_s: float = 0.1, amplitude: int = 12000,
             rate: int = RATE) -> bytes:
    """int16 LE mono sine; round(dur_s*rate) samples."""

class BeepPlayer:
    """One aplay per beep: prepare() spawns it (device open, waiting on stdin), fire() writes the tone."""
    def __init__(self, device: str = "default", *, popen=subprocess.Popen,
                 clock=time.monotonic, pcm: bytes | None = None):   # pcm default tone_pcm()
    def prepare(self) -> bool     # close() any previous; Popen(aplay_argv(device=device), stdin=PIPE, stdout=DEVNULL, stderr=PIPE); OSError → False
    def fire(self) -> float | None  # None if not prepared; t = clock(); stdin.write(pcm); stdin.close(); return t
    def error(self) -> str | None  # proc exited with rc != 0 → player_error(proc), else None
    def close(self) -> None       # terminate if still running (wait 0.5 s, then kill), close stdin/stderr
```
`fire()` writes inline (≤ 3200 bytes, far below the pipe buffer, never blocks). `BrokenPipeError`/`OSError` on write → return None.

`ui/reaction_page.py`:
```python
class StimulusWindow(QWidget):
    """Full-screen black window; show_stimulus() turns it white with "GO"."""
    qt_press = Signal(float)   # Space/Return keyPressEvent → time.monotonic() (Qt fallback only)
    def present(self) -> None   # showFullScreen + raise + keep-top timer (as FullScreenCounter.present)
    def blank(self) -> None     # black, label "wait…" (grey), repaint()
    def show_stimulus(self) -> float  # white background, big black "GO", repaint(); return time.monotonic()
    def close(self) -> bool     # stop the keep-top timer, super().close()
```
Only `Qt.Key_Space`/`Qt.Key_Return`/`Qt.Key_Enter` emit `qt_press`; the window has `Qt.StrongFocus`.

```python
class ReactionPage(QWidget):
    run_started = Signal()         # the dialog hides itself
    run_finished = Signal()        # the dialog shows itself again
    saved = Signal(str, dict)      # (kind, stats) after update_reaction
    notify = Signal(str)           # one-line messages (toast)
    def __init__(self, data_root, voice: dict, *, evdev: bool,
                 clock=time.monotonic, schedule=None, run_factory=ReactionRun,
                 stimulus_factory=StimulusWindow, beep_factory=None, parent=None)
    def on_press(self, t: float) -> None
    def start(self) -> None
    def abort(self) -> None        # Esc / dialog closed mid-run: cancel pending, close stimulus/beep, run_finished
```
- `schedule(delay_s, fn)` must return a handle with `.cancel()`; default builds one over `QTimer.singleShot` (a small `_QtScheduled` wrapper with a `QTimer` instance so `cancel()` is `timer.stop()`).
- `beep_factory` default: `lambda: BeepPlayer(str(voice["output"]))`.
- Widgets: `kind_visual`/`kind_audio` `QRadioButton` ("Visual — finish line", "Sound — gun start", visual checked), `start_btn` ("Start 10 trials"), `status` QLabel, `result` QLabel, `save_btn` ("Save to calibration.json", disabled until a run completes), a hint label (3 lines: press the trigger button as soon as the screen turns white / the beep sounds; too early = false start, the trial repeats; Esc aborts). All buttons `Qt.NoFocus` (comment: `Space must never "click" a focused button during a run`).
- `evdev=False`: stimulus window's `qt_press` → `on_press`; `press_source = "qt"`. `evdev=True`: Qt keys ignored; `press_source = "evdev"`.

Trial loop (all on the GUI thread):
```
start():  self.run = run_factory(kind); self.stimulus = stimulus_factory(); self.beep = beep_factory() if kind == "audio" else None
          run_started.emit(); stimulus.present(); (connect qt_press when not evdev); _schedule_trial()
_schedule_trial(): if run.done → _finish(); return
          if beep and not beep.prepare(): notify("aplay not available — install alsa-utils"); abort(); return
          stimulus.blank(); status = f"Trial {len(samples)+1}/{trials} — wait…"
          self._pending = schedule(run.next_delay(), self._show_stimulus)
_show_stimulus(): t = beep.fire() if beep else stimulus.show_stimulus()
          if t is None: notify(beep.error() or "beep failed"); abort(); return
          run.stimulus(t); self._pending = schedule(REACTION_LATE_MS/1000, self._on_timeout)
on_press(t): if run is None or run.state == "idle": return
          cancel pending; r = run.press(t)
          "ok" → status f"{samples[-1]:.0f} ms"; "early" → "False start — again"; "late" → "Missed — again"
          stimulus.blank(); self._pending = schedule(0.8, self._schedule_trial)
_on_timeout(): run.timeout(); status "Missed — again"; stimulus.blank(); schedule(0.8, _schedule_trial)
_finish(): close stimulus/beep; run_finished.emit(); self.stats = run.stats()
          result = f"median {median:.0f} ms · IQR {iqr:.0f} ms · n={n} · false starts {fs} · misses {m}"; save_btn enabled
save:     update_reaction(data_root, kind, stats, run.samples_ms, press_source); saved.emit(kind, stats); notify("Saved …")
```
`kind` string for the file: `"visual"` / `"audio"`.

### Tests

`tests/test_audio.py` (append):
| Test | Steps | Expected |
|---|---|---|
| `test_tone_pcm_shape` | `pcm = tone_pcm(1000.0, 0.1)` | `len(pcm) == 3200`; `rms(pcm) == pytest.approx(12000/math.sqrt(2), rel=0.02)` |
| `test_beep_player_prepare_fire` | `FakeProc` with `stdin = io.BytesIO()` (override `close` to set a flag), `stderr=None`, `poll()→None`, `returncode=None`, `terminate()`/`wait()` recorded; `popen` records argv and returns it; `clock = FakeClock(5.0)`; `bp = BeepPlayer("plughw:1,0", popen=popen, clock=clock, pcm=b"\x01\x02"*4)` | `bp.prepare() is True`; argv `== aplay_argv(device="plughw:1,0")`; `bp.fire() == 5.0`; `stdin.getvalue() == b"\x01\x02"*4`; stdin closed; `bp.close()` → `terminate` called once |
| `test_beep_player_oserror` | `popen` raises `OSError` | `prepare() is False`; `fire() is None` |

`tests/test_reaction_page.py` (create; `pytestmark = pytest.mark.qt`; `pytest.importorskip("PySide6")`). Fakes inside the test file: `FakeStimulus(QObject)` with `qt_press = Signal(float)`, counters `presented/blanks/shown/closed`, `show_stimulus()` returns `clock()`; `FakeBeep` with `prepared/fired` counters, `fire()` returns `clock()`, `error()` None. Build: `sched = FakeScheduler(); clock = FakeClock(100.0); page = ReactionPage(data_root, {"output": "default"}, evdev=True, clock=clock, schedule=sched, run_factory=lambda k: ReactionRun(k, trials=3, rng=lambda a, b: 2.0), stimulus_factory=lambda: stim, beep_factory=lambda: beep)`. Keep `clock` and `sched` in step: advance both together via a helper `tick(s)` that calls `clock.advance(s)` then `sched.advance(s)`.
| Test | Steps | Expected |
|---|---|---|
| `test_visual_run_completes_and_saves` | spy `run_started`/`run_finished`/`saved`; `page.start()`; for 3 trials: `tick(2.0)` (stimulus shown), `page.on_press(clock() + 0.25)`, `tick(0.8)` | `stim.presented == 1`; after loop `run_finished` emitted once; `page.result.text()` contains `"median 250 ms"` and `"n=3"`; `save_btn.isEnabled()`; click save → `Calibration.load(data_root).reaction_visual_ms == 250.0`; file `press_source == "evdev"`; `saved` emitted with `("visual", stats)` |
| `test_false_start_repeats_trial` | `page.start()`; `tick(1.0)`; `page.on_press(clock())` | `status.text() == "False start — again"`; `page.run.false_starts == 1`; `page.run.samples_ms == []`; `tick(0.8)` → a new stimulus is pending (`stim.blanks` increased, `page.run.state == "waiting"`) |
| `test_timeout_counts_miss` | `page.start()`; `tick(2.0)`; `tick(1.5)` | `page.run.misses == 1`; `status.text() == "Missed — again"` |
| `test_press_before_start_ignored` | no `start()`; `page.on_press(1.0)` | no exception; `page.run is None`; `status.text()` unchanged |
| `test_audio_kind_uses_beep` | `page.kind_audio.setChecked(True)`; `page.start()`; `tick(2.0)` | `beep.prepared == 1`; `beep.fired == 1`; `stim.shown == 0`; complete the 3 trials → save writes `reaction.audio.median_ms == 250.0` |
| `test_qt_fallback_uses_stimulus_keys` | `evdev=False`; `page.start()`; `tick(2.0)`; `clock.advance(0.3)`; `stim.qt_press.emit(clock())` | `page.run.samples_ms == [300.0]`; after finishing + save, file `press_source == "qt"` |
| `test_evdev_ignores_qt_keys` | `evdev=True`; `page.start()`; `tick(2.0)`; `stim.qt_press.emit(clock() + 0.3)` | `page.run.samples_ms == []`; `page.run.state == "shown"` |
| `test_abort_closes_everything` | `page.start()`; `tick(1.0)`; `page.abort()` | `stim.closed == 1`; `run_finished` emitted once; `page.run is None`; `tick(5.0)` fires nothing (`stim.shown == 0`) |
| `test_beep_unavailable_aborts` | `FakeBeep.prepare()` returns False; audio kind; spy `notify`; `page.start()` | `notify` got `"aplay not available — install alsa-utils"`; `run_finished` emitted; `page.run is None` |

**Commit:** `feat(calibration): reaction page — visual/sound stimulus, trial loop, save to calibration.json (step 22)`
