"""App build/version stamp: the git commit + date of the code that is running.

Kept in a small non-UI module so both the About screen and the separate-process
web server can report it without dragging in Qt.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git_stamp() -> str | None:
    """Live commit + date for a source checkout, or None if not a git tree.

    Walks up from the package directory to locate the repository root and reads
    HEAD directly, so the About screen reports the code actually on disk.
    """
    root = Path(__file__).resolve().parent
    for parent in root.parents:
        if (parent / ".git").exists():
            break
    else:
        return None
    try:
        commit = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, timeout=2).stdout.strip()
        date = subprocess.run(
            ["git", "-C", str(parent), "log", "-1", "--format=%cI"],
            capture_output=True, text=True, timeout=2).stdout.strip()[:10]
    except Exception:
        return None
    if not commit or not date:
        return None
    return f"{commit} · {date}"


def build_stamp() -> str:
    """Commit + date, preferring the embed written at packaging time.

    ``hallofframe/_build.py`` is generated at install time (INSTALL.md) with
    ``COMMIT`` and ``DATE`` and is baked into a binary deployment, so a shipped
    build always reports the exact code it was made from. A source checkout has
    no such file, so we fall back to the live git HEAD.
    """
    try:
        from ._build import COMMIT, DATE  # type: ignore
        return f"{COMMIT[:7]} · {DATE}"
    except Exception:
        pass
    return _git_stamp() or "source checkout"