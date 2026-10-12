# Sheet 27 — docs R4: reaction phases, Audio tab, settings.json

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe-finish-timer-spec.md` (§5.5 — new sub-section after "Calibration constraints imposed by this hardware"; §6.7 — the `config.toml`/`calibration.json` paragraph and the filesystem listing; §8 — `[timing]` comment + a new paragraph after the toml block; §13.3.4 "Implemented" note), `AGENTS.md` (the "Calibration (`delta`)" bullet, the "Data & config" list, the "Audio (§13.3.4)" bullet, the repo-layout entries for `calibration.py`, `config.py`, `ui/`), `TESTING.md` (new T5b and T15 after T14), `hallofframe.example.toml` (`[timing]` and `[voice]` comments only), `INSTALL.md` (§2 package table: one row), `tests/test_goldens.py` only if a golden changes (it must not — report instead).
**Read first:** `hallofframe/calibration.py` (constants, `ReactionRun`, `update_reaction`, `reaction_offset_ms`), `hallofframe/config.py` (`SETTINGS_FILENAME`, `write_overrides`), `hallofframe/ui/calibration_dialog.py` (tabs), `hallofframe/ui/audio_page.py` (`values()`), `hallofframe/audio.py` (`probe_phone_audio`, `make_source` — present only if sheet 26 landed; check `git log --oneline -1 -- hallofframe/audio.py`), the sections of the four docs named above.

### What to write (one place each; no new headings beyond those listed)

**Spec §5.5 — add `#### 5.5.1 Reaction phases (implemented 2026-10)`** (≤ 25 lines):
- Two measured kinds: *visual* (screen turns white → press) and *sound* (beep on `[voice] output` → press); 10 trials each, random 1.5–4 s wait, presses < 80 ms = false start (repeated), > 1500 ms = miss (repeated), median + IQR over ≥ 6 samples stored in `calibration.json` under `reaction.<kind>` with the raw samples and `press_source` (`evdev` = kernel timestamp, `qt` = fallback, degraded).
- **Only the visual value enters Δ**: `Δ = R_visual − L` (water), `Δ = R_visual` (screen); when no measurement exists, `[timing] reaction_offset_ms` is used as before. The sound value is informational (About screen): elapsed times are *never* corrected for reaction (§5.3's cancellation); a measured `R_sound − R_visual` difference is a diagnostic for §5.3.1, not a correction.
- Known biases: the visual stimulus carries one vsync of display lag inside R; the beep carries the ALSA period (a few ms) — both well under the N2 budget; a latency re-run preserves reaction data and vice versa.

**Spec §6.7** — in the "`config.toml` is hand-edited; `calibration.json` is machine-written" paragraph add: `settings.json` is the second machine-written file: the Audio tab writes the operator's `[voice]` choices there and `Config.section` merges it over `config.toml` (so the toml stays hand-edited and the tomli-w argument stands). Add `settings.json` to the filesystem listing under `calibration.json`.

**Spec §8** — `[timing] reaction_offset_ms` comment → `# fallback when calibration.json has no measured reaction (§5.5.1)`. After the toml block add one paragraph: `settings.json` precedence (defaults < config.toml < settings.json), which keys the Audio tab writes (`input, output, mixer_card, mixer, gain_db, highpass_hz, press_mute_ms`), and that the phone microphone appears as an `http://…` input only when the camera app serves a WAV stream (sheet 26's probe; absent otherwise).

**Spec §13.3.4 "Implemented" note** — append: `; Calibrate → Audio tab lists arecord/aplay PCMs and amixer controls, previews mic level / 3 s loopback / test tone, and writes settings.json`.

**AGENTS.md**:
- "Calibration (`delta`)" bullet — append: `Δ's reaction term is the measured visual reaction from calibration.json (reaction.visual.median_ms) when present, else [timing] reaction_offset_ms (calibration.reaction_offset_ms returns (ms, "measured"|"config")). The sound reaction is stored/displayed only. Calibrate is a 3-tab dialog (Latency | Reaction | Audio); while it is open MainWindow.press_hook routes evdev presses to it (never while ARMED/RECORDING).`
- "Data & config" list — new item after `calibration.json`: `settings.json — machine-written by the Audio tab ([voice] keys); Config.section merges defaults < config.toml < settings.json. The app still never writes config.toml.`
- "Audio (§13.3.4)" bullet — append: `The recorder's source is built per race from the live [voice] input (audio.make_source: http:// → HttpWavSource (phone mic via the tunnel), else ArecordSource).` (drop the `make_source`/`HttpWavSource` clause if sheet 26 is BLOCKED.)
- Repo layout: `calibration.py` line — add `reaction run/stats + update_reaction`; `config.py` line — `+ settings.json overrides`; `ui/` list — add `reaction_page, audio_page`.

**TESTING.md** — after T14:
- `### T5b — Reaction calibration (manual)`: C → Reaction tab; Visual run with the race trigger button: 10 white flashes, status shows each ms; false start on purpose → "False start — again"; result median plausibly 180–300 ms; Save → `calibration.json` `reaction.visual.press_source == "evdev"`; About (F1) shows "Reaction (visual) … (measured)"; Sound run with `[voice] output` set → beeps audible, `reaction.audio` written; a latency re-capture afterwards keeps both. Pass: both medians present, IQR < 80 ms, next race's `capture.delta_used` equals `(R_visual − L)/1000` (water) to 1 ms.
- `### T15 — Audio tab (manual)`: C → Audio; input/output lists match `arecord -L`/`aplay -L`; moving "Capture" changes `amixer sget Capture` immediately; Listen shows the bar moving when speaking; Record 3 s and play back plays your voice with the chosen gain/high-pass; Test tone on the chosen output; Accept writes `settings.json` with those seven keys; restart → the hiss level matches the sliders (mixer re-applied) and review `P` uses the new filters without editing `config.toml`. With the phone streaming, the input list has an "iPhone microphone" entry only if the gate in sheet 26 passed.

**hallofframe.example.toml** — `[timing] reaction_offset_ms` comment as in spec §8; `[voice]` block header: one comment line `# The Calibrate → Audio tab writes these keys to settings.json, which overrides this file.`

**INSTALL.md §2 package table** — the `alsa-utils` row: append `Also provides arecord -L / aplay -L / amixer for the Calibrate → Audio tab.`

### Tests
None new. `pytest tests/test_goldens.py` must be unchanged; the fast suite green.

**Commit:** `docs: reaction phases (§5.5.1), Audio tab, settings.json precedence; T5b/T15 (step 27)`
