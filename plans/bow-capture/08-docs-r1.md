# Sheet 08 — docs for R1 (spec v1.3, AGENTS.md, TESTING.md)

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe-finish-timer-spec.md`, `AGENTS.md`, `TESTING.md`, `hallofframe.example.toml`.
**Read first:** `hallofframe.example.toml` (`[voice]` block at ~line 106; add `[scratchpad]` before it, same comment as §8 below), spec lines 1–64 (header/version), §6.7 table (line ~1058–1208; `bow_suggested`/`bow_source` at ~1121–1122), §7.1 (1262), §8 (1367–1503; `[voice]` at ~1437), §9.3 (1568, steps 1–13), §11 table (1691), §13.3.1–13.3.3 (2215–2306); `AGENTS.md` "Repo layout" and "Key architectural rules"; `TESTING.md` §3 (T-numbering) and §8 table (~line 653); `tests/test_controller_scratch.py` (for the T13 node id).

### Edits (text only; no code)
1. Spec header: bump to v1.3 with a one-line changelog entry "§13.3 scratchpad implemented (R1)".
2. §6.7: add the `scratchpad` table DDL (copy from sheet 01) after the `capture` table; change the two `-- phase 8` comments on `bow_suggested`/`bow_source` (spec ~1121–1122) to `-- §13.3.4 ASR suggestion` and `-- §13.3: 'live' | 'manual' | 'asr'`. In §13.3.3, add after "a `race_active` flag": "(implemented as the existing `controller.running`)".
3. §7.1 layout sketch: add a `bow: [    ]  typed 3 / crossings 4` line under the CAPTURES box.
4. §8 config example: add
   ```toml
   [scratchpad]
   enabled = false              # §13.3 live bow-number scratchpad; needs an external trigger device
   ```
5. §9.3: add steps 14–15: "Confirm the external button is `[trigger] device_path` (the app refuses to arm with the scratchpad on the laptop keyboard)." and "Agree that operator 1 calls bow numbers digit by digit ("en-fire" for 14) immediately after each press."
6. §11 table: row **Scratchpad counters differ** | typed ≠ crossings turns red | Operator 2 fixes rows in review; nothing is guessed (§13.3.2).
7. §13.3 bullet "Bow-number capture": append "*(Scratchpad implemented <date>: `scratchpad.py`, `ui/scratchpad_widget.py`, `[scratchpad] enabled`; audio/ASR per §13.3.4 pending.)*".
8. `AGENTS.md`: repo layout rows for `scratchpad.py` and `ui/scratchpad_widget.py`; a "Key architectural rules" bullet: "**Scratchpad (§13.3)**: `controller.add_scratch` queues; `_recompute_scratch` runs on the writer thread after every capture/entry/undo and at race end; `bow_source` `live` is overwritable, `manual` never; `enqueue(fn)` is the only way to run code on the writer thread; UI gets one `scratch_changed` event."
9. `TESTING.md`: add **T13 — Scratchpad replay**: `pytest tests/test_controller_scratch.py::TestControllerScratch::test_worked_example` plus the bench procedure (external button + keyboard: 4 presses, type 14, 7, 3; expect rows 14, 7, 3, blank and counters 3/4 red; fix in review). Add T13 to the §8 table (needs iPhone: no; desktop: yes).

**Commit:** `docs: spec v1.3 scratchpad (§13.3 R1), AGENTS/TESTING updates (step 08)`
