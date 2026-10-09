# Sheet 13 — docs for R2 (INSTALL, example config, spec, AGENTS, TESTING)

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `INSTALL.md`, `hallofframe.example.toml`, `hallofframe-finish-timer-spec.md`, `AGENTS.md`, `TESTING.md`.
**Read first:** `INSTALL.md` §2 (apt list ~line 50–80, verify script ~500–520, offline staging ~565–575), `hallofframe.example.toml` `[voice]` (~line 106), spec §8 `[voice]` (~1437), §6.7 `audio_path` lines, §13.3.4 first paragraph, `AGENTS.md` repo layout, `TESTING.md` §8 table.

### Edits
1. INSTALL §2 apt list: add `alsa-utils`; table row: "`alsa-utils` | §13.3.4 | `arecord`/`aplay` for booth audio replay; optional, only with `[voice] enabled = true`". Verify script: add `arecord aplay` to the binaries loop. Offline staging list: add `alsa-utils`.
2. `hallofframe.example.toml` `[voice]`: `input` comment → `ALSA PCM for arecord -D ("default" = system default via PipeWire/Pulse; "plughw:1,0" for a USB headset)`; comment line "transcribe*/model: release 3 (ASR), unused until then".
3. Spec §8 same comment; §6.7 (spec lines ~1095–1096, comment text is `-- phase 8`): replace with `-- §13.3.4: races/<id>_<name>/audio.wav; seconds from t0 to the first sample (±0.2 s, replay only)`; §13.3.4 "Audio replay" paragraph: append "*(Implemented <date>: `audio.py` — arecord subprocess source, WAV sink with per-block header, aplay clip player; recorder starts at `t0`, stops at race end, not resumed after a restart; no web serving.)*".
4. `AGENTS.md`: layout row `audio.py`; rule bullet: "**Audio (§13.3.4)**: `AudioRecorder` capture thread → bounded queue → writer thread; drops become silence; `race.audio_t0_offset_s = t_first_sample − t0`; only with `[voice] enabled`; never on the trigger thread."
5. `TESTING.md`: **T14 — Audio replay (manual)**: mic on, race, speak at a crossing, end, `R`, select, `P`; check `races/<id>/audio.wav` and `SELECT audio_path, audio_t0_offset_s FROM race`. Add to the §8 table.

**Commit:** `docs: audio replay R2 (§13.3.4), alsa-utils in INSTALL (step 13)`
