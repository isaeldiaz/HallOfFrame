"""Live bow-number scratchpad rules (spec §13.3 step 02). Pure, no fixtures."""
from __future__ import annotations

from hallofframe.scratchpad import assign, normalize


def test_normalize():
    assert normalize("14") == "14"
    assert normalize(" 7 ") == "7"
    assert normalize("") is None
    assert normalize("1a") is None
    assert normalize("12345") is None
    assert normalize("٣") is None


def test_assign_in_order():
    entries = ["14", "7", "21"]
    caps = [(1, None, None), (2, None, None), (3, None, None)]
    assert assign(entries, caps) == [(1, "14"), (2, "7"), (3, "21")]


def test_fewer_entries():
    caps = [(1, None, None), (2, None, None), (3, None, None), (4, None, None)]
    assert assign(["14", "7"], caps) == [(1, "14"), (2, "7")]


def test_more_entries():
    caps = [(1, None, None), (2, None, None)]
    assert assign(["14", "7", "21"], caps) == [(1, "14"), (2, "7")]


def test_manual_keeps_position():
    entries = ["14", "7", "21"]
    caps = [(1, None, None), (2, "99", "manual"), (3, None, None)]
    assert assign(entries, caps) == [(1, "14"), (3, "21")]


def test_asr_accepted_is_locked_too():
    entries = ["14", "7", "21"]
    caps = [(1, None, None), (2, "99", "asr"), (3, None, None)]
    assert assign(entries, caps) == [(1, "14"), (3, "21")]


def test_legacy_bow_without_source_is_locked():
    assert assign(["14"], [(1, "5", None)]) == []


def test_entry_removed_shifts_later():
    entries = ["14", "21"]
    caps = [(1, "14", "live"), (2, "7", "live"), (3, "21", "live")]
    assert assign(entries, caps) == [(2, "21"), (3, None)]


def test_idempotent():
    entries = ["14", "7"]
    caps = [(1, "14", "live"), (2, "7", "live")]
    assert assign(entries, caps) == []


def test_no_captures():
    assert assign(["14"], []) == []
