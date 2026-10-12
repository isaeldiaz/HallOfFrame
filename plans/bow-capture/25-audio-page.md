# Sheet 25 — audio tab: devices, mixer sliders, playback filters, live preview, Accept → settings.json

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/ui/audio_page.py` (create), `hallofframe/ui/calibration_dialog.py` (`CalibrationDialog.__init__`, `closeEvent` only), `hallofframe/main.py` (`build_core`: the recorder source lambda only), `hallofframe/ui/review_screen.py` (`_voice` only), `tests/test_audio_page.py` (create).
**Read first:** `hallofframe/audio.py` (`list_pcms`, `mixer_controls`, `set_mixer`, `LevelMeter`, `ArecordSource`, `play_clip`, `process_clip`, `tone_pcm`, `BLOCK_BYTES`), `hallofframe/config.py` (`Config.section`, `write_overrides`), `hallofframe/ui/calibration_dialog.py` (`CalibrationDialog`), `hallofframe/ui/reaction_page.py` (constructor/injection style), `hallofframe/ui/audio_view.py` (`AudioView.set_waveform` + `set_marks` — reuse it to show the loopback envelope), `hallofframe/ui/review_screen.py` (`self._voice = …` near line 239, `play_selected`, `_poll_player`), `hallofframe/main.py` `build_core` (the `voice` block), `tests/test_reaction_page.py` (fake/injection style).

### Goal
Third calibration tab, "Audio": pick the input and output PCM, see and move the card's mixer controls (changes apply immediately through `amixer`), tune the review playback filters, **hear** the result (live mic meter, 3-second record-and-play loopback through the filter chain, test tone on the output), and Accept to write every value to `settings.json` (`[voice]` overrides). Nothing here touches the recorded WAV path, timing, or the trigger thread; the meter runs on its own thread and reaches widgets only through Qt signals.

### Spec

`ui/audio_page.py`:
```python
class AudioPage(QWidget):
    level = Signal(float)          # worker → GUI (LevelMeter.on_level)
    block = Signal(bytes)          # worker → GUI (LevelMeter.on_block)
    meter_error = Signal(str)
    notify = Signal(str)
    saved = Signal(dict)           # the dict written to settings.json
    def __init__(self, config, *, list_pcms=audio.list_pcms, mixer_controls=audio.mixer_controls,
                 set_mixer=audio.set_mixer, meter_factory=audio.LevelMeter,
                 source_factory=audio.ArecordSource, play=audio.play_clip,
                 extra_inputs: list[tuple[str, str]] = (), parent=None)
    def stop(self) -> None         # stop meter + player; idempotent (dialog close)
    def refresh(self) -> None      # re-list PCMs and mixer controls
    def values(self) -> dict       # the dict Accept writes
```
Widgets (object names in parentheses; tests find them by attribute):
- `input_combo`, `output_combo` (`QComboBox`): items from `list_pcms("input") + list(extra_inputs)` / `list_pcms("output")`, text `f"{name} — {desc}"`, `itemData = name`. Preselect the current `voice["input"]`/`voice["output"]`; if absent, add it as the first item with desc "(current)".
- `card_edit` (`QLineEdit`, prefilled `voice["mixer_card"]`), `refresh_btn`.
- `mixer_box` (`QGroupBox` "Mixer"): one row per `mixer_controls(card)` entry with `percent is not None`: label `f"{name} ({'capture' if capture else 'playback'})"` + `QSlider(Qt.Horizontal, 0..100)` initialised to `percent`. Store as `self.sliders: dict[str, QSlider]`. `valueChanged` → restart a 150 ms single-shot `QTimer` per control; on timeout `set_mixer(card, name, f"{v}%")`; a returned message → `notify.emit(f"audio mixer: {msg}")`. comment: `debounced so a drag does not spawn an amixer per pixel`.
- `gain_spin` (`QDoubleSpinBox` −20..20 dB, step 1, from `voice["gain_db"]`), `highpass_spin` (`QSpinBox` 0..500 Hz, step 10, from `voice["highpass_hz"]`), `mute_spin` (`QSpinBox` 0..500 ms, step 10, from `voice["press_mute_ms"]`).
- Preview: `meter_bar` (`QProgressBar` 0..100, text off), `listen_btn` (checkable "Listen"), `loop_btn` ("Record 3 s and play back"), `tone_btn` ("Test tone"), `audio_view = AudioView()` (shows the last loopback clip, press mark at 0).
- `accept_btn` ("Accept — write settings.json").

Behaviour:
- `listen_btn` checked → `self.meter = meter_factory(lambda: source_factory(self.input_name), self.level.emit, on_error=self.meter_error.emit, on_block=self.block.emit); meter.start()`. Unchecked → `meter.stop()`. `level` slot: `meter_bar.setValue(min(100, int(rms / 327.67)))` (int16 full scale = 100). `meter_error` slot: uncheck, `notify.emit(msg)`.
- `loop_btn`: starts the meter if not running, collects `block` payloads for 30 blocks (3 s), then stops the meter if it was not running before, builds `pcm = process_clip(b"".join(blocks), gain_db=gain, highpass_hz=hp)`, `audio_view.set_waveform(pcm, 3.0); audio_view.set_marks(0.0, [])`, `self.player = play(pcm, device=self.output_name)`; `None` → `notify("aplay not available — install alsa-utils")`. Button disabled while collecting; label "Recording… 3 s".
- `tone_btn`: `play(tone_pcm(1000.0, 1.0), device=self.output_name)`.
- A 100 ms `QTimer` polls `self.player` like `ReviewScreen._poll_player` (reuse `player_error`; close pipes); a non-zero exit → `notify`.
- `values()` →
  ```python
  {"input": input_name, "output": output_name, "mixer_card": card_edit.text().strip(),
   "mixer": [f"{name} {slider.value()}%" for name, slider in self.sliders.items()],
   "gain_db": float(gain_spin.value()), "highpass_hz": float(highpass_spin.value()),
   "press_mute_ms": int(mute_spin.value())}
  ```
- `accept_btn` → `config.write_overrides("voice", self.values())`; `saved.emit(values)`; `notify.emit("Audio settings written to settings.json")`. The `mixer` list is what `build_core` re-applies at the next start-up; the sliders already applied it live.
- `input_name`/`output_name` properties read `currentData()`.

`calibration_dialog.py`: third tab `self.audio = AudioPage(config)` ("Audio"); `audio.notify → self.notify`; `closeEvent` also calls `self.audio.stop()`.

`main.py` `build_core`: the recorder source factory reads the section at call time so a value accepted in the dialog is used by the next race without restart:
```python
AudioRecorder(lambda: ArecordSource(str(config.section("voice")["input"])))
```
`review_screen.py`: delete the `self._voice = controller.config.section("voice")` assignment and add
```python
@property
def _voice(self) -> dict:
    return self.controller.config.section("voice")
```
(the existing reads of `self._voice[...]` keep working; `play_btn.setVisible(bool(self._voice["enabled"]))` is evaluated at construction as before).

### Tests

`tests/test_audio_page.py` (create; `qt` marker). Fakes: `FakeMeter(source_factory, on_level, on_error=None, on_block=None)` records the callbacks, `start()` sets `running=True`, `stop()` clears it, test helper `feed(rms, block)` calls them; `fake_play(pcm, device=...)` records `(len(pcm), device)` and returns `None`-poll `FakeProc(returncode=0, poll()→0, stdin/stderr None)`. `list_pcms` fake: input `[("default","System default"),("plughw:CARD=Headset,DEV=0","Logitech")]`, output `[("default","System default"),("plughw:CARD=PCH,DEV=0","HDA Intel PCH")]`. `mixer_controls` fake returns the 3 entries from sheet 24's expected list. `set_mixer` fake records calls, returns `None` (or `"Nope 50%: boom"` when asked). `cfg = config(voice={"enabled": True, "input": "plughw:CARD=Headset,DEV=0", "output": "default", "mixer_card": "0", "gain_db": 0.0, "highpass_hz": 0.0, "press_mute_ms": 0})`.
| Test | Steps | Expected |
|---|---|---|
| `test_populates_devices_and_mixer` | build page | `input_combo.count() == 2`, `input_combo.currentData() == "plughw:CARD=Headset,DEV=0"`; `output_combo.currentData() == "default"`; `set(page.sliders) == {"Master", "Capture", "Mic Boost"}`; `sliders["Capture"].value() == 60` |
| `test_unknown_current_input_added` | cfg voice input `"hw:7,0"` | `input_combo.itemData(0) == "hw:7,0"`; `currentData() == "hw:7,0"`; text ends with `"(current)"` |
| `test_extra_inputs_listed` | `extra_inputs=[("http://127.0.0.1:8081/audio.wav", "iPhone microphone")]` | last input item data `== "http://127.0.0.1:8081/audio.wav"` |
| `test_slider_applies_debounced` | `sliders["Capture"].setValue(70)`; `processEvents()`; wait ≤ 0.5 s for the set_mixer fake to be called (`QTest.qWait(200)` or a loop) | exactly one call `("0", "Capture", "70%")` |
| `test_slider_error_toasts` | set_mixer fake returns `"Capture 70%: boom"`; spy `notify`; move slider; wait | notify got `"audio mixer: Capture 70%: boom"` |
| `test_listen_starts_meter_and_updates_bar` | `listen_btn.click()`; `meter.feed(16383.5, b"")`; `processEvents()` | `meter.running`; `meter_bar.value() == 50`; `listen_btn.click()` → `meter.running is False` |
| `test_meter_error_unchecks` | `listen_btn.click()`; `meter.on_error("audio: boom")`; `processEvents()` | `listen_btn.isChecked() is False`; notify got `"audio: boom"` |
| `test_loopback_records_then_plays_filtered` | `gain_spin.setValue(6.0206)`; `loop_btn.click()`; `meter.feed(1000.0, b"\xe8\x03" * 1600)` ×30 (sample 1000); `processEvents()` | `play` called once with `len == 96000` and `device == "default"`; played pcm samples all `== 2000` (gain applied); `meter.running is False` (was not listening before); `loop_btn.isEnabled()` again |
| `test_tone_plays_on_output` | `output_combo.setCurrentIndex(1)`; `tone_btn.click()` | `play` called with `(32000, "plughw:CARD=PCH,DEV=0")` |
| `test_accept_writes_settings` | `sliders["Capture"].setValue(70)`; `highpass_spin.setValue(150)`; `mute_spin.setValue(100)`; `output_combo.setCurrentIndex(1)`; `accept_btn.click()` | `json.load(cfg.settings_path)["voice"] == {"input": "plughw:CARD=Headset,DEV=0", "output": "plughw:CARD=PCH,DEV=0", "mixer_card": "0", "mixer": ["Master 100%", "Capture 70%", "Mic Boost 0%"], "gain_db": 0.0, "highpass_hz": 150.0, "press_mute_ms": 100}`; `cfg.section("voice")["output"] == "plughw:CARD=PCH,DEV=0"`; `saved` emitted once |
| `test_dialog_has_audio_tab_and_stops_meter` | `CalibrationDialog(buffer, data_root, cfg, evdev=False)` with the page's `meter_factory` patched via `mock.patch("hallofframe.ui.audio_page.audio.LevelMeter", FakeMeter)`; `dlg.audio.listen_btn.click()`; `dlg.close()` | tab texts `== ["Latency", "Reaction", "Audio"]`; meter stopped |
| `test_review_voice_is_live` | from `tests/test_review_audio.py` style env: open review, then `controller.config.write_overrides("voice", {"gain_db": 12.0})` | `screen._voice["gain_db"] == 12.0` |
| `test_build_core_source_reads_live_config` | `cfg = config(transport={"enabled": False}, voice={"enabled": True, "input": "a"})`; `core = build_core(cfg)`; `cfg.write_overrides("voice", {"input": "b"})`; with `mock.patch("hallofframe.main.ArecordSource")` — note the lambda imports `ArecordSource` inside `build_core`, so patch `hallofframe.audio.ArecordSource` **before** calling `build_core`; call `core["controller"].recorder._source_factory()` | patched class called with `"b"`; teardown as `test_build_core_attaches_recorder` |

**Commit:** `feat(calibration): Audio tab — PCM pick, live mixer sliders, playback filters, preview, Accept → settings.json (step 25)`
