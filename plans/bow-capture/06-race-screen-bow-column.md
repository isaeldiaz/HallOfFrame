# Sheet 06 — race screen: bow column + scratchpad slot

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/ui/crossing_list.py`, `hallofframe/ui/race_screen.py`, new `tests/test_race_screen_scratch.py`.
**Read first:** both UI files (whole), `tests/test_crossing_list.py`.

### Goal
The read-only crossing list shows each row's bow number live; the race screen can host the scratchpad under the list and re-focus it.

### Spec
`crossing_list.py`:
- `_Row.__init__`: in the **non-editable** branch add `self.bow_lbl = QLabel(data.get("bow") or "")` (mono, 30px, `TEXT_PRIMARY`, fixed width 90, centred), inserted right after the time label. Editable branch unchanged.
- `_Row.set_bow(self, text: str | None)`: non-editable → `bow_lbl.setText(text or "")`; editable → `bow_edit.setText(text or "")` only if the field does not have focus.
- `CrossingList.refresh_bow(self, sequence: int, text: str | None) -> None` → row.set_bow.
- `CrossingList.refresh_bows(self, bows: dict[int, str | None]) -> None` → for each known row.
- `_strike_widgets`: include `bow_lbl` when present.

`race_screen.py`:
- `RaceScreen.set_scratchpad(self, widget: QWidget) -> None`: adds the widget under `self.log` in the right column (wrap right column in a `QVBoxLayout`; keep the 660 px width). Called at most once.
- `RaceScreen.focus_scratchpad(self) -> None`: if a scratchpad was set, `widget.focus_field()`.
- `RaceScreen.set_bows(self, bows: dict) -> None` → `self.log.refresh_bows(bows)`.
- `RaceScreen.add_capture`: after adding the row, `if self._running: self.focus_scratchpad()` (`_running` is set by `begin()`/`end()`). The guard matters: `MainWindow.on_capture` also fires for restore/clone in REVIEW, and `setFocus()` on a hidden page still moves the application focus.

### Tests — `tests/test_race_screen_scratch.py` (qt)
| Test | Steps | Expected |
|---|---|---|
| `test_readonly_row_shows_bow` | `CrossingList(editable=False).add({... "bow": "14"})` | row's `bow_lbl.text() == "14"` |
| `test_refresh_bows` | list with seq 1,2; `refresh_bows({1:"14", 2:None})` | `"14"`, `""` |
| `test_editable_refresh_does_not_clobber_focused_field` | editable list; `lst.show(); processEvents()`; `focus_bow(1)`; assert `lst._rows[1].bow_edit.hasFocus()` first; `refresh_bow(1, "99")` | field text unchanged |
| `test_scratchpad_slot_and_focus` | `RaceScreen()`; `set_scratchpad(ScratchpadWidget())`; `show()`; `begin(0.0)`; `add_capture({...})` | after `processEvents`, `qapp.focusWidget() is widget.field` |
| `test_no_focus_steal_when_not_running` | same but without `begin()`; focus some other widget first; `add_capture` | `qapp.focusWidget()` unchanged |
| `test_screen_minimum_width_unchanged` | `RaceScreen()` with scratchpad | `minimumSizeHint().width() <= 1920` |

**Commit:** `feat(ui): live bow column on the race list, scratchpad slot (§13.3 step 06)`
