"""Live bow-number scratchpad rules (spec §13.3.2): the k-th typed number is
attached to the k-th crossing. Pure functions; the controller does the I/O."""
from __future__ import annotations

from typing import Sequence

DIGITS = frozenset("0123456789")
LOCKED_SOURCES = ("manual", "asr")   # bow_source values the scratchpad never overwrites


def normalize(text: str) -> str | None:
    """Strip; return the text if it is 1–4 ASCII digits, else None."""
    text = text.strip()
    if 1 <= len(text) <= 4 and all(c in DIGITS for c in text):
        return text
    return None


def assign(entries: Sequence[str],
           captures: Sequence[tuple[int, str | None, str | None]]
           ) -> list[tuple[int, str | None]]:
    """entries: texts in ordinal order (non-deleted only).
    captures: (capture_id, bow_number, bow_source) in sequence order (non-deleted only).
    For index i, capture (cid, bow, src):
      locked = src in LOCKED_SOURCES or (src is None and bow)   # typed/accepted in review, or a pre-feature row
      if locked: continue                                  # still consumes position i
      want = entries[i] if i < len(entries) else None
      if want != bow: append (cid, want)
    Returns only the rows that must change. Idempotent: assign() on the result of apply is []."""
    changes: list[tuple[int, str | None]] = []
    for i, (cid, bow, src) in enumerate(captures):
        locked = src in LOCKED_SOURCES or (src is None and bow)
        if locked:
            continue
        want = entries[i] if i < len(entries) else None
        if want != bow:
            changes.append((cid, want))
    return changes
