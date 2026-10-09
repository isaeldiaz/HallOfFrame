"""Whole-database HTML export (spec §6.8, requirement F6).

``export_all_html`` writes the whole database as one self-contained HTML page —
a card per crossing showing the captured frame itself. Images are linked by the
relative path already stored in ``primary_image``, so the file must stay next to
the ``races/`` folder inside the data root (the same place the CSV is written).

This module also owns the one shared page skeleton (:func:`page`) and the single
``CSS`` block used by the offline export and the live web server, so the two can
never drift apart (plan step 6.2).
"""
from __future__ import annotations

import datetime
import html
import urllib.parse
from pathlib import Path
from string import Template

from .. import __version__
from ..buildinfo import build_stamp
from ..storage import Storage
from . import flag_word, format_elapsed, local_hms, row_value, utc_iso


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

# Version + commit/date, read once at import. About the code serving this page.
_APP_VERSION = __version__
_BUILD_STAMP = build_stamp()

_REPOSITORY = "https://github.com/isaeldiaz/HallOfFrame"


# Every repeated inline ``style="..."`` moved here as a named class (step 6.2).
# ``$name`` placeholders are filled from ``_C`` / the font strings by :func:`_css`.
_CSS_TEMPLATE = """
* { box-sizing: border-box; }
body {
  margin: 0;
  background: $bg;
  font-family: $sans;
  color: $text;
  -webkit-font-smoothing: antialiased;
}
.wrap { margin: 0 auto; }
.mono { font-family: $mono; }
.c-text { color: $text; }
.c-text2 { color: $text2; }
.c-dim { color: $dim; }
.c-faint { color: $faint; }
.c-blue { color: $blue; }
.c-amber { color: $amber; }
.thumb {
  position: relative; width: 268px; height: 168px; flex: none;
  background: $letterbox; border-radius: 2px; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
.thumb-empty { flex-direction: column; gap: 10px; }
.thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
.finish-line {
  position: absolute; top: 0; bottom: 0; left: 50%; width: 2px;
  background: $finish;
}
.badge {
  position: absolute; top: 8px; left: 8px;
  font-family: $mono; font-size: 11px; font-weight: 600; letter-spacing: .08em;
  color: $amber; background: rgba(30,22,8,.92); border: 1px solid #4a3b12;
  border-radius: 2px; padding: 3px 7px;
}
.noimg-title {
  font-family: $mono; font-size: 12px; font-weight: 600; letter-spacing: .1em;
  color: $amber;
}
.noimg-sub { font-size: 12px; color: $faint; }
.meta-cell { display: flex; flex-direction: column; gap: 5px; }
.meta-label { font-size: 12px; letter-spacing: .1em; color: $faint; }
.meta-value { font-family: $mono; font-size: 15px; color: $text2; }
.card {
  display: flex; gap: 26px; align-items: stretch;
  background: $panel; border: 1px solid $panel_border;
  border-radius: 4px; padding: 16px;
}
.card-body {
  flex: 1; display: flex; flex-direction: column;
  justify-content: space-between; gap: 16px; padding: 4px 0;
}
.card-top {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 24px;
}
.card-left { display: flex; align-items: baseline; gap: 20px; }
.elapsed {
  font-family: $mono; font-size: 44px; font-weight: 500;
  color: $text; letter-spacing: -.02em;
}
.bow-group { display: flex; align-items: baseline; gap: 9px; }
.bow-label { font-size: 13px; letter-spacing: .1em; color: $faint; }
.bow { font-family: $mono; font-size: 30px; font-weight: 600; }
.seq {
  font-family: $mono; font-size: 13px; color: $faint; letter-spacing: .06em;
}
.card-meta { display: flex; flex-direction: column; gap: 14px; }
.meta-row { display: flex; gap: 44px; flex-wrap: wrap; }
.note-line {
  display: flex; align-items: center; gap: 10px;
  font-size: 14px; color: $amber;
}
.note-text { color: $dim; }
.flag-chip {
  font-family: $mono; font-size: 12px; font-weight: 600; letter-spacing: .08em;
  color: $amber; border: 1px solid #4a3b12; border-radius: 2px; padding: 2px 7px;
}
.race-head {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 32px; padding: 34px 0 18px;
  border-bottom: 1px solid $divider; flex-wrap: wrap;
}
.race-head-left { display: flex; flex-direction: column; gap: 8px; }
.race-label-row { display: flex; align-items: baseline; gap: 14px; }
.race-label {
  font-family: $mono; font-size: 15px; font-weight: 600;
  letter-spacing: .1em; color: $blue;
}
.race-id { font-family: $mono; font-size: 12px; color: $faint; }
.race-name {
  font-size: 27px; font-weight: 500; color: $text; letter-spacing: -.01em;
}
.race-head-right {
  display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
}
.race-grid {
  display: grid; grid-template-columns: auto auto auto; gap: 6px 28px;
  text-align: right;
}
.race-meta-label { font-size: 12px; letter-spacing: .1em; color: $faint; }
.race-meta-value { font-family: $mono; font-size: 17px; }
.cards { display: flex; flex-direction: column; gap: 14px; padding: 24px 0; }
.warn { margin-top: 14px; font-size: 14px; color: $amber; }
.empty { padding: 22px 0 10px; font-size: 15px; color: $faint; }
.excel-btn {
  display: inline-block; margin-top: 10px;
  font-family: $mono; font-size: 13px; font-weight: 600; color: $blue;
  border: 1px solid #2c3942; border-radius: 3px; padding: 8px 12px;
  cursor: pointer; background: transparent;
}
.head-row {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 40px; flex-wrap: wrap;
}
.brand-col { display: flex; flex-direction: column; gap: 5px; }
.brand {
  font-family: $mono; font-size: 26px; font-weight: 600; letter-spacing: -.01em;
}
.brand-red { color: $red; }
.tagline { font-size: 15px; color: $dim; }
.head-right {
  display: flex; flex-direction: column; gap: 6px; text-align: right;
}
.generated { font-family: $mono; font-size: 14px; color: $text2; }
.counts { font-size: 13px; color: $faint; }
.filter-row {
  display: flex; align-items: center; gap: 16px; margin-top: 30px;
  flex-wrap: wrap;
}
.filter-row input {
  flex: 1; min-width: 260px; background: $input_bg;
  border: 1px solid $panel_border; border-radius: 3px; padding: 11px 14px;
  font-family: $mono; font-size: 16px; color: $text;
}
.chips { display: flex; gap: 8px; }
.chip {
  font-size: 14px; font-family: $sans; color: $faint; background: transparent;
  border: 1px solid $panel_border; border-radius: 4px; padding: 8px 14px;
  cursor: pointer;
}
.chip.on { color: $text2; background: #151a1e; border-color: #2c3942; }
.export-head {
  padding: 40px 48px 32px; border-bottom: 1px solid $panel_border;
  background: $panel;
}
.main-export { padding: 6px 48px 20px; }
.export-footer {
  padding: 22px 48px 30px; border-top: 1px solid $panel_border;
  background: $panel; display: flex; justify-content: space-between;
  gap: 24px; flex-wrap: wrap; font-size: 13px; color: $faint;
}
.index-head {
  padding: 34px 40px 26px; border-bottom: 1px solid $panel_border;
  background: $panel;
}
.index-head-row {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 24px; flex-wrap: wrap;
}
.index-brand-col { display: flex; flex-direction: column; gap: 4px; }
.event {
  font-family: $mono; font-size: 14px; font-weight: 600;
  letter-spacing: .08em; color: $blue;
}
.brand-lg { font-family: $mono; font-size: 24px; font-weight: 600; }
.index-sub { font-size: 14px; color: $dim; }
.index-updated { font-size: 13px; color: $faint; }
.index-note { font-size: 13px; color: $faint; margin-top: 14px; }
.main-index { padding: 10px 40px 24px; }
.index-table { width: 100%; border-collapse: collapse; }
.index-thead {
  text-align: left; font-family: $mono; font-size: 12px;
  letter-spacing: .1em; color: $faint;
}
.th { padding: 14px 8px 6px; }
.th-right { padding: 14px 8px 6px; text-align: right; }
.row { border-bottom: 1px solid $divider; }
.td-label { padding: 14px 8px; font-family: $mono; font-size: 14px; color: $blue; }
.td-cat { padding: 14px 8px; font-size: 15px; color: $text; }
.td-gun { padding: 14px 8px; font-family: $mono; font-size: 14px; color: $text2; }
.td-n {
  padding: 14px 8px; text-align: right; font-family: $mono;
  font-size: 14px; color: $dim;
}
.td-actions { padding: 14px 8px; text-align: right; white-space: nowrap; }
.row-updated { font-size: 12px; color: $faint; margin-top: 3px; }
.link { color: $blue; text-decoration: none; }
.link-view { font-size: 14px; margin-right: 14px; }
.excel-inline {
  font-family: $mono; font-size: 14px; font-weight: 600; color: $blue;
  background: transparent; border: none; padding: 0; cursor: pointer;
}
.empty-row { padding: 40px; text-align: center; color: $faint; font-size: 15px; }
.index-footer {
  padding: 18px 40px 26px; border-top: 1px solid $panel_border;
  background: $panel; font-size: 12px; color: $faint;
}
.racepage-head {
  padding: 16px 48px; border-bottom: 1px solid $panel_border;
  background: $panel; display: flex; align-items: center;
  justify-content: space-between; gap: 24px; flex-wrap: wrap;
}
.back-link { color: $dim; text-decoration: none; font-size: 14px; }
.main-race { padding: 0 48px 20px; }
.race-footer {
  padding: 18px 48px 26px; border-top: 1px solid $panel_border;
  background: $panel; font-size: 12px; color: $faint;
}
.about-link { color: $blue; text-decoration: none; }
"""


def _css() -> str:
    return Template(_CSS_TEMPLATE).substitute(
        bg=_C["bg"], panel=_C["panel"], panel_border=_C["panel_border"],
        divider=_C["divider"], text=_C["text"], text2=_C["text2"],
        dim=_C["dim"], faint=_C["faint"], letterbox=_C["letterbox"],
        finish=_C["finish"], amber=_C["amber"], blue=_C["blue"], red=_C["red"],
        input_bg=_C["input_bg"], sans=_SANS, mono=_MONO)


CSS = _css()

# Race-page-only CSS (spec §13.3). Kept out of the shared block above so the
# index and the offline export stay byte-identical (the goldens enforce it);
# only build_race_page() injects it.
_PHOTO_CSS = """
.thumb-deferred { cursor: pointer; }
.thumb-deferred.photos-on .noimg-title,
.thumb-deferred.photos-on .noimg-sub { display: none; }
#viewer {
  display: none; position: fixed; inset: 0; z-index: 50;
  background: rgba(0,0,0,.92); align-items: center; justify-content: center;
}
#viewer img { max-width: 92vw; max-height: 80vh; object-fit: contain; }
.viewer-btn {
  font-family: 'IBM Plex Mono','SFMono-Regular',Consolas,monospace;
  font-size: 20px; color: #f2f6f8;
  background: rgba(20,26,30,.8); border: 1px solid #2c3942;
  border-radius: 4px; padding: 10px 16px; cursor: pointer;
}
#viewer-prev, #viewer-next { position: absolute; top: 50%; transform: translateY(-50%); }
#viewer-prev { left: 16px; }
#viewer-next { right: 16px; }
#viewer-close { position: absolute; top: 16px; right: 16px; }
.viewer-bar {
  position: absolute; bottom: 18px; left: 0; right: 0;
  display: flex; gap: 18px; align-items: center; justify-content: center;
  flex-wrap: wrap; color: #c6d3da; font-size: 14px;
}
.viewer-caption { font-family: 'IBM Plex Mono','SFMono-Regular',Consolas,monospace; color: #f2f6f8; }
.viewer-pos { color: #5f6f79; }
#viewer-full { font-size: 14px; }
"""

_FILTER_JS = """
(function () {
  var box = document.getElementById('q');
  if (!box) { return; }
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

# Wires every [data-excel] button to copy that race to the clipboard as the
# TSV + HTML-table pair (the same payload the review window copies), so pasting
# into Excel keeps the column layout. Prefers the async Clipboard API (secure
# contexts, e.g. localhost) which can write both formats; falls back to copying
# the TSV via execCommand on plain HTTP where ClipboardItem is unavailable.
_COPY_JS = r"""
(function () {
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
  }
  function flash(btn) {
    var old = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(function () { btn.textContent = old; }, 1500);
  }
  var btns = document.querySelectorAll('[data-excel]');
  for (var i = 0; i < btns.length; i++) {
    btns[i].addEventListener('click', function () {
      var id = this.getAttribute('data-excel');
      var btn = this;
      fetch('/excel/' + id)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (navigator.clipboard && window.ClipboardItem) {
            navigator.clipboard.write([new ClipboardItem({
              'text/html': new Blob([data.html], { type: 'text/html' }),
              'text/plain': new Blob([data.tsv], { type: 'text/plain' })
            })]).then(function () { flash(btn); },
                     function () { fallbackCopy(data.tsv); flash(btn); });
          } else {
            fallbackCopy(data.tsv);
            flash(btn);
          }
        })
        .catch(function () { btn.textContent = 'Copy failed'; });
    });
  }
})();
"""


def _viewer_html() -> str:
    """The overlay viewer shell for the live race page (spec §13.3).

    Hidden by default; ``_PHOTO_JS`` fills it in and toggles it. Only the
    thumbnail is loaded until the viewer presses Full size, which swaps the
    image for the /img/ original."""
    return (
        '<div id="viewer">'
        '<button type="button" id="viewer-close" class="viewer-btn" '
        'aria-label="Close">&times;</button>'
        '<button type="button" id="viewer-prev" class="viewer-btn" '
        'aria-label="Previous">&lsaquo;</button>'
        '<img id="viewer-img" src="" alt="">'
        '<button type="button" id="viewer-next" class="viewer-btn" '
        'aria-label="Next">&rsaquo;</button>'
        '<div class="viewer-bar">'
        '<span id="viewer-caption" class="viewer-caption"></span>'
        '<span id="viewer-pos" class="viewer-pos"></span>'
        '<button type="button" id="viewer-full" class="viewer-btn">'
        'Full size</button>'
        '</div></div>'
    )


# Deferred-photo viewer for the live race page (spec §13.3). Plain ES5, no
# external resources: the cards carry data-thumb / data-full / data-caption and
# nothing is fetched until the operator asks. With JS disabled the placeholders
# simply remain (the race-page footer says so).
_PHOTO_JS = r"""
(function () {
  var viewer = document.getElementById('viewer');
  var btn = document.getElementById('show-photos');
  function thumbs() {
    return document.querySelectorAll('.thumb-deferred');
  }
  function photosOn() {
    return !!(btn && btn.textContent === 'Hide photos');
  }
  function showPhotos() {
    var list = thumbs();
    for (var i = 0; i < list.length; i++) {
      var t = list[i];
      if (!t.getAttribute('data-loaded')) {
        var img = document.createElement('img');
        img.src = t.getAttribute('data-thumb');
        img.setAttribute('loading', 'lazy');
        img.alt = '';
        t.insertBefore(img, t.firstChild);
        t.setAttribute('data-loaded', '1');
      }
      t.classList.add('photos-on');
    }
    try { localStorage.setItem('hof_photos', '1'); } catch (e) {}
    if (btn) { btn.textContent = 'Hide photos'; }
  }
  function hidePhotos() {
    var list = thumbs();
    for (var i = 0; i < list.length; i++) {
      var t = list[i];
      var img = t.querySelector('img');
      if (img) { t.removeChild(img); }
      t.removeAttribute('data-loaded');
      t.classList.remove('photos-on');
    }
    try { localStorage.removeItem('hof_photos'); } catch (e) {}
    if (btn) { btn.textContent = 'Show photos'; }
  }
  if (btn) {
    btn.addEventListener('click', function () {
      if (photosOn()) { hidePhotos(); } else { showPhotos(); }
    });
    try {
      if (localStorage.getItem('hof_photos') === '1') { showPhotos(); }
    } catch (e) {}
  }

  var current = -1;
  function openAt(index) {
    var list = thumbs();
    if (!list.length) { return; }
    current = (index + list.length) % list.length;
    var t = list[current];
    var img = document.getElementById('viewer-img');
    if (img) { img.src = t.getAttribute('data-thumb'); }
    var cap = document.getElementById('viewer-caption');
    if (cap) { cap.textContent = t.getAttribute('data-caption') || ''; }
    var pos = document.getElementById('viewer-pos');
    if (pos) { pos.textContent = (current + 1) + ' / ' + list.length; }
    if (viewer) { viewer.style.display = 'flex'; }
    preload(current + 1);
  }
  function closeViewer() {
    if (viewer) { viewer.style.display = 'none'; }
    current = -1;
  }
  function preload(index) {
    var list = thumbs();
    if (!list.length) { return; }
    var url = list[(index + list.length) % list.length]
      .getAttribute('data-thumb');
    if (url) { var p = new Image(); p.src = url; }
  }
  document.addEventListener('click', function (e) {
    var node = e.target;
    while (node && node !== document.body) {
      if (node.classList && node.classList.contains('thumb-deferred')) {
        var list = thumbs();
        for (var i = 0; i < list.length; i++) {
          if (list[i] === node) { openAt(i); return; }
        }
      }
      node = node.parentNode;
    }
  });
  var closeBtn = document.getElementById('viewer-close');
  if (closeBtn) { closeBtn.addEventListener('click', closeViewer); }
  var prevBtn = document.getElementById('viewer-prev');
  if (prevBtn) {
    prevBtn.addEventListener('click', function () { openAt(current - 1); });
  }
  var nextBtn = document.getElementById('viewer-next');
  if (nextBtn) {
    nextBtn.addEventListener('click', function () { openAt(current + 1); });
  }
  var fullBtn = document.getElementById('viewer-full');
  if (fullBtn) {
    fullBtn.addEventListener('click', function () {
      var list = thumbs();
      if (current < 0 || !list.length) { return; }
      var img = document.getElementById('viewer-img');
      if (img) { img.src = list[current].getAttribute('data-full'); }
    });
  }
  document.addEventListener('keydown', function (e) {
    if (!viewer || viewer.style.display !== 'flex') { return; }
    if (e.key === 'Escape') { closeViewer(); }
    else if (e.key === 'ArrowLeft') { openAt(current - 1); }
    else if (e.key === 'ArrowRight') { openAt(current + 1); }
  });
  var touchX = null;
  if (viewer) {
    viewer.addEventListener('touchstart', function (e) {
      if (e.touches.length === 1) { touchX = e.touches[0].clientX; }
    });
    viewer.addEventListener('touchend', function (e) {
      if (touchX === null || !e.changedTouches.length) { return; }
      var dx = e.changedTouches[0].clientX - touchX;
      touchX = null;
      if (dx > 40) { openAt(current - 1); }
      else if (dx < -40) { openAt(current + 1); }
    });
  }
})();
"""


def page(title: str, body: str, *, width_px: int, footer: str,
         scripts: str = "", style: str = "") -> str:
    """One shared HTML skeleton for the export and the web server (step 6.2).

    *body* is the page content (header + main); *footer* is the full
    ``<footer>`` element (each page keeps its own padding/classes). Holds the
    doctype, ``<head>``, the single ``CSS`` block and both ``<script>`` tags.
    *scripts* is appended after the shared copy/filter scripts and *style* adds
    a second ``<style>`` block; both default to empty so the index and the
    offline export stay byte-identical (the live race page uses them for the
    deferred-photo viewer, spec §13.3)."""
    style_tag = f"<style>{style}</style>" if style else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f"<style>{CSS}</style>{style_tag}"
        "</head>"
        f'<body><div class="wrap" style="max-width:{int(width_px)}px">'
        f"{body}{footer}"
        "</div>"
        f"<script>{_COPY_JS}{_FILTER_JS}{scripts}</script>"
        "</body>\n</html>\n"
    )


def _about_footer() -> str:
    """Small About line for page footers: version + the commit/date that built
    the code serving the page."""
    return (
        f'<span class="mono">HallOfFrame v{_esc(_APP_VERSION)}'
        f" · {_esc(_BUILD_STAMP)}</span>"
        f' · <a href="{_esc(_REPOSITORY)}" class="about-link">'
        "github.com/isaeldiaz/HallOfFrame</a>"
    )


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _updated_hms(iso: str | None) -> str:
    """Local ``HH:MM:SS`` for an ISO-8601 ``updated_at`` (step 6.3)."""
    if not iso:
        return "—"
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return local_hms(dt.timestamp())


def _src(rel_path: str, img_base: str = "") -> str:
    """URL-quote a stored relative image path (race folders contain spaces).

    *img_base* prefixes the URL (e.g. ``/img/`` for the web server) so the same
    cards can be served from the web without the stored relative path leaking
    the on-disk layout. Empty for the offline export (paths stay relative).
    """
    return img_base + html.escape(
        urllib.parse.quote(rel_path.replace("\\", "/")), quote=True)


# Kept as a module-level alias: ``export.py`` (and its shim test) imports
# ``_row_value`` from here; the one implementation lives in ``render``.
_row_value = row_value


def _thumb_html(capture, position: int = 0, img_base: str = "",
                *, deferred_photos: bool = False) -> str:
    rel = capture["primary_image"] or ""
    word = flag_word(capture["image_flag"],
                     _row_value(capture, "debounce_suspect", 0))
    badge = f'<span class="badge">{_esc(word)}</span>' if word else ""
    if not rel:
        return ('<div class="thumb thumb-empty">'
                '<span class="noimg-title">NO IMAGE</span>'
                '<span class="noimg-sub">timing only</span></div>')
    if deferred_photos:
        # The live race page sends no images until the viewer asks (spec §13.3):
        # the card carries the /thumb/ and /img/ URLs for the JS viewer.
        thumb_src = _src(rel, "/thumb/")
        full_src = _src(rel, "/img/")
        elapsed = format_elapsed(capture["elapsed_s"])
        bow = capture["bow_number"] or ""
        caption = f"{elapsed} · BOW {bow}" if bow else elapsed
        return (
            f'<div class="thumb thumb-deferred" data-thumb="{thumb_src}" '
            f'data-full="{full_src}" data-pos="{int(position)}" '
            f'data-caption="{_esc(caption)}">'
            '<span class="noimg-title">PHOTO</span>'
            '<span class="noimg-sub">tap Show photos</span>'
            '<span class="finish-line"></span>'
            f"{badge}</div>")
    src = _src(rel, img_base)
    return (
        f'<a href="{src}" target="_blank" rel="noopener" class="thumb">'
        f'<img src="{src}" loading="lazy" alt="{_esc(rel)}">'
        '<span class="finish-line"></span>'
        f"{badge}</a>"
    )


def _meta_cell(label: str, value: str) -> str:
    return (f'<span class="meta-cell"><span class="meta-label">{_esc(label)}</span>'
            f'<span class="meta-value">{_esc(value)}</span></span>')


def _card_html(capture, position: int, img_base: str = "",
               *, deferred_photos: bool = False) -> str:
    word = flag_word(capture["image_flag"],
                     _row_value(capture, "debounce_suspect", 0))
    bow = capture["bow_number"] or ""
    notes = capture["notes"] or ""
    elapsed = format_elapsed(capture["elapsed_s"])
    if _row_value(capture, "t0_reconstructed", 0):
        elapsed = "~" + elapsed  # measured after a reconstructed resume (N4)
    elapsed_raw = "%.2f" % capture["elapsed_s"]
    source = _row_value(capture, "elapsed_source", "press") or "press"
    seq = "#%03d" % position
    search = " ".join(str(v) for v in (capture["sequence"], bow, elapsed, notes,
                                       word) if v)
    note_line = ""
    if word or notes:
        chip = f'<span class="flag-chip">{_esc(word)}</span>' if word else ""
        text = f'<span class="note-text">{_esc(notes)}</span>' if notes else ""
        note_line = f'<div class="note-line">{chip}{text}</div>'
    bow_html = (f'<span class="bow {"c-text" if bow else "c-faint"}">'
                f'{_esc(bow) if bow else "&mdash;"}</span>')
    return (
        f'<div class="card" data-search="{_esc(search)}" data-flag="{_esc(word)}"'
        f' data-photo="{"1" if capture["primary_image"] else "0"}">'
        f"{_thumb_html(capture, position, img_base, deferred_photos=deferred_photos)}"
        '<div class="card-body">'
        '<div class="card-top">'
        '<div class="card-left">'
        f'<span class="elapsed">{_esc(elapsed)}</span>'
        '<span class="bow-group">'
        f'<span class="bow-label">BOW</span>{bow_html}</span></div>'
        f'<span class="seq">{_esc(seq)}</span></div>'
        '<div class="card-meta">'
        '<div class="meta-row">'
        + _meta_cell("ELAPSED (S)", elapsed_raw)
        + _meta_cell("SRC", source)
        + _meta_cell("WALL CLOCK (UTC)", utc_iso(capture["t_press_wall"]))
        + "</div>"
        f"{note_line}</div></div></div>"
    )


def _race_html(race, captures, img_base: str = "", excel_id: int | None = None,
               *, deferred_photos: bool = False) -> str:
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
        warn = ('<div class="warn">Gun start was reconstructed after a '
                "restart — elapsed times for this race are approximate.</div>")
    search = " ".join(str(v) for v in (race["race_no"] or "", race["heat_no"] or "",
                                       race["name"] or "", label) if v)
    ordered = sorted(captures, key=lambda c: (c["elapsed_s"], c["sequence"]))
    body = ("".join(_card_html(c, pos, img_base, deferred_photos=deferred_photos)
                    for pos, c in enumerate(ordered, start=1))
            if ordered else
            '<div class="empty">No crossings recorded.</div>')
    metas = "".join(
        f'<span class="race-meta-label">{h}</span>'
        for h in ("GUN START", "MODE", "Δ USED")
    ) + "".join(
        f'<span class="race-meta-value {c}">{_esc(v)}</span>'
        for v, c in ((gun, "c-text"), (mode, "c-text2"), (delta, "c-text2"))
    )
    excel = ""
    if excel_id is not None:
        excel = (f'<button type="button" data-excel="{int(excel_id)}" '
                 'class="excel-btn">Copy as Excel</button>')
    updated = _updated_hms(_row_value(race, "updated_at"))
    return (
        f'<section data-race="{_esc(search)}">'
        '<div class="race-head">'
        '<div class="race-head-left">'
        '<div class="race-label-row">'
        f'<span class="race-label">{_esc(label)}</span>'
        f'<span class="race-id">race_id {race["id"]}</span></div>'
        f'<div class="race-name">{_esc(race["name"] or "")}</div>'
        f'<div class="counts">Updated {_esc(updated)}</div></div>'
        '<div class="race-head-right">'
        f'<div class="race-grid">{metas}</div>{excel}</div>{warn}</div>'
        f'<div class="cards">{body}</div></section>'
    )


def _all_race_blocks(storage: Storage):
    """Yield ``(race_row, captures)`` for the whole database.

    Races oldest first, crossings fastest-to-slowest within a race,
    soft-deleted crossings excluded. Every race is yielded — a race with no
    crossings yields an empty capture list rather than being skipped. Both
    ``export_all_csv`` and ``export_all_html`` consume this, so the two
    exporters can never diverge on ordering or filtering. Delegates to
    :meth:`Storage.all_bundles` (step 1.4e) so the web index shares the same
    query path.
    """
    yield from storage.all_bundles()


def build_all_html(storage: Storage, img_base: str = "") -> str:
    """Render the whole database as one HTML page — a card per crossing with the
    captured frame shown, grouped by race (oldest first) and fastest-to-slowest
    within a race.

    Images are referenced by the stored ``primary_image`` path. With
    ``img_base=""`` the paths stay relative (used by the offline exporter, so the
    file must sit next to the ``races/`` folder inside the data root). With an
    ``img_base`` such as ``/img/`` the same page is served by the web server,
    which resolves those paths to files on disk. Every race is listed, including
    races with no crossings. Soft-deleted crossings are excluded. No external CSS
    or JS: the page opens offline, and with JavaScript disabled everything except
    the filter box still works.
    """
    blocks = list(_all_race_blocks(storage))
    n_races = len(blocks)
    n_caps = sum(len(caps) for _, caps in blocks)
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    updated = _updated_hms(storage.last_updated())

    chips = "".join(
        f'<button type="button" data-preset="{key}" '
        f'class="chip{" on" if key == "all" else ""}">{label}</button>'
        for key, label in (("all", "All races"), ("photo", "With photos"),
                           ("flag", "Flagged")))

    header = (
        '<header class="export-head">'
        '<div class="head-row">'
        '<div class="brand-col">'
        '<div class="brand">HallOf<span class="brand-red">Frame</span></div>'
        '<div class="tagline">Finish-line results · full database</div></div>'
        '<div class="head-right">'
        f'<div class="generated">{_esc(generated)}</div>'
        f'<div class="counts">{n_races} race{"s" if n_races != 1 else ""} · '
        f'{n_caps} crossing{"s" if n_caps != 1 else ""}</div>'
        f'<div class="counts">Results updated {_esc(updated)}</div></div></div>'
        '<div class="filter-row">'
        '<input id="q" type="search" autocomplete="off"'
        ' placeholder="Filter races, bow numbers, categories…">'
        f'<div class="chips">{chips}</div></div></header>'
    )
    cards = "".join(_race_html(race, caps, img_base) for race, caps in blocks)
    footer = (
        '<footer class="export-footer">'
        "<span>Photos are linked relatively — keep this file next to the "
        '<span class="mono">races/</span> folder. '
        "Click any photo for full size.</span>"
        "<span>Soft-deleted crossings excluded</span></footer>"
    )
    return page(f"HallOfFrame results — {generated}", header
                + f'<main class="main-export">{cards}</main>',
                width_px=1120, footer=footer)


def export_all_html(storage: Storage, out_path: str | Path) -> Path:
    """Write the entire database as one HTML page (see :func:`build_all_html`).
    *out_path* must sit in the data root, beside the ``races/`` folder."""
    out_path = Path(out_path)
    out_path.write_text(build_all_html(storage), encoding="utf-8")
    return out_path
