# Record races to a Google Drive spreadsheet

## Context

Today a finished race lands in SQLite (`~/regatta-data/regatta.db`) and, on
demand, the operator's clipboard as an Excel-formatted block (`E` key →
`export.clipboard_data`). There is no durable, human-readable roll-up of the whole
regatta, and nothing leaves the laptop. The ask is for results to also accumulate in
a spreadsheet in Google Drive so they are visible off the machine.

**Spec constraint.** `regatta-finish-timer-spec.md:108` requirement N3: *"The system
must run offline. No internet, no cloud, no LAN infrastructure."* `verify-env.sh`
turns wifi off, and NTP is disabled (`spec:1528`). The laptop will essentially never
have network at the moment a race ends, so an in-app cloud write could never succeed
when it matters.

**Therefore the app itself does no networking.** It maintains a local `.xlsx` whose
path is configurable, pointed at a folder an existing desktop sync client (Insync,
GNOME Online Accounts, an rclone mount) already mirrors to Drive. The sync client
owns the OAuth token, the retry, and the offline queueing. N3 stays intact, no new
network code, no credentials in the repo, and no new Python dependency beyond
declaring `openpyxl`, which `races.py` already uses.

## Design (as revised after agent review)

**A manual, explicit save.** The operator triggers a **"Save race and copy"** action
only after bows and times are in (REVIEW or RACE_OVER state — never mid-race). It
does two things at once:
1. copies the race to the clipboard (the existing `_export`/`clipboard_data` path,
   unchanged), and
2. appends that race's results block to the end of `results.xlsx`, leaving one blank
   spacer row.

After a race has been saved once, the same button reads **"Copy only"** — it still
copies to the clipboard but will not append a second block for that race. This is how
duplicate blocks are prevented (no schema change, no DB write; the flag is in-memory).

This replaces the earlier "rebuild the whole workbook from SQLite on every trigger"
design. The sheet is now a **manual export log**, not a live projection: it reflects
whatever was saved, not whatever the DB currently holds. That is the accepted tradeoff.

### No SUPERSEDED; a re-record note instead

If the race name being saved already has a block in the sheet, the **new** block's
title carries a note so the operator can tell it is not the first run:

```
Heat 1 - Men's Single (re-recorded — an earlier entry exists)
```

The note is decided by **scanning the existing sheet's column A** for an exact
name match at save time (we load the workbook to append anyway, so this is free). We
**never** scan-and-edit an old block: no SUPERSEDED styling, no rewriting previous
rows, no `ws.delete_rows`. Re-recorded races simply stack as separate blocks in
chronological order.

### No in-place editing — append only

A save **never** touches previously written rows. It loads the workbook, finds the
end of the data region, writes the new block (title / header / data rows), writes one
blank spacer row, and saves atomically (temp file + `os.replace()` so a sync client
never uploads a half-written file).

### Documented limitations (accepted)

- **Not restart-proof.** Append keeps no persisted "this race was saved" marker, so a
  race saved, then the app restarted, then saved again would append a second block.
  This is inherent to append-on-save and accepted (the original rebuild design was
  restart-proof; this one is not).
- **Not always current.** The sheet only reflects what was explicitly saved, so edits
  made *after* a save won't appear. Operator should save only once, after final edits.
- **Re-record duplicates aren't reconciled.** The note tells the operator a prior
  entry exists, but nothing groups or compares attempts.

## Layout — one master sheet, races stacked

Mirrors what `export.clipboard_data` already produces, so the Drive sheet and a
clipboard paste look the same:

```
Heat 1 - Men's Single                      <- bold title row
sequence  bow_number  elapsed_seconds  elapsed_formatted  wall_clock_utc  image_file  image_flag  notes
1         4           372.483000       6:12.483           2026-08-25T...  ...
2         7           ...
                                           <- blank spacer row
Heat 2 - Men's Single
...
```

- Title row: the race name, bold, spanning all columns (same as `clipboard_data`).
- Header + data rows: exactly `export._COLUMNS` via `export._data_rows`
  (`export.py:37-51`), which already excludes soft-deleted captures
  (`include_deleted=False`).
- One blank, **unstyled** spacer row between blocks (see the spacer pitfall below).
- `image_file` is the existing data-root-relative `primary_image` (matches CSV and
  clipboard today); kept as-is for consistency.

## Changes

### New — `hallofframe/results.py`

Plain, Qt-free, openpyxl-free where possible (mirrors `races.py`/`export.py` shape):

- `has_block_for_name(path, name) -> bool` — open the workbook read-only and scan
  column A for an exact (whitespace-trimmed) match. Returns False if the file is
  missing or openpyxl is unavailable.
- `append_race(storage, race_id, out_path) -> Path` — build the block with
  `export._data_rows` (title = `storage.get_race(race_id)["name"]`, then the header
  and data rows, then one blank row). If the file exists, load it and append at the
  end; else create a fresh `Workbook`. Apply fonts: title bold; title gets the
  `(re-recorded — an earlier entry exists)` suffix when `has_block_for_name` was
  true. Save atomically: write to a temp file in the same directory, then
  `os.replace()`.
  - **Guard the `openpyxl` import** the way `races.py:49-52` does: on import failure,
    log and no-op instead of raising.

### `hallofframe/export.py`

`_data_rows` (`export.py:37`) is already exactly the needed serializer. Promote it to
a public `data_rows(storage, race_id)` and keep `_data_rows` as an alias, or update
its two in-module callers (`export_csv`, `clipboard_data`). Reuse `format_elapsed`
(`:20`) and `utc_iso` (`:28`) unchanged. **Do not fork the column list** — `_COLUMNS`
stays the single definition.

### `hallofframe/config.py`

Add a `results` section to `DEFAULTS` (`config.py:21`). This is mandatory:
`Config.section()` (`:88-92`) does an unguarded `DEFAULTS[name]` and raises
`KeyError` for an unregistered section.

```toml
[results]
xlsx_path = "~/regatta-data/results.xlsx"   # point at a Drive-synced folder
```

Expand `~` with `os.path.expanduser` following the `races.excel_path` precedent
(`main_window.py:565-566`). No `_validate` entry needed — no enum fields.

### `hallofframe/storage.py`

**No change required.** `get_race(race_id)` (`:122`) already returns the full row
including `name` and `ended_at`; `captures_for_race(race_id, include_deleted=False)`
(`:196`) provides the block rows. A save reads these directly.

### `hallofframe/main.py`

Add a results-completion signal to `_TriggerBridge` (`main.py:17`), e.g.
`results_done = Signal(int, str)`  # (race_id, outcome) — outcome "saved"|"error".
Wire it to a `MainWindow` handler that toasts the outcome and flips the button label.

### `hallofframe/ui/main_window.py`

- Add a **"Save race and copy"** button to the KeyBar in the `REVIEW`
  (`main_window.py:340-345`) and `RACE_OVER` (`main_window.py:346-350`) branches
  (`self.keybar.add(...)`, `ui/widgets.py:64-66`).
- The handler targets the **ended race whose data is on screen** (in RACE_OVER that is
  `self.controller.race_id`; in REVIEW the race the review screen holds). Guard on the
  race having ended: `get_race(race_id)["ended_at"] is not None` and
  `controller.running is False` — do not rely on the state enum alone, because REVIEW
  is also reachable from READY for a previous race (`main_window.py:357`).
- Keep the clipboard copy as-is: call `_export()`'s internals
  (`clipboard_data` + `_ExcelMimeData`, `main_window.py:39-56,524-531`).
- **Background thread for the write.** `load_workbook` + `wb.save` are disk/network
  I/O; on a slow FUSE mount they can block the GUI. Run `append_race` on a short-lived
  `threading.Thread`, marshal completion back via the `results_done` bridge signal, and
  surface the outcome with `_show_toast` (`main_window.py:552`) — never a modal dialog
  (spec §7.5) and never a direct Qt call from the worker.
- **"Saved once → Copy only" toggle.** Keep an in-memory `set[int]` of saved
  `race_id`s on the window. Key on `race_id` (unique, never reused,
  `controller.py:152-155`), **not** the name. After a successful save, add the id and
  re-render the bar so the button shows "Copy only". Because a save happens *within* a
  state, `_apply_keybar` (`main_window.py:288-322`) won't run on its own — call it
  explicitly (e.g. `_apply_keybar(self._last_state)`) after saving.
- **Do not change the READY `E` "Copy as Excel"** (`main_window.py:358`) — the new
  button is only for REVIEW/RACE_OVER.

### Dependency declaration

`openpyxl` is currently used by `races.py:26` but is absent from
`environment-lock.txt` and from the pip line at `INSTALL.md:136`. This change makes it
load-bearing, so declare it in all three places, including the offline wheelhouse in
`INSTALL.md` §9. The runtime import stays guarded regardless (a missing library
degrades to a logged no-op).

### Docs

- `README.md` — Configuration section: the `[results] xlsx_path` and `results.xlsx`;
  that the workbook is append-only generated output so hand annotations belong in a
  copy; that a save is manual and one-shot per race (button becomes "Copy only"); the
  re-record note; and the two documented limitations (not restart-proof, not
  always-current).
- `AGENTS.md` — the `~/regatta-data/` data inventory (`results.xlsx`), plus a line
  stating the app still performs no networking and Drive delivery is external.
- `regatta-finish-timer-spec.md` — a short note that a local results workbook is
  written and that N3 is preserved because upload is out-of-process.

## Verification

1. **Unit tests** — new `tests/test_results.py`, stdlib `unittest` under pytest, no Qt
   (follow `tests/test_export.py`). Build a `Storage` in a temp dir, insert races and
   captures directly, then assert:
   - one `append_race` produces a workbook with a title row, header row, data rows and
     one trailing blank spacer;
   - two `append_race` calls on different races produce two blocks separated by one
     blank row, with no gap growth between saves (guard the styled-spacer pitfall);
   - saving a race whose name already has a block yields a title suffixed
     `(re-recorded — an earlier entry exists)`, and the prior block is untouched;
   - a soft-deleted crossing is absent; an edited `bow_number` is reflected;
   - a race with no `ended_at` is not appended (the guard);
   - a missing `openpyxl` degrades to a no-op, not a traceback;
   - no temp file is left behind, and an existing workbook is never truncated on a
     mid-write failure (simulate by patching the save to raise);
   - `has_block_for_name` is False for a missing file and True after a matching save.
   Also close the known gap: `clipboard_data` still has no test (`tests/test_export.py`
   covers only `export_csv`).
2. **Full suite** — `./venv/bin/python -m pytest -q`.
3. **End-to-end on the machine** — set `[results] xlsx_path`, run
   `./venv/bin/python -m hallofframe`, record a short race with the trigger keyboard,
   press F12, open REVIEW, press the new "Save race and copy" button, and confirm the
   workbook appears with the race block and the button now reads "Copy only". Change a
   bow number in REVIEW, press Esc (leaving REVIEW) — confirm the clipboard copies the
   updated data but the workbook does **not** gain a duplicate block. Then record the
   same race name again and save it — confirm the second block appears below the first
   with the `(re-recorded …)` note.
4. **Spacer/gap regression** — save three races back-to-back and confirm there is
   exactly one blank row between blocks and no growing gap (catches the `max_row` /
   styled-spacer pitfall).
5. **Timing regression check** — record a race with several crossings and confirm
   elapsed times and `image_flag` values are unchanged versus a pre-change run; the
   trigger path must be untouched. Check `logs/regatta-app.jsonl` for the save/append
   events and for any errors.
6. **Drive leg** — point `xlsx_path` into the synced folder, run with network off,
   confirm the local file updates and the app shows no error; then restore network and
   confirm the sync client uploads it and Drive can open it in Sheets.
7. **Failure injection** — set `xlsx_path` to an unwritable directory and confirm the
   app logs, toasts, and keeps recording races to SQLite normally.

## Implementation notes from review (for the build agent)

- **Spacer pitfall:** `ws.max_row` includes any styled-but-empty cell. Write the spacer
  as a truly empty, unstyled row (or compute the append start by scanning the last
  column for the last non-empty row), or gaps grow by one row per save.
- **Save guard:** guard on `get_race(race_id)["ended_at"] is not None` plus
  `controller.running is False`, not the state enum alone (REVIEW is reachable from
  READY for a prior race).
- **Copy-only key:** in-memory `set[int]` keyed on `race_id`, not the display name, so
  a re-run of a same-named heat in one session isn't wrongly locked to Copy-only.
- **Re-record note key:** the note detection keys on the race *name* (workbook scan);
  the Copy-only lock keys on `race_id`. They are different lookups — keep them
  separate.
- **Thread:** background thread + `_TriggerBridge.results_done` signal + `_show_toast`.
  No direct Qt calls from the worker, no modal dialog.
- **openpyxl:** lazy guarded import; also add to `environment-lock.txt` and
  `INSTALL.md` (pip line + offline wheelhouse).
