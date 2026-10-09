# Sheet 01 — storage + config: scratchpad table, bow writes

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/storage.py`, `hallofframe/config.py`, new `tests/test_storage_scratch.py`.
**Read first:** `hallofframe/storage.py` (whole), `hallofframe/config.py` (DEFAULTS), `tests/test_storage.py` (style), `tests/conftest.py` (fixtures `storage`, `data_root`).

### Goal
Add the `scratchpad` table and the storage methods that the scratchpad feature will use. Add the `[scratchpad]` config section. No behaviour changes elsewhere.

### Spec

1. `config.py` `DEFAULTS`: add after `"voice"`:
   ```python
   "scratchpad": {"enabled": False},  # §13.3: live bow-number scratchpad (two-operator booth)
   ```
2. `storage.py` `SCHEMA`: append before the `CREATE INDEX` lines:
   ```sql
   CREATE TABLE IF NOT EXISTS scratchpad (
       id            INTEGER PRIMARY KEY,
       race_id       INTEGER NOT NULL REFERENCES race(id),
       ordinal       INTEGER NOT NULL,          -- order typed, 1..n, never reused
       text          TEXT NOT NULL,
       t_typed_wall  REAL NOT NULL,
       deleted       INTEGER NOT NULL DEFAULT 0,
       UNIQUE (race_id, ordinal)
   );
   ```
   No change in `_migrate` (the `CREATE TABLE IF NOT EXISTS` in `SCHEMA` runs on every open and is the migration).
3. `update_capture`: add `"bow_source"` to `allowed` (NOT `bow_suggested`: only the ASR writer of sheet 16 may write it, through its own method). In `SCHEMA`, change the two column comments at `bow_suggested`/`bow_source` from `-- phase 8, unused` to `-- §13.3.4 ASR suggestion` and `-- §13.3: 'live' | 'manual' | 'asr'`.
4. New methods on `Storage` (each takes `self._lock`, calls `self._touch(race_id)`, commits — copy the pattern of `insert_capture`):
   ```python
   def insert_scratch(self, race_id: int, text: str, t_typed_wall: float) -> int:
       """ordinal = COALESCE(MAX(ordinal),0)+1 over ALL rows of the race (deleted included). Returns the new id."""
   def scratch_entries(self, race_id: int, include_deleted: bool = False) -> list:
       """Rows ordered by ordinal. (No _touch: read only.)"""
   def delete_last_scratch(self, race_id: int) -> int | None:
       """Soft-delete (deleted=1) the non-deleted row with the highest ordinal. Returns its id, or None if none."""
   def scratch_counts(self, race_id: int) -> tuple[int, int]:
       """(non-deleted scratch rows, non-deleted captures) for the race. Read only."""
   def set_bow(self, capture_id: int, value: str | None, source: str | None) -> None:
       """UPDATE capture SET bow_number=?, bow_source=?, updated_at=? WHERE id=?; _touch(race_id)."""
   def apply_bows(self, changes: list[tuple[int, str | None]]) -> int:
       """One transaction. For each (capture_id, value): bow_number=value, bow_source=('live' if value is not None else NULL), updated_at=now.
       _touch each distinct race_id once. Returns number of rows updated. Empty list → 0, no commit."""
   ```
   `set_bow` and `apply_bows` must NOT read or write `t_press`, `elapsed_s`, `elapsed_source`.

### Tests — `tests/test_storage_scratch.py`
Use fixtures `storage`, and a helper `_race(storage)` → `storage.create_race("R", 0.0, 0.0, "direct", 0.0, 0.0, "screen")`, `_cap(storage, race_id, seq)` → `storage.insert_capture(race_id, seq, float(seq), float(seq), float(seq), 0.0)`.

| Test | Setup | Action | Expected |
|---|---|---|---|
| `test_section_default` | `Config(data={}, path=Path("x"))` | `.section("scratchpad")` | `{"enabled": False}` |
| `test_insert_scratch_ordinals` | race | insert `"14"`, `"7"` | ordinals 1, 2; `text` as given; `deleted` 0; `t_typed_wall` stored |
| `test_ordinal_never_reused` | race; insert 7, 8 | `delete_last_scratch` → id of ordinal 2; insert 9 | ordinals 1, 2(deleted=1), 3; `scratch_entries` returns [1,3]; `include_deleted=True` returns [1,2,3] |
| `test_delete_last_none` | race, no entries | `delete_last_scratch` | `None` |
| `test_scratch_counts` | race; 2 entries, 3 captures, 1 capture soft-deleted via `update_capture(deleted=1)` | `scratch_counts` | `(2, 2)` |
| `test_set_bow` | capture | `set_bow(id, "14", "manual")` | row `bow_number="14"`, `bow_source="manual"`; `t_press`, `elapsed_s` unchanged |
| `test_apply_bows` | 3 captures | `apply_bows([(a,"14"),(b,None),(c,"3")])` → 3 | a: `"14"`/`live`; b: `None`/`None`; c: `"3"`/`live` |
| `test_apply_bows_empty` | — | `apply_bows([])` | `0` |
| `test_apply_bows_touches_race` | capture; `monkeypatch.setattr(storage_mod, "_utcnow", _Clock())` (copy `_Clock` from `tests/test_storage.py` lines 21–29) | `last_updated(race_id)` before/after `apply_bows` | changed |
| `test_update_capture_allows_bow_source` | capture | `update_capture(id, bow_source="manual")` | stored; `update_capture(id, bow_suggested="9")` raises `ValueError` |
| `test_schema_on_existing_db` | do NOT use the `storage` fixture: `Storage(data_root).close(); st = Storage(data_root)` | `st.scratch_entries(1)` then `st.close()` | `[]` (no error) |

### Done when
All tests above pass; `pytest -m "not slow"` green; goldens unchanged.
**Commit:** `feat(storage): scratchpad table, bow writes, [scratchpad] config (§13.3 step 01)`
