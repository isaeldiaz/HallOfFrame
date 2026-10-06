"""Phase 0, step 0.3 — Golden files.

Seed a storage with two races and four crossings using FIXED timestamps (no
``time.time()`` anywhere), render the six exported surfaces and compare them
byte-for-byte against ``tests/goldens/``. Regenerate with::

    ./venv/bin/python -m pytest tests/test_goldens.py --update-goldens

The only volatile bits in the output are the build stamp / version in the web
footers (stripped by :func:`_normalize`) and ``datetime.now()`` in the HTML
export plus the local timezone used by ``local_hms`` (frozen to UTC by the
``fixed_clock`` fixture, so the goldens are machine-independent).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from hallofframe import __version__, buildinfo
from hallofframe import export, web

GOLDENS = Path(__file__).resolve().parent / "goldens"

# Fixed epoch: 2023-11-14T22:13:20Z (second race starts ten minutes later).
T0_WALL = 1_700_000_000.0
T0_WALL_B = T0_WALL + 600.0
FROZEN_NOW = (2024, 1, 2, 3, 4, 5)


@pytest.fixture
def fixed_clock(monkeypatch):
    """Make the export module's clock deterministic and timezone-independent.

    ``build_all_html`` stamps the page with ``datetime.now()`` and ``local_hms``
    renders in the machine's local timezone; both would otherwise change between
    runs/machines. Patching the module-level ``datetime`` name fixes ``now()``
    and pins ``fromtimestamp`` to UTC without touching the exported functions.
    """

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(*FROZEN_NOW, tzinfo=tz or _dt.timezone.utc)

        @classmethod
        def fromtimestamp(cls, ts, tz=_dt.timezone.utc):
            return _dt.datetime.fromtimestamp(ts, tz)

    class _Shim:
        datetime = _FixedDatetime
        timezone = _dt.timezone

    monkeypatch.setattr(export, "datetime", _Shim)
    return _Shim


@pytest.fixture
def update_goldens(request):
    return request.config.getoption("--update-goldens")


@pytest.fixture
def seeded(storage, fixed_clock):
    """Two races (3 + 1 crossings) with fixed, mutually consistent timestamps."""
    boot = "test-boot"

    race_a = storage.create_race(
        "Men under 18, single, final", 1000.0, T0_WALL, "direct", 0.0, 0.0,
        "water", 30.0, boot_id=boot, race_no="101", heat_no="1")
    race_b = storage.create_race(
        "Women open, double, heat", 2000.0, T0_WALL_B, "direct", 0.0, 0.0,
        "water", 30.0, boot_id=boot, race_no="102", heat_no="2")

    specs_a = [
        (1, 6.12, "07", "races/Race-20231114-2213/cap_0001.jpg", None,
         "photo finish"),
        (2, 7.45, "04", None, "approximate", None),
        (3, 8.01, "12", None, "missing", "lane 3"),
    ]
    for seq, elapsed, bow, image, flag, notes in specs_a:
        cap = storage.insert_capture(
            race_a, seq, 1000.0 + elapsed, T0_WALL + elapsed, elapsed, 0.0,
            image_flag=flag, bow_number=bow, notes=notes)
        if image:
            storage.update_capture(cap, primary_image=image)

    cap_b = storage.insert_capture(
        race_b, 1, 2000.0 + 9.99, T0_WALL_B + 9.99, 9.99, 0.0, bow_number="03")
    storage.update_capture(
        cap_b, primary_image="races/Race-20231114-2213/cap_0002.jpg")

    storage.mark_race_reviewed(race_a)
    storage.mark_race_reviewed(race_b)
    return storage


def _normalize(text: str) -> str:
    """Strip the build stamp and the app version (both change with the build)."""
    stamp = buildinfo.build_stamp()
    if stamp:
        text = text.replace(stamp, "<BUILD_STAMP>")
    return text.replace(__version__, "<VERSION>")


def _check(name: str, actual: str, update: bool) -> None:
    actual = _normalize(actual)
    path = GOLDENS / name
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(actual)
        return
    assert path.exists(), f"missing golden {name!r}; run with --update-goldens"
    with open(path, "r", encoding="utf-8", newline="") as fh:
        expected = fh.read()
    assert actual == expected, f"golden mismatch for {name!r}"


def test_export_csv_golden(seeded, data_root, update_goldens):
    out = data_root / "race.csv"
    export.export_csv(seeded, 1, out)
    _check("export_csv.golden", out.read_text(encoding="utf-8"), update_goldens)


def test_export_all_csv_golden(seeded, data_root, update_goldens):
    out = data_root / "all.csv"
    export.export_all_csv(seeded, out)
    _check("export_all_csv.golden", out.read_text(encoding="utf-8"),
           update_goldens)


def test_export_all_html_golden(seeded, data_root, update_goldens):
    out = data_root / "all.html"
    export.export_all_html(seeded, out)
    _check("export_all_html.golden", out.read_text(encoding="utf-8"),
           update_goldens)


def test_clipboard_data_golden(seeded, update_goldens):
    tsv, markup = export.clipboard_data(seeded, 1)
    combined = f"<<<TSV>>>\n{tsv}<<<HTML>>>\n{markup}\n"
    _check("clipboard_data.golden", combined, update_goldens)


def test_build_index_golden(seeded, update_goldens):
    _check("build_index.golden", web.build_index(seeded), update_goldens)


def test_build_race_page_golden(seeded, update_goldens):
    page = web.build_race_page(seeded, 1)
    assert page is not None
    _check("build_race_page.golden", page, update_goldens)
