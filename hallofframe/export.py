"""Deprecated compatibility shim (plan step 6.1).

``export.py`` was split into ``render/__init__.py`` (format helpers),
``render/csv.py``, ``render/clipboard.py`` and ``render/html.py``. This module
re-exports the old names for one release so existing imports keep working, and
emits a :class:`DeprecationWarning` on import. New code should import from
``hallofframe.render`` (or its submodules) directly.
"""
from __future__ import annotations

import datetime  # noqa: F401  (kept for backward-compatible monkeypatching)
import warnings

from .render import (format_elapsed, parse_elapsed, utc_iso, local_hms,
                     flag_word)
from .render.clipboard import clipboard_data
from .render.csv import (_COLUMNS, _ALL_COLUMNS, _data_rows, export_csv,
                         export_all_csv)
from .render.html import (_C, _MONO, _SANS, _FILTER_JS, _THUMB_W, _THUMB_H,
                          _esc, _src, _row_value, _thumb_html, _meta_cell,
                          _card_html, _race_html, _all_race_blocks,
                          build_all_html, export_all_html)

warnings.warn(
    "hallofframe.export is deprecated; import from hallofframe.render "
    "(render.csv, render.clipboard, render.html) instead.",
    DeprecationWarning,
    stacklevel=2,
)
