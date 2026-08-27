"""CSV export, HTML export and Excel-clipboard copy (spec §6.8, requirement F6).

``export_csv`` writes a flat table (one row per crossing): race_no, heat_no,
name, sequence, bow_number, elapsed_seconds, elapsed_formatted,
wall_clock_utc, image_file, image_flag, notes. Elapsed renders as M:SS.mmm with
three decimals. Soft-deleted rows are excluded.

``export_all_html`` writes the whole database as one self-contained HTML page —
a card per crossing showing the captured frame itself. Images are linked by the
relative path already stored in ``primary_image``, so the file must stay next to
the ``races/`` folder inside the data root (the same place the CSV is written).

``clipboard_data`` returns a tab-separated string plus an HTML table so pasting
into Excel keeps column formatting, using the label/value + sorted layout
described in the function's docstring.
"""
from __future__ import annotations

import csv
import datetime
import html
import urllib.parse
from pathlib import Path

from .storage import Storage


def format_elapsed(elapsed_s: float) -> str:
    """M:SS.mmm e.g. 6:12.483."""
    ms = round(elapsed_s * 1000.0)
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def utc_iso(wall_ts: float) -> str:
    dt = datetime.datetime.fromtimestamp(wall_ts, datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local_hms(wall_ts: float) -> str:
    """Local wall-clock time HH:MM:SS (system timezone) for the gun start."""
    dt = datetime.datetime.fromtimestamp(wall_ts)
    return dt.strftime("%H:%M:%S")


def flag_word(image_flag: str | None, suspect: bool | int | None) -> str:
    """The word shown in a crossing's flag column.

    Single source of truth for the three words used by both the live crossing
    list (``ui/crossing_list.flag_word``, which adds a colour) and the HTML
    export, so the app and the exported page can never disagree.
    """
    if image_flag == "missing":
        return "NO IMAGE"
    if image_flag == "approximate":
        return "APPROX"
    if suspect:
        return "DOUBLE?"
    return ""


_COLUMNS = ["race_no", "heat_no", "name", "sequence", "bow_number",
            "elapsed_seconds", "elapsed_formatted", "wall_clock_utc",
            "image_file", "image_flag", "notes"]


def _data_rows(storage: Storage, race_id: int):
    """Yield the exported table as a header row followed by data rows. Each
    data row is prefixed with the race's three identifying fields."""
    race = storage.get_race(race_id)
    race_no = (race["race_no"] or "") if race else ""
    heat_no = (race["heat_no"] or "") if race else ""
    name = (race["name"] or "") if race else ""
    yield list(_COLUMNS)
    captures = storage.captures_for_race(race_id, include_deleted=False)
    for c in captures:
        yield [
            race_no,
            heat_no,
            name,
            c["sequence"],
            c["bow_number"] or "",
            f"{c['elapsed_s']:.6f}",
            format_elapsed(c["elapsed_s"]),
            utc_iso(c["t_press_wall"]),
            c["primary_image"] or "",
            c["image_flag"] or "",
            c["notes"] or "",
        ]


def export_csv(storage: Storage, race_id: int, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for row in _data_rows(storage, race_id):
            writer.writerow(row)
    return out_path


_ALL_COLUMNS = ["race_id", "race_no", "heat_no", "name", "gun_start",
                "sequence", "bow_number", "elapsed_seconds",
                "elapsed_formatted", "wall_clock_utc", "captured_frame_link",
                "image_flag", "notes"]


def _all_race_blocks(storage: Storage):
    """Yield ``(race_row, captures)`` for the whole database.

    Races oldest first, crossings fastest-to-slowest within a race,
    soft-deleted crossings excluded. Every race is yielded — a race with no
    crossings yields an empty capture list rather than being skipped. Both
    ``export_all_csv`` and ``export_all_html`` consume this, so the two
    exporters can never diverge on ordering or filtering.
    """
    for race in storage.all_races():
        captures = storage.captures_for_race(race["id"], include_deleted=False)
        yield race, sorted(captures, key=lambda c: c["elapsed_s"])


def export_all_csv(storage: Storage, out_path: str | Path) -> Path:
    """Dump the entire database to a flat CSV: one row per crossing, grouped by
    race (oldest first) and fastest-to-slowest within a race.

    Every race is listed — a race with no crossings still appears once, with
    empty capture columns. ``race_id`` disambiguates races that share the same
    race_no/heat_no/name (overwrites). Soft-deleted crossings are excluded.
    """
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_ALL_COLUMNS)
        for race, captures in _all_race_blocks(storage):
            t0_wall = race["t0_wall"] if race["t0_wall"] is not None else None
            base = [
                race["id"],
                race["race_no"] or "",
                race["heat_no"] or "",
                race["name"] or "",
                local_hms(t0_wall) if t0_wall is not None else "",
            ]
            if not captures:
                writer.writerow(base + [""] * (len(_ALL_COLUMNS) - len(base)))
                continue
            for c in captures:
                writer.writerow(base + [
                    c["sequence"],
                    c["bow_number"] or "",
                    f"{c['elapsed_s']:.6f}",
                    format_elapsed(c["elapsed_s"]),
                    utc_iso(c["t_press_wall"]),
                    c["primary_image"] or "",
                    c["image_flag"] or "",
                    c["notes"] or "",
                ])
    return out_path


# --- HTML export ----------------------------------------------------------
# Colours mirror ui/styles.py. Kept as literals rather than imported: export
# must not pull in the UI package (PySide6) — it is used from tests and could be
# used from a headless script.
_C = {
    "bg": "#0d1114", "panel": "#11161a", "panel_border": "#1c2429",
    "divider": "#232b31", "text": "#f2f6f8", "text2": "#c6d3da",
    "dim": "#8fa0ab", "faint": "#5f6f79", "letterbox": "#1b262c",
    "finish": "rgba(255,90,66,.9)", "amber": "#ffb43a", "blue": "#6fb2e8",
    "red": "#ff5a42", "input_bg": "#0b0f12",
}

_SANS = "'IBM Plex Sans','Helvetica Neue',Arial,sans-serif"
_MONO = "'IBM Plex Mono','SFMono-Regular',Consolas,monospace"

_THUMB_W, _THUMB_H = 268, 168

_FILTER_JS = """
(function () {
  var box = document.getElementById('q');
  var chips = document.querySelectorAll('[data-preset]');
  var preset = 'all';
  function apply() {
    var q = (box.value || '').toLowerCase().trim();
    var races = document.querySelectorAll('[data-race]');
    for (var i = 0; i < races.length; i++) {
      var race = races[i];
      var cards = race.querySelectorAll('[data-search]');
      var shown = 0;
      for (var j = 0; j < cards.length; j++) {
        var card = cards[j];
        var hay = card.getAttribute('data-search').toLowerCase();
        var ok = (!q || hay.indexOf(q) !== -1)
          && (preset !== 'photo' || card.getAttribute('data-photo') === '1')
          && (preset !== 'flag' || card.getAttribute('data-flag') !== '');
        card.style.display = ok ? '' : 'none';
        if (ok) shown++;
      }
      var raceHay = race.getAttribute('data-race').toLowerCase();
      var raceMatch = !q || raceHay.indexOf(q) !== -1;
      race.style.display = (shown > 0 || (raceMatch && !cards.length && preset === 'all'))
        ? '' : 'none';
    }
  }
  box.addEventListener('input', apply);
  for (var k = 0; k < chips.length; k++) {
    chips[k].addEventListener('click', function (e) {
      preset = e.currentTarget.getAttribute('data-preset');
      for (var m = 0; m < chips.length; m++) {
        var on = chips[m] === e.currentTarget;
        chips[m].style.background = on ? '#151a1e' : 'transparent';
        chips[m].style.borderColor = on ? '#2c3942' : '#1c2429';
        chips[m].style.color = on ? '#c6d3da' : '#8fa0ab';
      }
      apply();
    });
  }
})();
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _src(rel_path: str) -> str:
    """URL-quote a stored relative image path (race folders contain spaces)."""
    return html.escape(urllib.parse.quote(rel_path.replace("\\", "/")), quote=True)


def _row_value(row, key, default=None):
    """Read an optional sqlite3.Row column without assuming the schema."""
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except (AttributeError, IndexError, KeyError):
        pass
    return default


def _thumb_html(capture) -> str:
    rel = capture["primary_image"] or ""
    word = flag_word(capture["image_flag"],
                     _row_value(capture, "debounce_suspect", 0))
    box = (f"position:relative;width:{_THUMB_W}px;height:{_THUMB_H}px;flex:none;"
           f"background:{_C['letterbox']};border-radius:2px;overflow:hidden;"
           "display:flex;align-items:center;justify-content:center")
    badge = ""
    if word:
        badge = (f'<span style="position:absolute;top:8px;left:8px;'
                 f'font-family:{_MONO};font-size:11px;font-weight:600;'
                 f'letter-spacing:.08em;color:{_C["amber"]};'
                 'background:rgba(30,22,8,.92);border:1px solid #4a3b12;'
                 f'border-radius:2px;padding:3px 7px">{_esc(word)}</span>')
    if not rel:
        return (f'<div style="{box};flex-direction:column;gap:10px">'
                f'<span style="font-family:{_MONO};font-size:12px;font-weight:600;'
                f'letter-spacing:.1em;color:{_C["amber"]}">NO IMAGE</span>'
                f'<span style="font-size:12px;color:{_C["faint"]}">timing only</span>'
                "</div>")
    src = _src(rel)
    return (
        f'<a href="{src}" target="_blank" rel="noopener" style="{box}">'
        f'<img src="{src}" loading="lazy" alt="{_esc(rel)}"'
        ' style="width:100%;height:100%;object-fit:contain;display:block">'
        f'<span style="position:absolute;top:0;bottom:0;left:50%;width:2px;'
        f'background:{_C["finish"]}"></span>'
        f"{badge}</a>"
    )


def _meta_cell(label: str, value: str) -> str:
    return (f'<span style="display:flex;flex-direction:column;gap:5px">'
            f'<span style="font-size:12px;letter-spacing:.1em;'
            f'color:{_C["faint"]}">{_esc(label)}</span>'
            f'<span style="font-family:{_MONO};font-size:15px;'
            f'color:{_C["text2"]}">{_esc(value)}</span></span>')


def _card_html(capture) -> str:
    word = flag_word(capture["image_flag"],
                     _row_value(capture, "debounce_suspect", 0))
    bow = capture["bow_number"] or ""
    notes = capture["notes"] or ""
    elapsed = format_elapsed(capture["elapsed_s"])
    elapsed_raw = "%.6f" % capture["elapsed_s"]
    seq = "#%03d" % capture["sequence"]
    search = " ".join(str(v) for v in (capture["sequence"], bow, elapsed, notes,
                                       word) if v)
    note_line = ""
    if word or notes:
        chip = ""
        if word:
            chip = (f'<span style="font-family:{_MONO};font-size:12px;'
                    f'font-weight:600;letter-spacing:.08em;color:{_C["amber"]};'
                    'border:1px solid #4a3b12;border-radius:2px;padding:2px 7px'
                    f'">{_esc(word)}</span>')
        text = (f'<span style="color:{_C["dim"]}">{_esc(notes)}</span>'
                if notes else "")
        note_line = ('<div style="display:flex;align-items:center;gap:10px;'
                     f'font-size:14px;color:{_C["amber"]}">{chip}{text}</div>')
    bow_html = (f'<span style="font-family:{_MONO};font-size:30px;'
                f'font-weight:600;color:{_C["text"] if bow else _C["faint"]}">'
                f'{_esc(bow) if bow else "&mdash;"}</span>')
    return (
        f'<div data-search="{_esc(search)}" data-flag="{_esc(word)}"'
        f' data-photo="{"1" if capture["primary_image"] else "0"}"'
        f' style="display:flex;gap:26px;align-items:stretch;'
        f'background:{_C["panel"]};border:1px solid {_C["panel_border"]};'
        'border-radius:4px;padding:16px">'
        f"{_thumb_html(capture)}"
        '<div style="flex:1;display:flex;flex-direction:column;'
        'justify-content:space-between;gap:16px;padding:4px 0">'
        '<div style="display:flex;align-items:flex-start;'
        'justify-content:space-between;gap:24px">'
        '<div style="display:flex;align-items:baseline;gap:20px">'
        f'<span style="font-family:{_MONO};font-size:44px;font-weight:500;'
        f'color:{_C["text"]};letter-spacing:-.02em">{_esc(elapsed)}</span>'
        '<span style="display:flex;align-items:baseline;gap:9px">'
        f'<span style="font-size:13px;letter-spacing:.1em;'
        f'color:{_C["faint"]}">BOW</span>{bow_html}</span></div>'
        f'<span style="font-family:{_MONO};font-size:13px;color:{_C["faint"]};'
        f'letter-spacing:.06em">{_esc(seq)}</span></div>'
        '<div style="display:flex;flex-direction:column;gap:14px">'
        '<div style="display:flex;gap:44px;flex-wrap:wrap">'
        + _meta_cell("ELAPSED (S)", elapsed_raw)
        + _meta_cell("WALL CLOCK (UTC)", utc_iso(capture["t_press_wall"]))
        + f"</div>{note_line}</div></div></div>"
    )


def _race_html(race, captures) -> str:
    t0_wall = race["t0_wall"]
    gun = local_hms(t0_wall) if t0_wall is not None else "—"
    mode = _row_value(race, "viewing_mode", "") or "—"
    trigger_mode = _row_value(race, "trigger_mode", "")
    if trigger_mode:
        mode = f"{mode} · {trigger_mode}"
    latency = _row_value(race, "latency_s", None)
    delta = f"{float(latency) * 1000:.0f} ms" if latency not in (None, "") else "—"
    label = " · ".join(p for p in (
        f"RACE {race['race_no']}" if race["race_no"] else "UNLISTED",
        f"HEAT {race['heat_no']}" if race["heat_no"] else "") if p)
    reconstructed = _row_value(race, "t0_reconstructed", 0)
    warn = ""
    if reconstructed:
        warn = ('<div style="margin-top:14px;font-size:14px;'
                f'color:{_C["amber"]}">Gun start was reconstructed after a '
                "restart — elapsed times for this race are approximate.</div>")
    search = " ".join(str(v) for v in (race["race_no"] or "", race["heat_no"] or "",
                                       race["name"] or "", label) if v)
    body = ("".join(_card_html(c) for c in captures) if captures else
            f'<div style="padding:22px 0 10px;font-size:15px;'
            f'color:{_C["faint"]}">No crossings recorded.</div>')
    metas = "".join(
        f'<span style="font-size:12px;letter-spacing:.1em;'
        f'color:{_C["faint"]}">{h}</span>' for h in ("GUN START", "MODE", "Δ USED")
    ) + "".join(
        f'<span style="font-family:{_MONO};font-size:17px;color:{c}">{_esc(v)}</span>'
        for v, c in ((gun, _C["text"]), (mode, _C["text2"]), (delta, _C["text2"]))
    )
    return (
        f'<section data-race="{_esc(search)}">'
        '<div style="display:flex;align-items:flex-end;'
        'justify-content:space-between;gap:32px;padding:34px 0 18px;'
        f'border-bottom:1px solid {_C["divider"]};flex-wrap:wrap">'
        '<div style="display:flex;flex-direction:column;gap:8px">'
        '<div style="display:flex;align-items:baseline;gap:14px">'
        f'<span style="font-family:{_MONO};font-size:15px;font-weight:600;'
        f'letter-spacing:.1em;color:{_C["blue"]}">{_esc(label)}</span>'
        f'<span style="font-family:{_MONO};font-size:12px;'
        f'color:{_C["faint"]}">race_id {race["id"]}</span></div>'
        f'<div style="font-size:27px;font-weight:500;color:{_C["text"]};'
        f'letter-spacing:-.01em">{_esc(race["name"] or "")}</div></div>'
        '<div style="display:grid;grid-template-columns:auto auto auto;'
        f'gap:6px 28px;text-align:right">{metas}</div>{warn}</div>'
        f'<div style="display:flex;flex-direction:column;gap:14px;'
        f'padding:24px 0">{body}</div></section>'
    )


def export_all_html(storage: Storage, out_path: str | Path) -> Path:
    """Write the entire database as one HTML page — a card per crossing with the
    captured frame shown, grouped by race (oldest first) and fastest-to-slowest
    within a race.

    Images are referenced by the relative path stored in ``primary_image``, so
    *out_path* must sit in the data root, beside the ``races/`` folder. Every
    race is listed, including races with no crossings. Soft-deleted crossings
    are excluded. No external CSS or JS: the page opens offline, and with
    JavaScript disabled everything except the filter box still works.
    """
    out_path = Path(out_path)
    blocks = list(_all_race_blocks(storage))
    n_races = len(blocks)
    n_caps = sum(len(caps) for _, caps in blocks)
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    chips = "".join(
        f'<button type="button" data-preset="{key}" style="font-size:14px;'
        f'font-family:{_SANS};color:{"#c6d3da" if key == "all" else "#8fa0ab"};'
        f'background:{"#151a1e" if key == "all" else "transparent"};'
        f'border:1px solid {"#2c3942" if key == "all" else "#1c2429"};'
        'border-radius:4px;padding:8px 14px;cursor:pointer">'
        f"{label}</button>"
        for key, label in (("all", "All races"), ("photo", "With photos"),
                           ("flag", "Flagged")))

    parts = [
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>HallOfFrame results — {_esc(generated)}</title>",
        "</head>",
        f'<body style="margin:0;background:{_C["bg"]};font-family:{_SANS};'
        f'color:{_C["text"]};-webkit-font-smoothing:antialiased">',
        '<div style="max-width:1120px;margin:0 auto">',
        # --- header ---
        f'<header style="padding:40px 48px 32px;'
        f'border-bottom:1px solid {_C["panel_border"]};background:{_C["panel"]}">'
        '<div style="display:flex;align-items:flex-start;'
        'justify-content:space-between;gap:40px;flex-wrap:wrap">'
        '<div style="display:flex;flex-direction:column;gap:5px">'
        f'<div style="font-family:{_MONO};font-size:26px;font-weight:600;'
        f'letter-spacing:-.01em">HallOf<span style="color:{_C["red"]}">'
        "Frame</span></div>"
        f'<div style="font-size:15px;color:{_C["dim"]}">Finish-line results · '
        "full database</div></div>"
        '<div style="display:flex;flex-direction:column;gap:6px;text-align:right">'
        f'<div style="font-family:{_MONO};font-size:14px;'
        f'color:{_C["text2"]}">{_esc(generated)}</div>'
        f'<div style="font-size:13px;color:{_C["faint"]}">{n_races} race'
        f'{"s" if n_races != 1 else ""} · {n_caps} crossing'
        f'{"s" if n_caps != 1 else ""}</div></div></div>'
        '<div style="display:flex;align-items:center;gap:16px;margin-top:30px;'
        'flex-wrap:wrap">'
        '<input id="q" type="search" autocomplete="off"'
        ' placeholder="Filter races, bow numbers, categories…"'
        f' style="flex:1;min-width:260px;background:{_C["input_bg"]};'
        f'border:1px solid {_C["panel_border"]};border-radius:3px;'
        f'padding:11px 14px;font-family:{_MONO};font-size:16px;'
        f'color:{_C["text"]}">'
        f'<div style="display:flex;gap:8px">{chips}</div></div></header>',
        '<main style="padding:6px 48px 20px">',
    ]
    parts += [_race_html(race, caps) for race, caps in blocks]
    parts += [
        "</main>",
        f'<footer style="padding:22px 48px 30px;'
        f'border-top:1px solid {_C["panel_border"]};background:{_C["panel"]};'
        'display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;'
        f'font-size:13px;color:{_C["faint"]}">'
        "<span>Photos are linked relatively — keep this file next to the "
        f'<span style="font-family:{_MONO}">races/</span> folder. '
        "Click any photo for full size.</span>"
        "<span>Soft-deleted crossings excluded</span></footer>",
        "</div>",
        f"<script>{_FILTER_JS}</script>",
        "</body>\n</html>\n",
    ]
    out_path.write_text("".join(parts), encoding="utf-8")
    return out_path


def clipboard_data(storage: Storage, race_id: int) -> tuple[str, str]:
    """Return (tab_separated, html_table) for pasting into Excel with formatting.

    Layout (one field per cell):
      Race ID, race_no
      Heat no, heat_no
      Category, name
      Gun start, HH:MM:SS (local wall-clock time of the gun)
      Elapsed Time, Bow number, notes
      <one row per crossing, fastest to slowest>
    """
    race = storage.get_race(race_id)
    race_no = (race["race_no"] or "") if race else ""
    heat_no = (race["heat_no"] or "") if race else ""
    name = (race["name"] or "") if race else ""
    t0_wall = (race["t0_wall"] if race and race["t0_wall"] is not None else None)

    header = ["Elapsed Time", "Bow number", "notes"]
    rows: list[list[str]] = [
        ["Race ID", race_no],
        ["Heat no", heat_no],
        ["Category", name],
        ["Gun start", local_hms(t0_wall) if t0_wall is not None else ""],
        header,
    ]
    captures = storage.captures_for_race(race_id, include_deleted=False)
    captures = sorted(captures, key=lambda c: c["elapsed_s"])
    for c in captures:
        rows.append([
            format_elapsed(c["elapsed_s"]),
            c["bow_number"] or "",
            c["notes"] or "",
        ])

    tsv = "\r\n".join("\t".join(str(cell) for cell in row) for row in rows) + "\r\n"

    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    markup = (
        '<html xmlns:x="urn:schemas-microsoft-com:office:excel">'
        "<head><meta charset='utf-8'></head>"
        f"<body><table>{body}</table></body></html>"
    )
    return tsv, markup
