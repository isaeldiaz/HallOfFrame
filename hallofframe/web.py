"""Separate-process HTTP server for live results (spec §6.8, F6).

Runs OUT OF PROCESS from the timing app on purpose. It opens its own SQLite
read connection (safe under WAL against the app's single writer) and serves
only pre-rendered HTML plus files already on disk, so even a crowd of viewers
cannot perturb the evdev-triggered timing thread. Launch it explicitly::

    python -m hallofframe.web --config /path/to/config.toml

Routes:
  GET /                  race index (compact list)
  GET /race/<id>         one race, a card per crossing with its captured frame
  GET /excel/<id>        JSON payload for the "Copy as Excel" buttons: the race
                         as {tsv, html} — the same tab-separated + HTML-table
                         pair the review window copies to clipboard, so pasting
                         into Excel keeps the column layout
  GET /excel/<id>.xls    same race as a downloadable Excel-compatible .xls
                         (HTML table; fallback for browsers/users that prefer a
                         file over the clipboard copy)
  GET /img/<relpath>     a captured frame by its stored relative path

The app's ``Storage`` uses a single locked connection; this server NEVER touches
it. It builds its own ``Storage`` (a second, read-only connection) so reads never
block the app's writer. ``PRAGMA busy_timeout`` backs the rare WAL checkpoint
contention between many readers and the one writer.
"""
from __future__ import annotations

import argparse
import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from . import __version__
from .buildinfo import build_stamp
from .config import load_config
from .export import (_C, _MONO, _SANS, _esc, clipboard_data, local_hms,
                     _race_html)
from .storage import Storage

# Version + commit/date, read once at import. About the code serving this page.
_APP_VERSION = __version__
_BUILD_STAMP = build_stamp()

_IMG_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}

_REPOSITORY = "https://github.com/isaeldiaz/HallOfFrame"

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


def _excel_filename(storage: Storage, race_id: int) -> str:
    race = storage.get_race(race_id)
    if race is None:
        return f"race-{race_id}.xls"
    parts = [race["race_no"] or "", race["heat_no"] or "", race["name"] or ""]
    base = "-".join(p for p in parts if p) or f"race-{race_id}"
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in base).strip()
    return f"{safe or f'race-{race_id}'}.xls"


def resolve_image_file(data_root: Path, rel: str) -> Path | None:
    """Resolve a stored relative image path to a file inside *data_root*, or
    None if it escapes the root or does not exist. Guarded against ``../``
    traversal so a crafted URL cannot read arbitrary files."""
    target = (Path(data_root) / rel).resolve()
    root = Path(data_root).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def _about_footer() -> str:
    """Small About line for page footers: version + the commit/date that built
    the code serving the page."""
    return (
        f'<span style="font-family:{_MONO}">HallOfFrame v{_esc(_APP_VERSION)}'
        f" · {_esc(_BUILD_STAMP)}</span>"
        f' · <a href="{_esc(_REPOSITORY)}" style="color:{_C["blue"]};'
        'text-decoration:none">github.com/isaeldiaz/HallOfFrame</a>'
    )


def build_index(storage: Storage) -> str:
    """Compact race list: newest first, one row per race with links to the race
    page and its Excel copy. Only reviewed races are published (spec §6.8)."""
    races = storage.list_races(reviewed_only=True)  # id DESC (newest first)
    rows = []
    for r in races:
        race = storage.get_race(r["id"])
        captures = storage.captures_for_race(r["id"], include_deleted=False)
        n = len(captures)
        t0 = race["t0_wall"] if race["t0_wall"] is not None else None
        gun = local_hms(t0) if t0 is not None else "—"
        label = " · ".join(p for p in (
            f"RACE {race['race_no']}" if race["race_no"] else "UNLISTED",
            f"HEAT {race['heat_no']}" if race["heat_no"] else "") if p) or "UNLISTED"
        rows.append(
            f'<tr style="border-bottom:1px solid {_C["divider"]}">'
            f'<td style="padding:14px 8px;font-family:{_MONO};font-size:14px;'
            f'color:{_C["blue"]}">{_esc(label)}</td>'
            f'<td style="padding:14px 8px;font-size:15px;color:{_C["text"]}">'
            f'{_esc(race["name"] or "")}</td>'
            f'<td style="padding:14px 8px;font-family:{_MONO};font-size:14px;'
            f'color:{_C["text2"]}">{_esc(gun)}</td>'
            f'<td style="padding:14px 8px;text-align:right;font-family:{_MONO};'
            f'font-size:14px;color:{_C["dim"]}">{n}</td>'
            f'<td style="padding:14px 8px;text-align:right;white-space:nowrap">'
            f'<a href="/race/{r["id"]}" style="color:{_C["blue"]};'
            'text-decoration:none;font-size:14px;margin-right:14px">View</a>'
            f'<button type="button" data-excel="{r["id"]}"'
            f' style="font-family:{_MONO};font-size:14px;font-weight:600;'
            f'color:{_C["blue"]};background:transparent;border:none;'
            'padding:0;cursor:pointer">Copy table</button></td></tr>')

    body_rows = "".join(rows) or (
        f'<tr><td colspan="5" style="padding:40px;text-align:center;'
        f'color:{_C["faint"]};font-size:15px">No races recorded yet.</td></tr>')

    event = storage.event_name or ""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(event)} — HallOfFrame results</title></head>"
        f'<body style="margin:0;background:{_C["bg"]};font-family:{_SANS};'
        f'color:{_C["text"]};-webkit-font-smoothing:antialiased">'
        '<div style="max-width:960px;margin:0 auto">'
        f'<header style="padding:34px 40px 26px;border-bottom:1px solid '
        f'{_C["panel_border"]};background:{_C["panel"]}">'
        '<div style="display:flex;align-items:flex-end;justify-content:'
        'space-between;gap:24px;flex-wrap:wrap">'
        '<div style="display:flex;flex-direction:column;gap:4px">'
        f'<div style="font-family:{_MONO};font-size:14px;font-weight:600;'
        f'letter-spacing:.08em;color:{_C["blue"]}">{_esc(event)}</div>'
        f'<div style="font-family:{_MONO};font-size:24px;font-weight:600">'
        f'HallOf<span style="color:{_C["red"]}">Frame</span></div>'
        f'<div style="font-size:14px;color:{_C["dim"]}">Finish-line results'
        " · races</div></div>"
        f'<div style="font-size:13px;color:{_C["faint"]}">'
        f'{len(races)} race{"s" if len(races) != 1 else ""}</div></div></header>'
        '<main style="padding:10px 40px 24px">'
        '<table style="width:100%;border-collapse:collapse">'
        '<thead><tr style="text-align:left;font-family:'
        f'{_MONO};font-size:12px;letter-spacing:.1em;color:{_C["faint"]}">'
        "<th style=\"padding:14px 8px 6px\">RACE</th>"
        '<th style="padding:14px 8px 6px">CATEGORY</th>'
        '<th style="padding:14px 8px 6px">GUN</th>'
        '<th style="padding:14px 8px 6px;text-align:right">#</th>'
        '<th style="padding:14px 8px 6px;text-align:right"></th></tr></thead>'
        f"<tbody>{body_rows}</tbody></table></main>"
        f'<footer style="padding:18px 40px 26px;border-top:1px solid '
        f'{_C["panel_border"]};background:{_C["panel"]};font-size:12px;'
        f'color:{_C["faint"]}">Copy table copies a race to the clipboard — paste '
        "it into a spreadsheet to keep the column layout."
        f" · {_about_footer()}</footer>"
        "</div>"
        f"<script>{_COPY_JS}</script>"
        "</body>\n</html>\n"
    )


def build_race_page(storage: Storage, race_id: int) -> str | None:
    """One race as a full page (cards with frames + Copy as Excel). None if the
    race id is unknown."""
    race = storage.get_race(race_id)
    if race is None:
        return None
    captures = storage.captures_for_race(race_id, include_deleted=False)
    captures = sorted(captures, key=lambda c: c["elapsed_s"])
    body = _race_html(race, captures, img_base="/img/", excel_id=race_id)
    event = storage.event_name or ""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(event)} — Race {_esc(race['race_no'] or race['name'] or race_id)}"
        " — HallOfFrame</title></head>"
        f'<body style="margin:0;background:{_C["bg"]};font-family:{_SANS};'
        f'color:{_C["text"]};-webkit-font-smoothing:antialiased">'
        '<div style="max-width:1120px;margin:0 auto">'
        f'<header style="padding:16px 48px;border-bottom:1px solid '
        f'{_C["panel_border"]};background:{_C["panel"]};display:flex;'
        'align-items:center;justify-content:space-between;gap:24px;'
        'flex-wrap:wrap">'
        '<a href="/" style="color:#8fa0ab;text-decoration:none;font-size:14px">'
        "&larr; All races</a>"
        f'<span style="font-family:{_MONO};font-size:13px;font-weight:600;'
        f'letter-spacing:.08em;color:{_C["blue"]}">{_esc(event)}</span>'
        "</header>"
        '<main style="padding:0 48px 20px">'
        f"{body}</main>"
        f'<footer style="padding:18px 48px 26px;border-top:1px solid '
        f'{_C["panel_border"]};background:{_C["panel"]};font-size:12px;'
        f'color:{_C["faint"]}">{_about_footer()}</footer>'
        "</div>"
        f"<script>{_COPY_JS}</script>"
        "</body>\n</html>\n"
    )


class WebServer(ThreadingHTTPServer):
    """HTTP server carrying shared state (its own read-only Storage)."""

    def __init__(self, server_address, storage: Storage, data_root: Path):
        super().__init__(server_address, WebHandler)
        self.storage = storage
        self.data_root = data_root


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer

    # --- helpers ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _html(self, code: int, body: str) -> None:
        self._send(code, body.encode("utf-8"), "text/html; charset=utf-8")

    # --- routes ----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        storage = self.server.storage

        if path == "/" or path == "/index.html":
            self._html(200, build_index(storage))
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if path.startswith("/race/"):
            self._race(storage, path)
            return
        if path.startswith("/excel/"):
            self._excel(storage, path)
            return
        if path.startswith("/img/"):
            self._img(path)
            return
        self._html(404, "<h1>404</h1><p>Not found.</p>")

    def _race(self, storage: Storage, path: str) -> None:
        rest = path[len("/race/"):]
        try:
            race_id = int(rest)
        except ValueError:
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        page = build_race_page(storage, race_id)
        if page is None:
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        self._html(200, page)

    def _excel(self, storage: Storage, path: str) -> None:
        rest = path[len("/excel/"):]
        download = rest.endswith(".xls")
        if download:
            rest = rest[:-4]
        try:
            race_id = int(rest)
        except ValueError:
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        if storage.get_race(race_id) is None:
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        tsv, markup = clipboard_data(storage, race_id)
        if download:
            self._send(200, markup.encode("utf-8"),
                       "application/vnd.ms-excel; charset=utf-8",
                       {"Content-Disposition":
                        f"attachment; filename=\"{_excel_filename(storage, race_id)}\""})
        else:
            payload = json.dumps({"tsv": tsv, "html": markup}).encode("utf-8")
            self._send(200, payload, "application/json; charset=utf-8")

    def _img(self, path: str) -> None:
        rel = unquote(path[len("/img/"):])
        ctype = _IMG_TYPES.get(Path(rel).suffix.lower(), "application/octet-stream")
        target = resolve_image_file(self.server.data_root, rel)
        if target is None:
            self._send(404, b"", ctype)
            return
        self._send(200, target.read_bytes(), ctype)

    def log_message(self, fmt, *args):  # keep console quiet during a race
        return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hallofframe.web")
    parser.add_argument("--config", default=None,
                        help="path to config.toml (default: ~/regatta-data)")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    web = config.section("web")
    if not bool(web.get("enabled", True)):
        print("web server disabled in config.toml ([web] enabled=false)",
              file=__import__("sys").stderr)
        return 3

    storage = Storage(config.data_root, event_name=config.event_name)
    storage._conn.execute("PRAGMA busy_timeout=3000")  # read side, WAL-safe

    host = str(web.get("host", "127.0.0.1"))
    port = int(web.get("port", 8080))
    server = WebServer((host, port), storage, config.data_root)
    print(f"HallOfFrame results server on http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())