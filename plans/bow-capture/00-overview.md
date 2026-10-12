# Bow-number capture — task sheets

One sheet = one agent run. Do them in numeric order; each assumes the previous ones are merged on `main`.

## Rules for every sheet (also repeated at the top of each sheet)

1. Read `AGENTS.md`, then your sheet, then ONLY the files the sheet lists under "Read first". Do not read the spec or other sheets.
2. Edit ONLY the files listed under "Files you may edit". Need another file changed? Write `plans/bow-capture/BLOCKED-<nn>.md` (3 lines: what, why, which file) and stop.
3. No new dependencies, no new config keys, no new tables/columns beyond what your sheet states verbatim.
4. Never change an expected value in a test to make it pass. A failing test is reported, not silenced.
5. Nothing you write runs on the trigger thread; nothing opens its own SQLite connection (use `Storage` methods only).
6. Before you start and before you finish, run the fast suite: on the Linux laptop `~/regatta/venv/bin/python -m pytest -m "not slow"`; on the Windows dev host `python -m pytest -m "not slow"` (needs `pytest`, `PySide6-Essentials`, `pillow` installed). On Windows exactly 7 tests fail before you touch anything (5× `test_goldens.py` CRLF, `test_config.py::test_data_root_expands_tilde`, `test_web_thumbs.py::test_unwritable_cache_still_serves`); those are host artifacts. "Green" means: no failure that was not already failing before your change.
7. Done = every test named in your sheet passes + the fast suite is green per rule 6 + one commit with the message given in the sheet.
8. Keep docstrings short; one-line comments only where the sheet says "comment:".

## Map

| # | Sheet | Files created | Files edited |
|---|---|---|---|
| 01 | storage + config | tests/test_storage_scratch.py | storage.py, config.py |
| 02 | scratchpad rules (pure) | scratchpad.py, tests/test_scratchpad.py | — |
| 03 | controller | tests/test_controller_scratch.py | controller.py |
| 04 | trigger device identity | — | trigger.py, tests/test_trigger.py |
| 05 | scratchpad widget | ui/scratchpad_widget.py, tests/test_scratchpad_widget.py | — |
| 06 | race screen bow column | tests/test_race_screen_scratch.py | ui/crossing_list.py, ui/race_screen.py |
| 07 | wiring + arm gate | tests/test_main_window_scratch.py | ui/main_window.py, main.py |
| 08 | docs R1 | — | spec, AGENTS.md, TESTING.md, hallofframe.example.toml |
| 09 | audio: clip maths + WAV sink | audio.py, tests/test_audio.py | — |
| 10 | audio: recorder thread, arecord source, aplay | — | audio.py, tests/test_audio.py |
| 11 | audio: controller + build_core | tests/test_controller_audio.py | controller.py, main.py |
| 12 | audio: review play (P) | tests/test_review_audio.py | ui/review_screen.py, session.py, ui/main_window.py, tests/test_session.py |
| 12b | audio: envelope view + press mark in review | ui/audio_view.py, tests/test_audio_view.py | audio.py, ui/review_screen.py, tests/test_audio.py, tests/test_review_audio.py |
| 12c | audio quality: `[voice] mixer` at start-up, playback high-pass/gain/press-mute | — | audio.py, config.py, main.py, ui/review_screen.py, tests/test_audio.py, tests/test_controller_audio.py, tests/test_review_audio.py, example toml, spec, AGENTS.md, TESTING.md |
| 13 | docs R2 + INSTALL | — | INSTALL.md, hallofframe.example.toml, spec, AGENTS.md, TESTING.md |
| 14 | asr: config keys + Norwegian number words | asr.py, tests/test_asr.py | config.py, hallofframe.example.toml, tests/test_config.py |
| 15 | asr: whisper JSON parse + utterance grouping | — | asr.py, tests/test_asr.py |
| 16 | asr: storage suggestions + command builder + runners | — | storage.py, asr.py, tests/fakes.py, tests/test_asr.py, tests/test_storage.py |
| 17 | asr: assign + Transcriber | — | asr.py, tests/test_asr.py |
| 18 | asr: `source='asr'` + suggestion column | — | controller.py, ui/crossing_list.py, tests/test_controller_scratch.py, tests/test_crossing_list.py |
| 19 | asr: review Transcribe (T) / Accept (A), cancel on arm | tests/test_review_asr.py | ui/review_screen.py, session.py, ui/main_window.py, tests/test_session.py |
| 20 | docs R3 + INSTALL (whisper.cpp, model) | — | INSTALL.md, spec, AGENTS.md, TESTING.md |
| 21 | reaction: `ReactionRun` + stats, `calibration.json` `reaction` object, Δ uses measured visual R, About rows | tests/test_reaction.py | calibration.py, controller.py, ui/about_screen.py, tests/test_calibration.py |
| 22 | reaction: `tone_pcm`/`BeepPlayer`, `StimulusWindow`, `ReactionPage` (injected clock/scheduler) | ui/reaction_page.py, tests/test_reaction_page.py | audio.py, tests/test_audio.py |
| 23 | Calibrate dialog tabs (Latency \| Reaction) + `MainWindow.press_hook` evdev routing | tests/test_main_window_calibrate.py | ui/calibration_dialog.py, ui/main_window.py |
| 24 | audio: `list_pcms`, `mixer_controls`/`set_mixer`, `LevelMeter`; `settings.json` overrides in `Config` | — | audio.py, config.py, tests/test_audio.py, tests/test_config.py |
| 25 | Audio tab: devices, live mixer sliders, playback filters, meter/loopback/tone, Accept → settings.json | ui/audio_page.py, tests/test_audio_page.py | ui/calibration_dialog.py, main.py, ui/review_screen.py |
| 26 | iPhone mic via the tunnel: `HttpWavSource`, `make_source`, `probe_phone_audio` — **gated** on the camera app serving WAV | — | audio.py, main.py, ui/calibration_dialog.py, tests/test_audio.py |
| 27 | docs R4 (§5.5.1 reaction, settings.json, T5b/T15) | — | spec, AGENTS.md, TESTING.md, example toml, INSTALL.md |

## Delivery order and gates (coordinator)

| Release | Steps | Gate before next release |
|---|---|---|
| R1 Scratchpad | 01–08 | full suite green; bench replay of the worked example (sheet 08) |
| R2 Audio replay | 09–12, 12b, 12c, 13 (13 done before 12b/12c; each later sheet carries its own doc lines) | full suite green; one real recording plays in review on the laptop |
| R3 ASR suggestion | 14–20 | full suite green; one real race transcribed on the laptop (INSTALL §2b check) |
| R4 Calibration phases | 21–27 (21→22→23 reaction; 24→25→26 audio; 26 is gated and may be BLOCKED; 27 last) | full suite green; T5b + T15 on the laptop |

Spec §13.3.4 says R2/R3 wait for a real regatta with R1. Sheets are written for all three now; the coordinator decides when to hand out 09+. R4 is independent of R3 and can run right after R2 (it builds on sheet 12c's `[voice]` keys and `process_clip`).

## Verification (coordinator, after each release)


- R1: `pytest -m "not slow"` green on Windows dev host and Linux laptop; `pytest tests/test_goldens.py` unchanged; bench: `./hallofframe-fake.sh` with `[scratchpad] enabled = true`, `[trigger] device_path` = external button, `end_device_path` = laptop keyboard: arm, 4 presses, type `14`, `7`, `3` + Enter → race list shows 14, 7, 3, blank; counters `3 / 4` red; review: type 21 into row 3, 3 into row 4; export shows 14, 7, 21, 3.
- R2: on the laptop with a USB headset: race with speech, `R`, `P` plays the call; `audio_t0_offset_s` is a small positive number. After 12c: `[voice] mixer` lowers the hiss at start-up and `press_mute_ms = 100` silences the button click in playback.
- R3: INSTALL §2b verify commands pass; `T` on the R2 race produces suggestions within the timeout; `A` accepts.
- R4: `C` opens a 3-tab dialog; Reaction visual run with the real trigger button writes `reaction.visual` (`press_source: "evdev"`) and the next race's `delta_used` equals `(R_visual − L)/1000`; sound run beeps on `[voice] output`; Audio tab sliders move `amixer sget` live, loopback is audible, Accept writes `settings.json`, and after a restart review `P` uses the new filters with `config.toml` untouched (TESTING T5b, T15).

## Decisions taken (so nobody re-litigates them in a sheet)

- Sequential steps on `main`; no branches, hooks or OWNERS file. Disjointness is per sheet, not per agent.
- `enqueue(fn)` takes a zero-arg callable; closures capture `self.storage`.
- `bow_source` ∈ {`live`, `manual`, `asr`}; the scratchpad overwrites only rows with `bow_source = 'live'` or `(NULL bow, NULL source)`; a review edit (even clearing to blank) marks the row `manual` and locks it; the recompute runs only while the race runs and once at race end. Spec §13.3.3's `race_active` is the existing `controller.running`.
- Audio: `arecord`/`aplay` subprocesses (no Python audio dependency, no PySide6-Addons); recorder starts at `t0`, no resume, no free-space check, no web serving; offset = `t_first_sample − t0`.
- ASR: single `asr.py`; whisper-cli via injectable `Runner` (`nice` only when present); utterances whose audio time + offset is negative (before the gun) dropped — with the R2 recorder that never happens, the filter is a guard; suggestions never `_touch`; not exported; `[voice] transcribe` enables the button, nothing runs automatically; the worker always emits its finished signal.
- Review `P`/`T`/`A` are screen-handled keys (`shortcut=False`), so a focused bow field still gets the letters.
- **R4 — the app still never writes `config.toml`.** Measured reaction times go into `calibration.json` (`reaction.visual` / `reaction.audio`, with samples and `press_source`); the Audio tab's choices go into a new machine-written `<data_root>/settings.json` that `Config.section` merges over `config.toml` (defaults < toml < settings.json). If the user wants the toml itself rewritten, that is a coordinator decision reversing spec §6.7 (and re-adding `tomli-w`); the sheets are written for the file-override design.
- **R4 — only the visual reaction enters Δ.** `Δ = R_visual − L` (water) / `R_visual` (screen); the measured value replaces `[timing] reaction_offset_ms` when present (it does not add to it). The sound (gun) reaction is stored and shown on About only — elapsed is never corrected for reaction (spec §5.3). Median, not mean, as for latency.
- **R4 — presses during calibration are kernel timestamps.** The dialog opens only outside ARMED/RECORDING, where the trigger device is ungrabbed; `MainWindow.press_hook` diverts `on_evdev_start`/`on_evdev_crossing` to the dialog, and the page ignores the duplicate Qt key event when evdev is active. Qt fallback presses are stored with `press_source: "qt"`.
- **R4 — sound stimulus** is one `aplay` per trial, spawned before the random wait (device open, blocked on stdin) and fed the beep at the stimulus instant; visual stimulus time is `time.monotonic()` after a synchronous `repaint()`.
- **R4 — iPhone mic** is only reachable as an HTTP WAV stream through the iproxy tunnel; sheet 26 starts with a `curl` gate and is BLOCKED (not faked) if the camera app serves no WAV endpoint.

## Background (coordinator)

Spec §13.3.1–13.3.4 describes a two-operator booth: operator 1 presses the trigger and calls the bow number aloud; operator 2 types it into a **scratchpad**; the k-th typed number attaches to the k-th crossing. Later releases add **audio replay** (one WAV per race, play button in review) and **ASR suggestions** (offline whisper.cpp, suggestion column, never writes `bow_number`).

The work will be done by several weak coding agents, **sequentially on `main`**, one small step each. Each step is a self-contained task sheet: exact files, signatures, SQL, tests with expected values, done-criteria. Agents never read the spec or any other sheet.

Verified facts from the code that shape the design (so agents are not asked to discover them):

- `capture.bow_number`, `bow_suggested`, `bow_source` already exist (`storage.py:65,72-73`); `update_capture` whitelist (`storage.py:524`) does not include `bow_source`/`bow_suggested`.
- `race.audio_path`, `race.audio_t0_offset_s`, `Storage.set_race_audio` already exist (`storage.py:39-40,484`).
- `[voice]` config block already exists (`config.py:90-99`) with `enabled,input,play_before_s,play_after_s,transcribe,model,transcribe_before_s,transcribe_after_s`. `Config.section(name)` raises `KeyError` for a section missing from `DEFAULTS`, so `[scratchpad]` must be added to `DEFAULTS`.
- Writer thread: `controller._queue` with job kinds `"capture"`/`"stop"` only (`controller.py:331`). There is no generic "run fn on writer thread". Tests drain with `controller._queue.join()`.
- Events: one hook `controller.events(kind, payload)`; `main.py:249-261` installs its own router that forwards only `capture_added, capture_deleted, image_ready, race_ended, warning` through `_TriggerBridge`. A new kind needs a branch in **both** `main.py` and `MainWindow._on_controller_event`.
- `MainWindow._enable_shortcuts` already silences `typable` shortcuts while a `QLineEdit` has focus; in RECORDING, `Return/Enter/SPACE` are typable, `F12/Ctrl+Z/Ctrl+Q/Esc/F1/Ctrl+S` are not. So digits + Enter reach a focused field during a race with no keymap change.
- The race screen's `CrossingList(editable=False)` has no bow display; the review list has `_BowEdit` per row and `bow_edited(seq, text)` → `controller.set_bow_number`.
- Grab: `main.py:285-299` grabs `device_path` in ARMED/RECORDING. With `end_device_path` (laptop keyboard) set, Qt keeps receiving typing. The spec requires refusing to arm when the scratchpad is on and `device_path` is the internal keyboard; `trigger.py` never reads device name/phys today.
- Review screen `keyPressEvent` (`review_screen.py:638`) handles Up/Down/Shift+←→/0/Enter/Del/Ins/U/Shift+D; free letters: `P`, `T`, `A` (checked against `_REVIEW_KEYS` in `session.py:268`).

Verified facts for R4 (sheets 21–27):

- `calibration.py`: `Calibration` is a frozen dataclass with 6 positional fields (`latency_median_ms, latency_iqr_ms, resolution, fps, mean_frame_bytes, measured_at`); `load` returns None on missing/unreadable; `write_calibration` overwrites the whole file. `CALIBRATION_FILENAME = "calibration.json"`.
- `controller.py:331-343` `_compute_delta` reads `timing["reaction_offset_ms"]` directly; `_load_latency`/`calibration_status` (`controller.py:787-830`) are the only latency readers. `attach_recorder` stores the recorder as `controller.recorder` (`controller.py:149-152`).
- `ui/about_screen.py:66-78` `environment_rows(config)` returns `(label, value)` tuples; "Calibrated latency" is the last row.
- `ui/main_window.py:853-862` `_calibrate` builds `CalibrationDialog(self.buffer, self.config.data_root, self.config, self)` and refuses in ARMED/RECORDING; `on_evdev_start` (`:617`) and `on_evdev_crossing` (`:644`) are the bridge targets; `evdev_active` is set by `main.py` after the window exists.
- `ui/calibration_dialog.py`: `CalibrationDialog(QDialog)` hides itself during capture and `accept()`s at the end; `FullScreenCounter.present()` shows the keep-on-top pattern. There are no Qt tests for the dialog today (only `tests/test_calibration.py` for the loader).
- `ui/review_screen.py:238` sets `self._voice = controller.config.section("voice")` once at construction; `play_selected` reads `self._voice[...]`; `main.py` `build_core` builds `AudioRecorder(lambda: ArecordSource(str(voice["input"])))` from a section dict captured at start-up.
- `config.py`: `Config(data, path)` dataclass; `section()` = `DEFAULTS[name]` updated by `data[name]`; `load_config` is the only constructor outside tests; tests build `Config(data=..., path=...)` directly (`conftest.py:85`, `test_config.py`, `late_regatta_soak.py:59`).
- `tests/fakes.py`: `FakeScheduler(delay_s, cb)` returns a handle with `cancel()`; `advance(s)` fires due callbacks in time order. `FakeClock()` is callable with `advance`.
- `audio.py` has no device enumeration, no tone generator, no HTTP source; `aplay_argv(device=...)` and `play_clip` spawn `aplay -t raw -f S16_LE -r 16000 -c 1 -`.
