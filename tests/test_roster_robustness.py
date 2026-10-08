"""Robust roster loading (BEHAVIOUR §4).

The race list is a CSV exported from Excel on race morning. Realistic exports
are frequently not clean UTF-8 comma-separated text, and several of them used
to abort application start because the exception escaped ``Roster.load()``.
Timing must never depend on the CSV parsing cleanly: every one of these files
must load without raising, good rows still load, and the problem is reported
(``file_error``/``warnings``/``errors``) instead.
"""
from __future__ import annotations

import pytest

from hallofframe.roster import Roster, RosterWriteError

# name, raw bytes, expected ok, expected len(races), file_error substring,
# warning substring, expected errors
CASES = [
    ("cp1252",
     "race_no,heat_no,name\n101,1,Mæn østå\n".encode("cp1252"),
     True, 1, "", "Windows-1252", []),
    ("utf16",
     "race_no,heat_no,name\n101,1,Mæn østå\n".encode("utf-16"),
     True, 1, "", "", []),
    ("xlsx",
     b"PK\x03\x04junkjunkjunk",
     False, 0, "Excel workbook", "", []),
    ("large_field",
     ("race_no,heat_no,name\n101,1," + "x" * 200_000 + "\n").encode(),
     True, 1, "", "", []),
    ("semicolon",
     b"race_no;heat_no;name\n101;1;A\n102;1;B\n",
     True, 2, "", "semicolon-separated", []),
    ("caps_header",
     b"Race,Heat,Name\n101,1,Final\n",
     True, 1, "", "non-standard header", []),
    ("ragged",
     b"race_no,heat_no,name\n101,1,Final A\n2\n102,1,Final B\n",
     True, 2, "", "", [(3, "expected race_no, heat_no, name")]),
    ("ragged_no_header",
     b"101,1,Final A\n2\n102,1,Final B\n",
     True, 2, "", "", [(2, "expected race_no, heat_no, name")]),
    ("unbalanced_quote",
     b'race_no,heat_no,name\n101,1,"unterminated\n',
     True, 1, "", "", []),
    ("nul",
     b"race_no,heat_no,name\n101,1,A\x00B\n",
     True, 1, "", "", []),
    ("blank_lines",
     b"\nrace_no,heat_no,name\n\n101,1,A\n\n",
     True, 1, "", "", []),
    ("empty",
     b"",
     True, 0, "", "", []),
    ("header_only",
     b"race_no,heat_no,name\n",
     True, 0, "", "", []),
]


@pytest.mark.parametrize(
    "name,data,ok,n,file_err,warn,errors", CASES, ids=[c[0] for c in CASES])
def test_fixture_loads_without_raising(tmp_path, name, data, ok, n,
                                       file_err, warn, errors):
    p = tmp_path / f"{name}.csv"
    p.write_bytes(data)

    result = Roster(str(p)).result  # must not raise

    assert result.ok is ok
    assert len(result.races) == n
    if file_err:
        assert file_err in result.file_error
    else:
        assert result.file_error == ""
    assert result.errors == errors
    if warn:
        assert any(warn in w for w in result.warnings)
    else:
        assert result.warnings == []


def test_leading_blank_line_can_still_be_edited(tmp_path):
    p = tmp_path / "races.csv"
    p.write_bytes(b"\nrace_no,heat_no,name\n101,1,A\n102,1,B\n")
    r = Roster(str(p))
    assert r.result.ok
    r.skip(("num", "101", "1"), True)   # must not raise IndexError
    assert ("num", "101", "1") in r.skipped_keys()


def test_undefined_cp1252_byte_loads_with_replacement(tmp_path):
    p = tmp_path / "races.csv"
    p.write_bytes(b"race_no,heat_no,name\n101,1,A\x81B\n")
    result = Roster(str(p)).result
    assert result.ok
    assert len(result.races) == 1
    assert any("Windows-1252" in w for w in result.warnings)


def test_directory_path_reports_file_error(tmp_path):
    result = Roster(str(tmp_path)).result
    assert not result.ok
    assert result.file_error
    assert result.races == []
    assert result.path == str(tmp_path)


def test_semicolon_file_stays_semicolon_after_edits(tmp_path):
    p = tmp_path / "races.csv"
    p.write_bytes(b"race_no;heat_no;name\n101;1;A\n102;1;B\n")
    r = Roster(str(p))
    assert r.delimiter == ";"
    assert r.result.delimiter == ";"

    r.skip(("num", "101", "1"), True)
    r.move(("num", "102", "1"), -1)

    first_line = p.read_text(encoding="utf-8").splitlines()[0]
    assert ";" in first_line and "," not in first_line
    assert r.delimiter == ";"
    # The edits landed: 102 moved above 101, and 101 carries the status.
    assert [x.race_no for x in r.races] == ["102", "101"]
    assert ("num", "101", "1") in r.skipped_keys()


@pytest.mark.parametrize("method,args", [
    ("skip", (("num", "101", "1"), True)),
    ("rename", (("num", "101", "1"), "X")),
    ("add", ("123", "1", "New")),
    ("move", (("num", "101", "1"), +1)),
])
def test_mutators_refuse_when_unreadable(tmp_path, method, args):
    # A directory cannot be read; every mutator reports it instead of crashing.
    r = Roster(str(tmp_path))
    with pytest.raises(RosterWriteError, match="could not be read"):
        getattr(r, method)(*args)
