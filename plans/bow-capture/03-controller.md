# Sheet 03 — controller: writer-thread calls, scratch commands, recompute, event

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/controller.py`, new `tests/test_controller_scratch.py`.
**Read first:** `hallofframe/controller.py` (whole), `hallofframe/scratchpad.py`, `hallofframe/storage.py` (the scratch/bow methods from sheet 01), `tests/test_controller.py` lines 1–60 (fixture + `commit()` pattern).

### Goal
The controller owns all scratchpad I/O. Every write runs on the existing persistence writer thread; the UI only calls `add_scratch`/`undo_scratch` and listens for one event.

### Spec
1. Constructor: `self.scratchpad_enabled = bool(config.section("scratchpad")["enabled"])`.
2. Generic writer-thread call:
   ```python
   def enqueue(self, fn) -> None:
       """Run fn() on the persistence writer thread, after everything already queued."""
       self._queue.put(("call", fn))
   ```
   `_writer_loop`: add `elif kind == "call": payload()`. The existing `except Exception` logs `capture_failed`; change the log key to `"job_failed"` and the toast text to `f"write failed: {exc}"` (both kinds).
3. Commands (GUI thread, return immediately; these are NOT guarded by `scratchpad_enabled` — only `_recompute_scratch` is):
   ```python
   def add_scratch(self, text: str) -> bool:
       """Queue one typed bow number for the running race. False (nothing queued)
       when no race is running or normalize(text) is None."""
       if not self.running or self.race_id is None: return False
       t = scratchpad.normalize(text)
       if t is None: return False
       rid, t_wall = self.race_id, time.time()          # snapshot NOW, never inside the job
       def _job():
           self.storage.insert_scratch(rid, t, t_wall)
           self._recompute_scratch(rid)
       self.enqueue(_job)
       return True
   def undo_scratch(self) -> None:
       """Soft-delete the last typed number of the running race, then recompute. No-op when not running.
       Enqueued (never synchronous) so it runs AFTER any insert still in the queue."""
       if not self.running or self.race_id is None: return
       rid = self.race_id
       def _job():
           self.storage.delete_last_scratch(rid)
           self._recompute_scratch(rid)
       self.enqueue(_job)
   ```
4. Recompute (writer thread only; `comment: runs on the persistence writer thread`):
   ```python
   def _recompute_scratch(self, race_id: int) -> None:
       if not self.scratchpad_enabled: return
       entries = [r["text"] for r in self.storage.scratch_entries(race_id)]
       caps = self.storage.captures_for_race(race_id)          # non-deleted, by sequence
       changes = scratchpad.assign(entries, [(c["id"], c["bow_number"], c["bow_source"]) for c in caps])
       if changes: self.storage.apply_bows(changes)
       typed, crossings = self.storage.scratch_counts(race_id)
       bows = {c["sequence"]: c["bow_number"] for c in self.storage.captures_for_race(race_id)}
       self._emit("scratch_changed", race_id=race_id, typed=typed, crossings=crossings, bows=bows)
   ```
5. Hooks (all guarded by `self.scratchpad_enabled`):
   - `_handle_capture`: after `self._emit_capture(cap)`: `self._recompute_scratch(race_id)` (same thread, direct call).
   - `start_race`: after `self._emit("race_started", ...)`: `self.enqueue(lambda: self._recompute_scratch(race_id))`.
   - `resume_race`: at the end: same enqueue.
   - `end_race`: immediately BEFORE the existing `self._drain_queue()`: `self.enqueue(lambda: self._recompute_scratch(race_id))` (FIFO → runs after pending captures; the drain waits for it).
   - `remove(capture_id)` and `undo_last()`: after the delete, `if cap is not None and self.running and cap["race_id"] == self.race_id: rid = self.race_id; self.enqueue(lambda: self._recompute_scratch(rid))` (`cap` can be None in `remove`; snapshot `rid` outside the lambda).
6. `set_bow_number(self, capture_id, value, source="manual")` → `self.storage.set_bow(capture_id, value, source)`. (Review edits become `manual` and are never overwritten by the scratchpad.)
7. Nothing above touches `t0`, `t_press`, `delta`, image selection, or `record_crossing`.

### Tests — `tests/test_controller_scratch.py` (class `TestControllerScratch`)
Fixture like `controller_env` in `test_controller.py` but `config(scratchpad={"enabled": True}, timing={"image_mode": "off"})` (timing-only: no buffer needed). Capture events: `events = []; controller.events = lambda k, p: events.append((k, p))`. Helper `commit()` = `controller._queue.join()`. Start: `rid = controller.start_race(1000.0, name="R")`. Crossing: `controller.record_crossing(1000.0 + n)`. Bows: `{r["sequence"]: (r["bow_number"], r["bow_source"]) for r in storage.captures_for_race(rid)}`.

| Test | Steps | Expected |
|---|---|---|
| `test_add_scratch_requires_race` | no race: `add_scratch("14")` | `False`; `scratch_entries` empty for any race |
| `test_add_scratch_rejects_bad_text` | race; `add_scratch("")`, `add_scratch("1a")` | both `False`; 0 entries |
| `test_worked_example` (spec §13.3.2) | race; crossing; add 14; crossing; add 7; crossing; crossing; add 3; `commit()` | bows `{1:("14","live"),2:("7","live"),3:("3","live"),4:(None,None)}`; last `scratch_changed` payload `typed=3, crossings=4` |
| `test_typed_before_press_waits` | race; add 14; `commit()`; then crossing; `commit()` | seq 1 → `"14"` |
| `test_manual_wins_and_keeps_position` | race; 3 crossings; `commit()`; `set_bow_number(cap2_id, "99")`; add 14, 7, 21; `commit()` | `{1:("14","live"),2:("99","manual"),3:("21","live")}` |
| `test_undo_scratch_shifts` | race; 3 crossings; add 14, 7, 21; `undo_scratch()`; `commit()` | `{1:"14",2:"7",3:None}`; entry 3 `deleted=1` |
| `test_undo_last_capture_recomputes` | race; 2 crossings; add 14, 7; `commit()`; `controller.undo_last()`; `commit()` | remaining seq 1 → `"14"`; last event `crossings=1, typed=2` |
| `test_end_race_recomputes_pending` | race; add 14; crossing; `end_race()` (no manual commit) | seq 1 → `"14"`; `race.ended_at` set |
| `test_resume_continues_ordinals` | race; add 14, 7; `commit()`; `controller.stop()`; second controller (same storage, same config) with its own `events` list; `resume_race(rid)`; add 3; `commit()`; `stop()` it in the test | ordinals 1,2,3; `scratch_changed` emitted on resume (`resume_race` does not read `ended_at`, so nothing needs resetting) |
| `test_disabled_is_inert` | `config(scratchpad={"enabled": False})`; race; `add_scratch("14")`; `commit()` | returns True and the row is inserted, but `_recompute_scratch` is a no-op: no `scratch_changed` event, capture bows stay None |
| `test_set_bow_number_default_source_manual` | capture | `set_bow_number(id, "5")` → `bow_source == "manual"`; `set_bow_number(id, None)` → `(None, "manual")` |
| `test_call_job_failure_does_not_kill_writer` | race; `enqueue(lambda: 1/0)`; `commit()`; crossing; `commit()` | crossing persisted; a `warning` event with `"write failed"` |

**Commit:** `feat(controller): scratchpad commands, writer-thread calls, scratch_changed (§13.3 step 03)`
