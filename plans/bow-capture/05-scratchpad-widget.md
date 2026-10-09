# Sheet 05 — `ui/scratchpad_widget.py`

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** new `hallofframe/ui/scratchpad_widget.py`, new `tests/test_scratchpad_widget.py`.
**Read first:** `hallofframe/ui/styles.py` (colour/font names), `hallofframe/ui/crossing_list.py` lines 82–135 (`_BowEdit`: how a field claims keys in `event()`), `tests/test_crossing_list.py` (Qt test style: `pytest.importorskip("PySide6")`, `qapp` fixture, `QKeyEvent`).

### Goal
One widget: a digits-only field, two counters. It never touches storage or the controller; it emits signals.

### Spec
```python
class ScratchpadWidget(QWidget):
    committed = Signal(str)       # normalized text, on Enter
    undo_requested = Signal()     # Backspace on an EMPTY field

    def __init__(self, parent=None): ...
        # self.field: QLineEdit — placeholder "bow", maxLength 4, QRegularExpressionValidator(r"[0-9]{0,4}"),
        #   mono font, font-size 34px, fixed height 64, StrongFocus. Buttons: none.
        # self.typed_lbl, self.cross_lbl: QLabel mono 28px; caption labels "typed" / "crossings" (14px, TEXT_FAINT).
        # Layout: [field][typed_lbl "0"] "typed" [cross_lbl "0"] "crossings"
    def set_counts(self, typed: int, crossings: int) -> None:
        """Set both labels. Colour: styles.RED_TEXT on both when typed != crossings, else styles.TEXT_PRIMARY."""
    def mismatch(self) -> bool: ...
    def flash(self) -> None:
        """Border styles.RED for 300 ms (QTimer.singleShot), then back."""
    def focus_field(self) -> None: self.field.setFocus(); 
```
Key handling in a `_Field(QLineEdit)` subclass with `__init__(self, owner: "ScratchpadWidget")` storing `self._owner = owner`; its `event()` (same technique as `_BowEdit`) calls the owner by attribute lookup at call time (`self._owner.committed.emit(t)`, `self._owner.undo_requested.emit()`, `self._owner.flash()`):
- `Return`/`Enter` (KeyPress): `t = scratchpad.normalize(self.text())`; if `t`: `owner.committed.emit(t)`, `clear()`; else `owner.flash()`. Return True (consumed).
- `Backspace` (KeyPress) when `text() == ""`: `owner.undo_requested.emit()`; True.
- `ShortcutOverride` with `Qt.ControlModifier` set (`Ctrl+Z` undo-last-crossing, `Ctrl+Q`, `Ctrl+S`): `event.ignore(); return False` — a plain QLineEdit would otherwise claim Ctrl+Z as text-undo and the app's "Undo last" would go dead while the field has focus.
- `ShortcutOverride` for Return/Enter: `event.accept(); return True` (belt and braces over `MainWindow._enable_shortcuts`; digits and Backspace are already claimed by QLineEdit itself).
- Everything else: `super().event(event)`.

### Tests — `tests/test_scratchpad_widget.py` (`pytestmark = pytest.mark.qt`)
Helper `press(widget.field, key, text="")` sends `QKeyEvent(KeyPress, key, NoModifier, text)` via `qapp.sendEvent` then `processEvents()`.

| Test | Steps | Expected |
|---|---|---|
| `test_enter_commits_digits` | type `1`,`4`; Enter | `committed` got `["14"]`; field empty |
| `test_enter_on_empty_flashes_only` | Enter | no signal; `widget.styleSheet()` contains `styles.RED` right after (flash active) |
| `test_ctrl_z_not_claimed` | send `QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Z, Qt.ControlModifier)` to the field | `not event.isAccepted()` (pattern: `tests/test_crossing_list.py` lines ~119–129) |
| `test_return_is_claimed` | same with `Qt.Key_Return`, `NoModifier` | `event.isAccepted()` |
| `test_letters_rejected_by_validator` | `field.insert("a")` | `field.text() == ""` |
| `test_backspace_on_empty_requests_undo` | Backspace | `undo_requested` once |
| `test_backspace_with_text_edits` | type `1`,`4`; Backspace | text `"1"`, no `undo_requested` |
| `test_counts_red_on_mismatch` | `set_counts(2,3)` | labels `"2"`,`"3"`; `mismatch()` True; stylesheet contains `styles.RED_TEXT` |
| `test_counts_plain_when_equal` | `set_counts(3,3)` | `mismatch()` False |
| `test_no_focusable_buttons` | — | `findChildren(QPushButton) == []` |

**Commit:** `feat(ui): ScratchpadWidget (§13.3 step 05)`
