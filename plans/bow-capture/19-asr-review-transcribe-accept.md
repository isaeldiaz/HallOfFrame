# Sheet 19 — review: Transcribe (`T`), Accept (`A`), cancel on arm

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/ui/review_screen.py`, `hallofframe/session.py`, `hallofframe/ui/main_window.py`, new `tests/test_review_asr.py`, `tests/test_session.py` (append).
**Read first:** `review_screen.py` (`__init__`, `load_captures`, `keyPressEvent`, `_move_selection`, `_selected_capture`, `play_btn` from sheet 12), `session.py` `_REVIEW_KEYS`, `main_window.py` (`_open_review`, `_apply_state`, review forwarders), `main.py` `_TriggerBridge` (worker→GUI signal pattern), `asr.py` (`Transcriber`, `TranscribeResult`), `tests/test_review_audio.py` (fixture), `tests/fakes.py` (`FakeRunner`).

### Spec
`review_screen.py`:
- `__init__(self, controller, data_root, race_id=None, parent=None, transcriber=None)`; `self._transcriber = transcriber`; `self._asr_inflight = False`; class attribute `_asr_finished = Signal(object)` connected to `self._on_transcribed`.
- Header: `self.transcribe_btn = QPushButton("Transcribe (T)")`, `Qt.NoFocus`, `clicked → self.transcribe`, `setVisible(transcriber is not None)`, after `play_btn`.
- `load_captures`: pass `"bow_suggested": c["bow_suggested"]` into `list.add`; call `_refresh_transcribe_btn()`.
- `can_transcribe(self) -> bool`: `transcriber is not None and not _asr_inflight and not controller.running and self._audio_path is not None`.
- `_refresh_transcribe_btn`: `setEnabled(can_transcribe())`; text `"Transcribing…"` while in flight else `"Transcribe (T)"`.
- `transcribe(self)`: if not `can_transcribe()`: `notify.emit("Transcribe needs a finished race with audio")`; return. Else set in flight, refresh, `threading.Thread(target=self._run_transcribe, args=(self.race_id,), daemon=True, name="asr").start()`.
- `_run_transcribe(self, race_id)`: `try: result = self._transcriber.run(race_id) except Exception as exc: result = TranscribeResult(race_id, 0, [], str(exc))`; then `self._asr_finished.emit(result)` — the ONLY thing the worker does with Qt is emit this signal, and it always emits (otherwise the button would stay on "Transcribing…" forever).
- `_on_transcribed(self, result)`: clear in-flight; refresh button; if `result.race_id != self.race_id`: return (the operator moved to another race; its suggestions are in the DB for when it is reopened). `result.error` → `notify.emit(f"Transcription failed: {result.error}")`; else remember `_selected_seq`, `load_captures()`, re-select, `notify.emit(f"{result.count} bow suggestions — A accepts")`.
- `accept_suggestion(self)`: cap = selected; `s = cap.get("bow_suggested")`; None → `notify.emit("no suggestion for this crossing")`; else `controller.set_bow_number(cap["id"], s, source="asr")`; update `cap["bow_number"]`, `cap["bow_source"]`; `list.set_bow(seq, s)`; advance with `_move_selection(1)` **only if the selected row is not the last entry of `self._captures`** (no wrap-around, so repeated `A` stops at the bottom).
- `cancel_transcription(self)`: if in flight: `self._transcriber.cancel()`.
- `keyPressEvent` (after the focused-`QLineEdit` early return, same modifier check as `U`): `Qt.Key_T` → `transcribe()`; `Qt.Key_A` → `accept_suggestion()`.

`session.py` `_REVIEW_KEYS`: add `_k("T", "Transcribe", "_review_transcribe", show=False, shortcut=False)` and `_k("A", "Accept bow", "_review_accept", show=False, shortcut=False)`.

`main_window.py`: `self._transcriber = None`; in `__init__`, `if bool(config.section("voice")["transcribe"]): from ..asr import Transcriber; self._transcriber = Transcriber.from_config(config, controller.storage, logger=self._logger)` (import inside the branch; note the attribute is `self._logger`). `_open_review`: pass `transcriber=self._transcriber`. Forwarders `_review_transcribe` / `_review_accept` (shape of `_review_undo`). `_apply_state`: `if state in (AppState.ARMED, AppState.RECORDING) and self._review_screen is not None: self._review_screen.cancel_transcription()`.

### Tests — `tests/test_review_asr.py` (qt). Copy the fixture from `test_review_audio.py` (do not import it) and add: `voice={"transcribe": True}`; `(data_root/"models"/"ggml-nb-whisper-base.bin")` touched; `self.runner = FakeRunner(json.dumps(CANNED))` (`from fakes import FakeRunner`, `from test_asr import CANNED`); `self.win._transcriber = Transcriber(storage, self.runner, whisper_cli="whisper-cli", model=…, gap_s=0.4, timeout_s=5, data_root=data_root)` BEFORE `_open_review()`. The fixture race has 3 captures (seq 1–3).
| Test | Setup | Expected |
|---|---|---|
| `test_button_hidden_without_transcriber` | `win._transcriber = None` before `_open_review()` | `transcribe_btn.isHidden()` |
| `test_disabled_while_race_running` | `controller.running = True` BEFORE `_open_review()` | button disabled; `transcribe()` → `runner.calls == []`, `notify` emitted |
| `test_disabled_without_audio` | `storage.set_race_audio(race_id, None, None)` before `_open_review()` | button disabled |
| `test_transcribe_fills_suggestions` | `_open_review()`; `screen.transcribe()`; loop `qapp.processEvents()` until `not screen._asr_inflight` (5 s deadline) | `bow_suggested` `"14","7","21"` on seq 1–3; `sugg_lbl` shown on each; toast text contains `"3 bow suggestions"` |
| `test_a_accepts_and_advances` | `storage.set_bow_suggestions(race_id, {id1:"14", id2:"7"})` then `_open_review()`; `screen.setFocus()`; key `A` | seq 1 `bow_number "14"`, `bow_source "asr"`; selection is seq 2 |
| `test_a_on_last_row_does_not_wrap` | suggestions on all 3; select seq 3; key `A` | seq 3 accepted; selection stays seq 3 |
| `test_a_in_bow_field_types_letter` | focus a bow field; key `A` with text `"a"` | storage unchanged |
| `test_arming_cancels_inflight` | `_open_review()`; `screen._asr_inflight = True`; `win._apply_state(AppState.ARMED)` | `runner.cancelled is True` |
| `test_stale_result_for_other_race_ignored` | `screen._on_transcribed(TranscribeResult(race_id + 99, 2, [], None))` | no toast; `_asr_inflight False` |
| `test_transcriber_built_only_when_voice_transcribe` | default config | `win._transcriber is None` |
`tests/test_session.py` (append): `test_review_keymap_has_t_and_a_uncapped` (`T`, `A` in REVIEW with `shortcut False`, `show False`; absent elsewhere).

**Commit:** `feat(review): Transcribe (T) and Accept (A) bow suggestions (§13.3.4 step 19)`
