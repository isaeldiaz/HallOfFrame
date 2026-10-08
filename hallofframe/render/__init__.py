"""Rendering helpers shared by the CSV, clipboard and HTML exporters.

The split out of ``export.py`` (plan step 6.1) keeps the format helpers here so
``render.csv``, ``render.clipboard`` and ``render.html`` can share them without
importing each other's heavy modules. This module must not import
``render.html`` (that would be circular).
"""
from __future__ import annotations

import datetime


def format_elapsed(elapsed_s: float) -> str:
    """M:SS.cc e.g. 6:12.48 (hundredths of a second)."""
    cs = round(elapsed_s * 100.0)
    minutes, rem = divmod(cs, 6000)
    seconds, centis = divmod(rem, 100)
    return f"{minutes}:{seconds:02d}.{centis:02d}"


def parse_elapsed(text: str) -> float | None:
    """Parse an elapsed-time string (the inverse of :func:`format_elapsed`).

    Accepts ``[M:]SS[.cc]`` — e.g. ``6:12.48``, ``12.5``, ``:45``. Returns the
    elapsed seconds, or None if the text is not a valid elapsed time.
    """
    t = text.strip()
    if not t:
        return None
    neg = t.startswith("-")
    if neg:
        t = t[1:]
    had_colon = ":" in t
    try:
        if had_colon:
            minutes, seconds = t.split(":", 1)
            minutes = int(minutes) if minutes else 0
        else:
            minutes, seconds = 0, t
        seconds = float(seconds)
    except ValueError:
        return None
    if minutes < 0 or seconds < 0:
        return None
    if had_colon and seconds >= 60:
        return None
    value = minutes * 60.0 + seconds
    return -value if neg else value


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
