"""Roster CSV reading/writing and the Qt-free ``Roster`` class (plan step 2.7).

Merges the former ``test_races.py`` and ``test_roster_write.py``. Covers the CSV
format (``race_no,heat_no,name,source,status``; a 3-column legacy file still
loads; ``status=skipped`` marks a skipped row), the single atomic write path
(BEHAVIOUR §2), and every ``Roster`` method: ``move`` at both ends, ``move`` on a
legacy file (pads to 5 columns), ``skip`` round-trip, ``add`` collision and
``add`` with ``after_key``, and concurrent-edit refusal.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hallofframe.roster import (HEADER_5, RaceInfo, Roster, RosterWriteError,
                                format_display, load_races, race_key, read_rows,
                                rename_race, skip_race, write_example)


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "races.csv"
    write_example(p)
    return p


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _data(p: Path) -> list[list[str]]:
    rows = read_rows(p)
    assert rows is not None
    return rows[1:]


# --- loading (BEHAVIOUR §1, §4) ------------------------------------------

def test_write_and_read_roundtrip(tmp_path):
    p = tmp_path / "races.csv"
    races = [RaceInfo("101", "1", "Men under 18, single, final"),
             RaceInfo("104", "2", "Men under 16, double, heat")]
    write_example(p, races)
    assert load_races(p).races == races


def test_skips_header_and_blank_rows(tmp_path):
    p = tmp_path / "races.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([
            ["race_no", "heat_no", "name"],
            ["101", "1", "Final A"],
            [],
            ["102", "1", "Final B"],
        ])
    assert [r.display for r in load_races(p).races] == [
        "101-H1 - Final A", "102-H1 - Final B"]


def test_race_key_normalisation():
    # Leading zeros and the H-heat prefix are equal under normalisation.
    assert race_key("0102", "1", "X") == race_key("102", "1", "Y")
    assert race_key("102", "H1", "X") == race_key("102", "1", "Y")
    assert race_key("007", "H2", "X") == race_key("7", "2", "Y")
    assert race_key("102", "", "X") == race_key("102", " ", "Y")
    assert race_key(" 102 ", "1", "X") == race_key("102", "1", "Y")
    # Empty heat is a valid, distinct key component.
    assert race_key("102", "", "X") != race_key("102", "1", "X")
    # Case is folded.
    assert race_key("A", "1", "X") == race_key("a", "1", "Y")
    # A legacy name-only row never equals a numbered row.
    assert race_key("", "", "Heat 1") != race_key("102", "1", "Heat 1")
    assert race_key("", "", "Heat 1") == race_key("", "", "heat 1")


def test_twocolumn_race_heat_no_name_is_malformed_not_rekeyed(tmp_path):
    # A row with a heat number but no name must not be re-keyed by treating
    # column A as the name (that would drop the race number from identity).
    p = tmp_path / "races.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["race_no", "heat_no", "name"], ["101", "2", ""]])
    r = load_races(p)
    assert r.errors == [(2, "expected race_no, heat_no, name")]
    assert not r.ok


def test_legacy_one_column_roster(tmp_path):
    p = tmp_path / "races.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["Heat 1"], ["Heat 2"]])
    assert [r.display for r in load_races(p).races] == ["Heat 1", "Heat 2"]
    # race/heat fields stay empty so the legacy row keys on its name
    assert load_races(p).races[0].key == ("name", "heat 1", "")


def test_missing_file_reports_missing(tmp_path):
    r = load_races(tmp_path / "nope.csv")
    assert r.missing
    assert not r.ok
    assert r.races == []
    assert not (tmp_path / "nope.csv").exists()


def test_malformed_row_reports_line_no_no_roster(tmp_path):
    p = tmp_path / "races.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([
            ["race_no", "heat_no", "name"],
            ["101", "1", "Final A"],
            ["", "1", ""],           # no usable name
            ["102", "1", "Final B"],
        ])
    r = load_races(p)
    assert r.errors == [(3, "expected race_no, heat_no, name")]
    assert not r.ok
    # no roster loads at all on a malformed row (BEHAVIOUR §4)
    assert r.races == []


def test_duplicate_normalised_keys_reported_first_wins(tmp_path):
    p = tmp_path / "races.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([
            ["race_no", "heat_no", "name"],
            ["0102", "1", "First"],
            ["102", "1", "Second"],   # same normalised key as line 2
            ["103", "1", "Heat"],
        ])
    r = load_races(p)
    assert r.ok
    assert len(r.races) == 2
    assert r.races[0].display == "0102-H1 - First"
    assert len(r.duplicates) == 1
    key, l_a, l_b = r.duplicates[0]
    assert key == ("num", "102", "1")
    assert (l_a, l_b) == (2, 3)


def test_format_display():
    assert format_display("101", "1", "Final A") == "101-H1 - Final A"
    assert format_display("", "", "Legacy") == "Legacy"
    assert format_display("101", "", "") == "101"


# --- the atomic write path (BEHAVIOUR §2, §3) ----------------------------

def test_bak_holds_previous_good_file(csv_path):
    before = _text(csv_path)
    rename_race(csv_path, ("num", "101", "1"), "Renamed")
    assert (csv_path.parent / "races.csv.bak").read_text() == before


def test_rename_leaves_other_rows_byte_identical(csv_path):
    before = _text(csv_path)
    rename_race(csv_path, ("num", "101", "1"), "Renamed")
    after_lines = _text(csv_path).splitlines()
    before_lines = before.splitlines()
    assert after_lines[2] == before_lines[2]   # 102 row untouched
    assert after_lines[3] == before_lines[3]   # 103 row untouched
    assert after_lines[1].startswith("101,1,Renamed")


def test_rename_sets_source_edited(csv_path):
    rename_race(csv_path, ("num", "101", "1"), "Renamed")
    row = next(r for r in _data(csv_path) if r[0] == "101")
    assert row[3] == "edited"


def test_add_appends_at_end(tmp_path):
    p = tmp_path / "races.csv"
    p.write_text("103,1,High\n101,1,Low\n", encoding="utf-8")
    from hallofframe.roster import add_row
    add_row(p, "102", "1", "Middle")
    assert [r[0] for r in _data(p)] == ["103", "101", "102"]


def test_add_row_inserts_after_after_key(csv_path):
    from hallofframe.roster import add_row
    add_row(csv_path, "102", "2", "New heat", after_key=("num", "102", "1"))
    rows = _data(csv_path)
    assert rows[2][:3] == ["102", "2", "New heat"]   # right after 102-H1
    assert rows[3][0] == "103"


def test_first_save_expands_legacy_to_5_columns(tmp_path):
    p = tmp_path / "races.csv"
    p.write_text("101,1,Final A\n102,1,Final B\n", encoding="utf-8")
    rename_race(p, ("num", "101", "1"), "Renamed")
    assert _text(p).splitlines()[0] == ",".join(HEADER_5)
    assert len(_text(p).splitlines()[1].split(",")) == 5


def test_add_row_collision_writes_nothing(csv_path):
    from hallofframe.roster import add_row
    before = _text(csv_path)
    result, outcome = add_row(csv_path, "101", "1", "Dup")
    assert outcome == "collision"
    assert _text(csv_path) == before


def test_skip_is_reversible_and_stored_in_status(csv_path):
    skip_race(csv_path, ("num", "103", "1"), skip=True)
    row = next(r for r in _data(csv_path) if r[0] == "103")
    assert row[4] == "skipped"
    skip_race(csv_path, ("num", "103", "1"), skip=False)
    row = next(r for r in _data(csv_path) if r[0] == "103")
    assert row[4] == ""


def test_mutation_refuses_when_changed_on_disk(csv_path):
    expected = read_rows(csv_path)
    csv_path.write_text("999,1,Changed on disk\n", encoding="utf-8")
    with pytest.raises(RosterWriteError):
        rename_race(csv_path, ("num", "101", "1"), "X", expected=expected)


def test_failed_write_leaves_target_unchanged(csv_path):
    expected = read_rows(csv_path)
    # Remove the file behind the loader's back; the mutation must not run.
    csv_path.unlink()
    with pytest.raises(RosterWriteError):
        rename_race(csv_path, ("num", "101", "1"), "X", expected=expected)
    assert not csv_path.exists()


# --- the Roster class (plan step 2.3) ------------------------------------

def test_load_missing(tmp_path):
    r = Roster(str(tmp_path / "nope.csv"))
    assert r.result.missing
    assert r.races == []
    assert r.rows is None


def test_load_switches_path(tmp_path):
    p = tmp_path / "races.csv"
    write_example(p)
    r = Roster(None)
    assert r.path is None
    result = r.load(str(p))
    assert result.ok
    assert r.path == str(p)
    assert len(r.races) == 5


def test_races_drops_duplicates_first_wins(tmp_path):
    p = tmp_path / "races.csv"
    p.write_text("0102,1,First\n102,1,Second\n103,1,Heat\n", encoding="utf-8")
    r = Roster(str(p))
    assert [x.race_no for x in r.races] == ["0102", "103"]
    assert len(r.result.duplicates) == 1


def test_skipped_keys_empty_for_legacy_file(tmp_path):
    p = tmp_path / "races.csv"
    p.write_text("Heat 1\nHeat 2\n", encoding="utf-8")
    r = Roster(str(p))
    assert r.skipped_keys() == set()


def test_skip_round_trip(csv_path):
    r = Roster(str(csv_path))
    assert ("num", "103", "1") not in r.skipped_keys()
    r.skip(("num", "103", "1"), True)
    assert ("num", "103", "1") in r.skipped_keys()
    r.skip(("num", "103", "1"), False)
    assert ("num", "103", "1") not in r.skipped_keys()
    # the loaded rows were refreshed for the next optimistic check
    assert r.rows == read_rows(csv_path)


def test_move_up_swaps_data_rows(csv_path):
    r = Roster(str(csv_path))
    r.move(("num", "103", "1"), -1)
    assert [row[0] for row in _data(csv_path)] == \
        ["101", "103", "102", "104", "105"]


def test_move_down_swaps_data_rows(csv_path):
    r = Roster(str(csv_path))
    r.move(("num", "103", "1"), +1)
    assert [row[0] for row in _data(csv_path)] == \
        ["101", "102", "104", "103", "105"]


def test_move_refuses_at_top(csv_path):
    r = Roster(str(csv_path))
    with pytest.raises(RosterWriteError):
        r.move(("num", "101", "1"), -1)
    assert [row[0] for row in _data(csv_path)] == ["101", "102", "103", "104", "105"]


def test_move_refuses_at_bottom(csv_path):
    r = Roster(str(csv_path))
    with pytest.raises(RosterWriteError):
        r.move(("num", "105", "1"), +1)
    assert [row[0] for row in _data(csv_path)] == ["101", "102", "103", "104", "105"]


def test_move_on_legacy_file_pads_to_5_columns(tmp_path):
    p = tmp_path / "races.csv"
    p.write_text("103,1,High\n101,1,Low\n102,1,Mid\n", encoding="utf-8")
    r = Roster(str(p))
    r.move(("num", "101", "1"), +1)   # swap 101 and 102
    assert _text(p).splitlines()[0] == ",".join(HEADER_5)
    assert [row[0] for row in _data(p)] == ["103", "102", "101"]
    assert all(len(row) == 5 for row in _data(p))


def test_add_collision(csv_path):
    r = Roster(str(csv_path))
    before = _text(csv_path)
    result, outcome = r.add("101", "1", "Dup")
    assert outcome == "collision"
    assert _text(csv_path) == before


def test_add_with_after_key(csv_path):
    r = Roster(str(csv_path))
    result, outcome = r.add("102", "2", "New heat",
                            after_key=("num", "102", "1"))
    assert outcome == "ok"
    rows = _data(csv_path)
    assert rows[2][:3] == ["102", "2", "New heat"]
    assert rows[3][0] == "103"
    assert any(x.heat_no == "2" for x in r.races)


def test_add_appends_without_after_key(csv_path):
    r = Roster(str(csv_path))
    r.add("999", "1", "Appended")
    assert _data(csv_path)[-1][:3] == ["999", "1", "Appended"]


def test_rename_via_roster(csv_path):
    r = Roster(str(csv_path))
    r.rename(("num", "101", "1"), "Renamed")
    assert next(x for x in r.races if x.race_no == "101").name == "Renamed"


def test_write_example_via_roster(tmp_path):
    p = tmp_path / "races.csv"
    r = Roster(str(p))
    assert r.result.missing
    r.write_example()
    assert p.exists()
    assert not r.result.missing
    assert len(r.races) == 5


@pytest.mark.parametrize("method,args", [
    ("skip", (("num", "101", "1"), True)),
    ("rename", (("num", "101", "1"), "X")),
    ("add", ("123", "1", "New")),
    ("move", (("num", "101", "1"), +1)),
])
def test_concurrent_edit_refused(csv_path, method, args):
    r = Roster(str(csv_path))
    r.load()
    # The file changes underneath the operator; every write must be refused.
    csv_path.write_text("999,1,Changed on disk\n", encoding="utf-8")
    with pytest.raises(RosterWriteError):
        getattr(r, method)(*args)
