"""Separate-process HTTP server for live results (spec §6.8, F6).

Runs OUT OF PROCESS from the timing app on purpose. It opens its own SQLite
read connection (safe under WAL against the app's single writer) and serves
only pre-rendered HTML plus files already on disk, so even a crowd of viewers
cannot perturb the evdev-triggered timing thread. Launch it explicitly::

    python -m hallofframe.web --config /path/to/config.toml

Routes:
  GET /                  race index (compact list; no images)
  GET /race/<id>         one race, a card per crossing with its captured frame
  GET /excel/<id>        JSON payload for the "Copy as Excel" buttons: the race
                         as {tsv, html} — the same tab-separated + HTML-table
                         pair the review window copies to clipboard, so pasting
                         into Excel keeps the column layout
  GET /excel/<id>.xls    same race as a downloadable Excel-compatible .xls
                         (HTML table; fallback for browsers/users that prefer a
                         file over the clipboard copy)
  GET /img/<relpath>     a captured frame by its stored relative path

Conditional requests (step 6.4): HTML is ``Cache-Control: no-cache`` and carries
an ``ETag``/``Last-Modified`` derived from the relevant ``updated_at``; a
matching ``If-None-Match``/``If-Modified-Since`` gets ``304 Not Modified`` with
no body. ``/img/`` files never change once written, so they are served
``immutable`` with a year-long max-age.

The app's ``Storage`` uses a single locked connection; this server NEVER touches
it. It builds its own ``Storage`` (a second, read-only connection) so reads never
block the app's writer. ``PRAGMA busy_timeout`` backs the rare WAL checkpoint
contention between many readers and the one writer.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import threading
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .config import load_config
from .render import local_hms
from .render.clipboard import clipboard_data
from .render.html import (_about_footer, _esc, _race_html, _row_value,
                          _updated_hms, page)
from .storage import Storage

_IMG_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}
_IMG_SUFFIXES = frozenset(_IMG_TYPES)


def _parse_iso(iso: str) -> datetime.datetime | None:
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _http_date(iso: str) -> str | None:
    dt = _parse_iso(iso)
    return formatdate(dt.timestamp(), usegmt=True) if dt else None


def _excel_filename(storage: Storage, race_id: int) -> str:
    race = storage.get_race(race_id)
    if race is None:
        return f"race-{race_id}.xls"
    parts = [race["race_no"] or "", race["heat_no"] or "", race["name"] or ""]
    base = "-".join(p for p in parts if p) or f"race-{race_id}"
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in base).strip()
    return f"{safe or f'race-{race_id}'}.xls"


def resolve_image_file(data_root: Path, rel: str) -> Path | None:
    """Resolve a stored relative image path to a captured frame file, or None.

    Only files under ``<data_root>/races`` with an image suffix are served, so a
    crafted URL cannot read ``config.toml``, the database or logs even though
    they live under the data root. Guarded against ``../`` traversal and null
    bytes in *rel*."""
    try:
        target = (Path(data_root) / rel).resolve()
        races_root = (Path(data_root) / "races").resolve()
    except (ValueError, OSError):
        return None
    if not target.is_relative_to(races_root):
        return None
    if target.suffix.lower() not in _IMG_SUFFIXES:
        return None
    if not target.is_file():
        return None
    return target


def build_index(storage: Storage) -> str:
    """Compact race list: newest first, one row per race with links to the race
    page and its Excel copy. Only reviewed races are published (spec §6.8).
    No images — photos live on each race page (step 6.4a)."""
    races = storage.list_races(reviewed_only=True)  # id DESC (newest first)
    updated = _updated_hms(storage.last_updated())
    rows = []
    for r in races:
        race, captures = storage.race_bundle(r["id"])  # step 1.4e
        n = len(captures)
        t0 = race["t0_wall"] if race["t0_wall"] is not None else None
        gun = local_hms(t0) if t0 is not None else "—"
        label = " · ".join(p for p in (
            f"RACE {race['race_no']}" if race["race_no"] else "UNLISTED",
            f"HEAT {race['heat_no']}" if race["heat_no"] else "") if p) or "UNLISTED"
        row_updated = _updated_hms(_row_value(race, "updated_at"))
        rows.append(
            '<tr class="row">'
            f'<td class="td-label">{_esc(label)}'
            f'<div class="row-updated">Updated {_esc(row_updated)}</div></td>'
            f'<td class="td-cat">{_esc(race["name"] or "")}</td>'
            f'<td class="td-gun">{_esc(gun)}</td>'
            f'<td class="td-n">{n}</td>'
            '<td class="td-actions">'
            f'<a href="/race/{r["id"]}" class="link link-view">View</a>'
            f'<button type="button" data-excel="{r["id"]}" class="excel-inline">'
            'Copy table</button></td></tr>')

    body_rows = "".join(rows) or (
        '<tr><td colspan="5" class="empty-row">No races recorded yet.</td></tr>')

    event = storage.event_name or ""
    header = (
        '<header class="index-head">'
        '<div class="index-head-row">'
        '<div class="index-brand-col">'
        f'<div class="event">{_esc(event)}</div>'
        '<div class="brand-lg">HallOf<span class="brand-red">Frame</span></div>'
        '<div class="index-sub">Finish-line results · races</div>'
        f'<div class="index-updated">Results updated {_esc(updated)}</div></div>'
        f'<div class="counts">{len(races)} race'
        f'{"s" if len(races) != 1 else ""}</div></div>'
        '<div class="index-note">Photos are on each race page.</div></header>'
    )
    main = (
        '<main class="main-index">'
        '<table class="index-table">'
        '<thead><tr class="index-thead">'
        '<th class="th">RACE</th><th class="th">CATEGORY</th>'
        '<th class="th">GUN</th><th class="th-right">#</th>'
        '<th class="th-right"></th></tr></thead>'
        f"<tbody>{body_rows}</tbody></table></main>"
    )
    footer = (
        '<footer class="index-footer">Copy table copies a race to the clipboard '
        "— paste it into a spreadsheet to keep the column layout."
        f" · {_about_footer()}</footer>"
    )
    return page(f"{event} — HallOfFrame results", header + main,
                width_px=960, footer=footer)


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
    updated = _updated_hms(storage.last_updated(race_id))
    header = (
        '<header class="racepage-head">'
        '<a href="/" class="back-link">&larr; All races</a>'
        f'<span class="event">{_esc(event)}</span>'
        f'<span class="counts">Results updated {_esc(updated)}</span></header>'
    )
    footer = f'<footer class="race-footer">{_about_footer()}</footer>'
    title = (f"{event} — Race "
             f"{race['race_no'] or race['name'] or race_id} — HallOfFrame")
    return page(title, header + f'<main class="main-race">{body}</main>',
                width_px=1120, footer=footer)


class WebServer(ThreadingHTTPServer):
    """HTTP server carrying shared state (its own read-only Storage)."""

    def __init__(self, server_address, storage: Storage, data_root: Path,
                 copy_heading: bool = True):
        super().__init__(server_address, WebHandler)
        self.storage = storage
        self.data_root = data_root
        self.copy_heading = copy_heading


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer

    # --- helpers ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        if code != 304:
            # RFC 7232 §4.1: a 304 carries no representation, so it must not
            # advertise the (empty) body's length.
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and code != 304:
            self.wfile.write(body)

    def _html(self, code: int, body: str,
              extra: dict[str, str] | None = None) -> None:
        self._send(code, body.encode("utf-8"), "text/html; charset=utf-8", extra)

    def _cache(self, updated_iso: str | None = None, mtime: float | None = None,
               immutable: bool = False) -> tuple[dict[str, str], bool]:
        """Build ETag/Last-Modified/Cache-Control and decide on a 304.

        ``updated_iso`` is the relevant ``updated_at`` (DB-wide or per race);
        ``mtime`` is used for image files (which never change once written)."""
        if updated_iso is not None:
            etag = f'"{updated_iso}"'
            last_modified = _http_date(updated_iso)
        elif mtime is not None:
            etag = f'"{int(mtime)}"'
            last_modified = formatdate(mtime, usegmt=True)
        else:
            return {}, False
        headers = {"ETag": etag}
        if last_modified:
            headers["Last-Modified"] = last_modified
        headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if immutable else "no-cache")
        inm = self.headers.get("If-None-Match")
        ims = self.headers.get("If-Modified-Since")
        # RFC 7232 §6: when If-None-Match is present, If-Modified-Since is
        # ignored. ORing them let a coarse (1 s) Last-Modified match while the
        # ETag had already changed, serving a stale page after an edit.
        if inm:
            not_modified = inm == etag
        else:
            not_modified = bool(ims and last_modified and ims == last_modified)
        return headers, not_modified

    # --- routes ----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        storage = self.server.storage

        if path == "/" or path == "/index.html":
            headers, not_modified = self._cache(
                updated_iso=storage.last_updated())
            self._html(304 if not_modified else 200,
                       "" if not_modified else build_index(storage), headers)
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

    def _published(self, storage: Storage, race_id: int) -> bool:
        """True only for a race the operator has closed in review.

        The index hides unreviewed races; the race/excel routes must enforce the
        same gate, or a still-running race is reachable by its (sequential) id."""
        race = storage.get_race(race_id)
        return race is not None and bool(race["reviewed"])

    def _race(self, storage: Storage, path: str) -> None:
        rest = path[len("/race/"):]
        try:
            race_id = int(rest)
        except ValueError:
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        if not self._published(storage, race_id):
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        headers, not_modified = self._cache(
            updated_iso=storage.last_updated(race_id))
        if not_modified:
            self._html(304, "", headers)
            return
        page_html = build_race_page(storage, race_id)
        if page_html is None:
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        self._html(200, page_html, headers)

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
        if not self._published(storage, race_id):
            self._html(404, "<h1>404</h1><p>Unknown race.</p>")
            return
        headers, not_modified = self._cache(
            updated_iso=storage.last_updated(race_id))
        if download:
            ctype = "application/vnd.ms-excel; charset=utf-8"
        else:
            ctype = "application/json; charset=utf-8"
        if not_modified:
            self._send(304, b"", ctype, headers)
            return
        tsv, markup = clipboard_data(storage, race_id,
                                     self.server.copy_heading)
        if download:
            self._send(200, markup.encode("utf-8"), ctype,
                       dict(headers, **{"Content-Disposition":
                            f"attachment; filename=\"{_excel_filename(storage, race_id)}\""}))
        else:
            payload = json.dumps({"tsv": tsv, "html": markup}).encode("utf-8")
            self._send(200, payload, ctype, headers)

    def _img(self, path: str) -> None:
        rel = unquote(path[len("/img/"):])
        ctype = _IMG_TYPES.get(Path(rel).suffix.lower(), "application/octet-stream")
        target = resolve_image_file(self.server.data_root, rel)
        if target is None:
            self._send(404, b"", ctype)
            return
        headers, not_modified = self._cache(
            mtime=target.stat().st_mtime, immutable=True)
        if not_modified:
            self._send(304, b"", ctype, headers)
            return
        self._send(200, target.read_bytes(), ctype, headers)

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
              file=sys.stderr)
        return 3

    try:
        # Own read-only connection: never creates the schema or migrates, and
        # cannot write, so it never contends with the app's writer (§8, §13.3).
        storage = Storage(config.data_root, event_name=config.event_name,
                          read_only=True)
    except Exception as exc:  # missing/unreadable DB: nothing to serve yet
        print(f"cannot open results database read-only: {exc}", file=sys.stderr)
        return 1

    host = str(web.get("host", "127.0.0.1"))
    port = int(web.get("port", 8080))
    copy_heading = bool(web.get("copy_heading", True))
    server = WebServer((host, port), storage, config.data_root,
                       copy_heading=copy_heading)
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
