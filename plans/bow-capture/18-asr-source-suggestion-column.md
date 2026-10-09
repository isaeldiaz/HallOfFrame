# Sheet 18 — `source='asr'` in the controller + suggestion column in the crossing list

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/controller.py`, `hallofframe/ui/crossing_list.py`, `tests/test_controller_scratch.py` (append), `tests/test_crossing_list.py` (append).
**Read first:** `controller.py` `set_bow_number`, `crossing_list.py` (`_Row` editable branch, `set_selected`, `_input_style`), `tests/test_crossing_list.py`.

### Spec
`controller.py`: `set_bow_number(self, capture_id, value, source="manual")`: `if source not in ("live", "manual", "asr"): raise ValueError(source)`; rest unchanged.

`crossing_list.py` (editable rows only). The bow field's stylesheet moves into `_Row` so one place decides the border (selected = blue, disagreeing suggestion = amber):
- `_Row.__init__`: `self._selected = False`; `self.suggested = data.get("bow_suggested")`; in the editable branch, **after** `self.bow_edit` is created, create `self.sugg_lbl = QLabel("")` (mono 18px) and insert it immediately before `bow_edit` in the layout; then call `self._restyle_suggestion()` (it reads `bow_edit.text()`, so it must run after `bow_edit` exists).
- `_Row._bow_style(self) -> str`: the string `CrossingList._input_style` builds today, with `border = styles.AMBER if (self.suggested is not None and not agree) else (styles.BLUE if self._selected else styles.PANEL_BORDER)`.
- `_Row._restyle_suggestion(self)`: no-op for non-editable rows. `suggested is None` → `sugg_lbl.hide()`, `setFixedWidth(0)`, no tooltip; else `sugg_lbl.show()`, `setFixedWidth(60)`, text `suggested`; `agree = self.bow_edit.text().strip() == suggested`; label colour `styles.TEXT_DIM` when agree else `styles.AMBER_TEXT`; tooltip `f"heard {suggested} — A accepts"`. In every case: `self.bow_edit.setStyleSheet(self._bow_style())` then `self.apply_deleted_style()` (setStyleSheet resets the strike font — same reason as the existing comment in `set_selected`).
- `_Row.set_highlight(on)`: additionally `self._selected = on`; `_Row.set_suggestion(suggested)`: store + restyle; `_Row.set_bow(text)` (from sheet 06): after setting the field text also restyle.
- The `bow_edit.editingFinished` lambda: emit `bow_edited` then `self._restyle_suggestion()`.
- `CrossingList.set_selected`: for editable rows replace `self._edits[seq].setStyleSheet(self._input_style(...))` + `row.apply_deleted_style()` with `row.set_highlight(seq == sequence); row._restyle_suggestion()`. Keep `_input_style` as a thin method delegating to the same string (nothing else calls it, but leave it).
- `CrossingList.set_suggestion(self, sequence, suggested)`; `CrossingList.set_bow(self, sequence, value)` → `row.set_bow(value)`.

### Tests
`tests/test_controller_scratch.py` (append): `test_set_bow_number_asr_source` (`bow_source == "asr"`); `test_set_bow_number_rejects_unknown_source` (`ValueError`).
`tests/test_crossing_list.py` (append): `test_suggestion_hidden_when_absent` (`row.sugg_lbl.isHidden()` and `width() == 0`; row `minimumSizeHint().width()` equals a row built without the key); `test_suggestion_amber_when_bow_differs_or_empty` (stylesheet of `bow_edit` contains `styles.AMBER`); `test_suggestion_dim_when_bow_matches`; `test_selected_row_keeps_blue_without_suggestion` (`set_selected(1)` → `bow_edit` stylesheet contains `styles.BLUE`); `test_selected_row_with_disagreeing_suggestion_is_amber`; `test_set_bow_restyles` (`set_bow(1,"14")` with suggestion "14" → dim); `test_readonly_list_ignores_suggestion` (no `sugg_lbl` attribute on read-only rows).

**Commit:** `feat(ui): ASR suggestion column, bow_source='asr' (§13.3.4 step 18)`
