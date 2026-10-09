# Sheet 02 — `scratchpad.py`: the assignment rule (pure, no I/O)

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** new `hallofframe/scratchpad.py`, new `tests/test_scratchpad.py`.
**Read first:** `hallofframe/roster.py` lines 1–40 (style of a Qt-free module), `tests/test_session.py` (style).

### Goal
Two pure functions. No imports from storage/controller/Qt.

### Spec
```python
"""Live bow-number scratchpad rules (spec §13.3.2): the k-th typed number is
attached to the k-th crossing. Pure functions; the controller does the I/O."""
from __future__ import annotations
from typing import Sequence

DIGITS = frozenset("0123456789")
LOCKED_SOURCES = ("manual", "asr")   # bow_source values the scratchpad never overwrites

def normalize(text: str) -> str | None:
    """Strip; return the text if it is 1–4 ASCII digits, else None."""

def assign(entries: Sequence[str],
           captures: Sequence[tuple[int, str | None, str | None]]
           ) -> list[tuple[int, str | None]]:
    """entries: texts in ordinal order (non-deleted only).
    captures: (capture_id, bow_number, bow_source) in sequence order (non-deleted only).
    For index i, capture (cid, bow, src):
      locked = src in LOCKED_SOURCES or (src is None and bow)   # LOCKED_SOURCES = ("manual", "asr"): typed/accepted in review, or a pre-feature row
      if locked: continue                                  # still consumes position i
      want = entries[i] if i < len(entries) else None
      if want != bow: append (cid, want)
    Returns only the rows that must change. Idempotent: assign() on the result of apply is []."""
```

### Tests — `tests/test_scratchpad.py` (pure, no fixtures)
| Test | Input | Expected |
|---|---|---|
| `test_normalize` | `"14"`,`" 7 "`,`""`,`"1a"`,`"12345"`,`"٣"` (Arabic digit) | `"14"`,`"7"`,`None`,`None`,`None`,`None` |
(entries are always **strings**)
| `test_assign_in_order` | entries `["14","7","21"]`, caps `[(1,None,None),(2,None,None),(3,None,None)]` | `[(1,"14"),(2,"7"),(3,"21")]` |
| `test_fewer_entries` | `["14","7"]`, 4 empty caps | `[(1,"14"),(2,"7")]` (3,4 untouched: already None) |
| `test_more_entries` | `["14","7","21"]`, 2 caps | `[(1,"14"),(2,"7")]` |
| `test_manual_keeps_position` | `["14","7","21"]`, caps `[(1,None,None),(2,"99","manual"),(3,None,None)]` | `[(1,"14"),(3,"21")]` |
| `test_asr_accepted_is_locked_too` | same with `(2,"99","asr")` | `[(1,"14"),(3,"21")]` |
| `test_legacy_bow_without_source_is_locked` | `["14"]`, caps `[(1,"5",None)]` | `[]` |
| `test_entry_removed_shifts_later` | `["14","21"]`, caps `[(1,"14","live"),(2,"7","live"),(3,"21","live")]` | `[(2,"21"),(3,None)]` |
| `test_idempotent` | `["14","7"]`, caps `[(1,"14","live"),(2,"7","live")]` | `[]` |
| `test_no_captures` | `["14"]`, `[]` | `[]` |

**Commit:** `feat(scratchpad): pure assignment rule (§13.3 step 02)`
