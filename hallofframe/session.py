"""Operator session state machine (plan Phase 3, step 3.1).

One object holds the operator's position in the race lifecycle. Every
transition is a named method that returns ``None`` or raises ``SessionError``
with a message suitable for a toast, so the window never pokes raw flags.

``derive_state`` is a pure, Qt-free mapping from the session phase plus live
stream/calibration health to the ``AppState`` the UI renders. Precedence when
several conditions hold at once (highest first):

    RECORDING > ARMED > REVIEW > RACE_OVER > STREAM_DOWN > RECALIBRATE > READY

A stream drop must NOT knock the UI out of RECORDING (timing continues
regardless), nor out of REVIEW / RACE_OVER: a timing-only race runs with the
stream down yet must still be reviewed and exported.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from .ui.state import AppState


class Phase(enum.Enum):
    IDLE = "idle"                # Ready: arm, or load a race
    ARMED = "armed"              # Ctrl+S armed, waiting for t0
    RECORDING = "recording"      # a race is running
    RACE_OVER = "race_over"      # race ended; summary shown
    REVIEW = "review"            # crossing review screen


class SessionError(Exception):
    """An illegal session transition; the message is shown as a toast."""


@dataclass
class Session:
    phase: Phase = Phase.IDLE
    focused_race_id: int | None = None   # race shown in RACE_OVER / REVIEW
    return_to: Phase = Phase.IDLE        # where Esc from REVIEW goes

    def arm(self) -> None:
        """IDLE or RACE_OVER -> ARMED."""
        if self.phase not in (Phase.IDLE, Phase.RACE_OVER):
            raise SessionError("Can't arm in this state.")
        self.phase = Phase.ARMED

    def disarm(self) -> None:
        """ARMED -> IDLE."""
        if self.phase is not Phase.ARMED:
            raise SessionError("Can't disarm in this state.")
        self.phase = Phase.IDLE

    def race_started(self, race_id: int) -> None:
        """ARMED -> RECORDING, focused = race_id."""
        if self.phase is not Phase.ARMED:
            raise SessionError("Can't start a race in this state.")
        self.phase = Phase.RECORDING
        self.focused_race_id = race_id

    def race_ended(self, race_id: int) -> None:
        """RECORDING -> RACE_OVER, focused = race_id."""
        if self.phase is not Phase.RECORDING:
            raise SessionError("Can't end a race in this state.")
        self.phase = Phase.RACE_OVER
        self.focused_race_id = race_id

    def load_race(self, race_id: int) -> None:
        """IDLE -> RACE_OVER, focused = race_id."""
        if self.phase is not Phase.IDLE:
            raise SessionError("Load race only in Ready.")
        self.phase = Phase.RACE_OVER
        self.focused_race_id = race_id

    def open_review(self, race_id: int) -> None:
        """RACE_OVER or IDLE -> REVIEW, focused = race_id, return_to set."""
        if self.phase not in (Phase.RACE_OVER, Phase.IDLE):
            raise SessionError("Can't review in this state.")
        self.return_to = self.phase
        self.phase = Phase.REVIEW
        self.focused_race_id = race_id

    def close_review(self) -> None:
        """REVIEW -> return_to."""
        if self.phase is not Phase.REVIEW:
            raise SessionError("Not reviewing.")
        self.phase = self.return_to

    def dismiss(self) -> None:
        """RACE_OVER -> IDLE."""
        if self.phase is not Phase.RACE_OVER:
            raise SessionError("Can't dismiss in this state.")
        self.phase = Phase.IDLE


def derive_state(session: Session, stream_alive: bool, cal_ok: bool) -> AppState:
    """Map the session phase and stream health to the state the UI renders.

    Precedence: RECORDING > ARMED > REVIEW > RACE_OVER > STREAM_DOWN >
    RECALIBRATE > READY. ``cal_ok`` is True when calibration matches the live
    stream (or there is no stream).
    """
    phase = session.phase
    if phase is Phase.RECORDING:
        return AppState.RECORDING
    if phase is Phase.ARMED:
        return AppState.ARMED
    if phase is Phase.REVIEW:
        return AppState.REVIEW
    if phase is Phase.RACE_OVER:
        return AppState.RACE_OVER
    if not stream_alive:
        return AppState.STREAM_DOWN
    if not cal_ok:
        return AppState.RECALIBRATE
    return AppState.READY


# --------------------------------------------------------------------- keymap
# Plan step 3.3: one table describes every operator key. ``MainWindow`` builds
# one ``QShortcut`` per distinct ``key`` and rebuilds the ``KeyBar`` from the
# same list, so the two can never drift apart.
#
# ``show`` marks whether the key gets a cap in the key bar (some shortcuts, e.g.
# F1 About and Esc to dismiss RACE_OVER, stay active without a cap). ``shortcut``
# is False for informational caps the focused screen handles itself (REVIEW's
# arrows / Tab / Del), which must NOT become application-wide shortcuts or they
# would steal those keys from the review screen. ``typable`` mirrors the old
# ``_typable_shortcuts`` rule: the shortcut stands down while a QLineEdit has
# focus so bow numbers and race names arrive intact.
@dataclass(frozen=True)
class Key:
    key: str          # Qt sequence, e.g. "Ctrl+S"
    label: str        # keybar text
    action: str       # name of a MainWindow method
    hot: bool = False
    typable: bool = False
    show: bool = True
    shortcut: bool = True


def _k(key: str, label: str, action: str, hot: bool = False,
       typable: bool = False, show: bool = True,
       shortcut: bool = True) -> Key:
    return Key(key, label, action, hot, typable, show, shortcut)


_READY_KEYS = [
    _k("Ctrl+S", "Arm", "_arm_start", hot=True),
    _k("C", "Calibrate", "_calibrate", typable=True),
    _k("L", "Load race", "_load_selected_race", typable=True),
    _k("Shift+↑", "Move up", "_move_up"),
    _k("Shift+↓", "Move down", "_move_down"),
    _k("D", "Save DB HTML", "_export_html", typable=True),
    _k("Ctrl+Q", "Quit", "_quit"),
    # Active but uncapped in the key bar (kept from the old global shortcuts).
    _k("Ctrl+Z", "Undo last", "_undo_last", show=False),
    _k("Esc", "Cancel", "_esc", show=False),
    _k("F12", "End race", "_end_key", show=False),
    _k("F1", "About", "_toggle_about", show=False),
    _k("E", "Rename race", "_on_e", show=False, typable=True),
    _k("R", "Review crossings", "_on_r", show=False, typable=True),
    _k("N", "Next race", "_next_race", show=False, typable=True),
    _k("/", "Find race", "_focus_filter", show=False, typable=True),
    _k("End", "Unlisted race", "_end_unlisted", show=False, typable=True),
    _k("Return", "Start race", "_start_key", show=False, typable=True),
    _k("Enter", "Start race", "_start_key", show=False, typable=True),
    _k("SPACE", "Record crossing", "_crossing_key", show=False, typable=True),
]

_RECALIBRATE_KEYS = [
    _k("C", "Calibrate", "_calibrate", hot=True, typable=True),
    _k("Ctrl+Q", "Quit", "_quit"),
    _k("Ctrl+S", "Arm", "_arm_start", show=False),
    _k("Ctrl+Z", "Undo last", "_undo_last", show=False),
    _k("Esc", "Cancel", "_esc", show=False),
    _k("F12", "End race", "_end_key", show=False),
    _k("F1", "About", "_toggle_about", show=False),
    _k("E", "Rename race", "_on_e", show=False, typable=True),
    _k("L", "Load race", "_load_selected_race", show=False, typable=True),
    _k("R", "Review crossings", "_on_r", show=False, typable=True),
    _k("N", "Next race", "_next_race", show=False, typable=True),
    _k("D", "Save DB HTML", "_export_html", show=False, typable=True),
    _k("Shift+↑", "Move up", "_move_up", show=False),
    _k("Shift+↓", "Move down", "_move_down", show=False),
    _k("/", "Find race", "_focus_filter", show=False, typable=True),
    _k("End", "Unlisted race", "_end_unlisted", show=False, typable=True),
    _k("Return", "Start race", "_start_key", show=False, typable=True),
    _k("Enter", "Start race", "_start_key", show=False, typable=True),
    _k("SPACE", "Record crossing", "_crossing_key", show=False, typable=True),
]

_RACE_OVER_KEYS = [
    _k("R", "Review crossings", "_on_r", hot=True, typable=True),
    _k("E", "Copy as Excel", "_on_e", typable=True),
    _k("N", "Next race", "_next_race", typable=True),
    _k("Ctrl+Q", "Quit", "_quit"),
    _k("Ctrl+S", "Arm", "_arm_start", show=False),
    _k("Ctrl+Z", "Undo last", "_undo_last", show=False),
    _k("Esc", "Cancel", "_esc", show=False),
    _k("F12", "End race", "_end_key", show=False),
    _k("F1", "About", "_toggle_about", show=False),
    _k("C", "Calibrate", "_calibrate", show=False, typable=True),
    _k("L", "Load race", "_load_selected_race", show=False, typable=True),
    _k("D", "Save DB HTML", "_export_html", show=False, typable=True),
    _k("Shift+↑", "Move up", "_move_up", show=False),
    _k("Shift+↓", "Move down", "_move_down", show=False),
    _k("/", "Find race", "_focus_filter", show=False, typable=True),
    _k("End", "Unlisted race", "_end_unlisted", show=False, typable=True),
    _k("Return", "Start race", "_start_key", show=False, typable=True),
    _k("Enter", "Start race", "_start_key", show=False, typable=True),
    _k("SPACE", "Record crossing", "_crossing_key", show=False, typable=True),
]

_ARMED_KEYS = [
    _k("F12", "Disarm", "_end_key"),
    _k("Esc", "Cancel", "_esc"),
    _k("Ctrl+S", "Arm", "_arm_start", show=False),
    _k("Ctrl+Q", "Quit", "_quit", show=False),
    _k("Ctrl+Z", "Undo last", "_undo_last", show=False),
    _k("F1", "About", "_toggle_about", show=False),
    _k("E", "Rename race", "_on_e", show=False, typable=True),
    _k("L", "Load race", "_load_selected_race", show=False, typable=True),
    _k("R", "Review crossings", "_on_r", show=False, typable=True),
    _k("N", "Next race", "_next_race", show=False, typable=True),
    _k("D", "Save DB HTML", "_export_html", show=False, typable=True),
    _k("C", "Calibrate", "_calibrate", show=False, typable=True),
    _k("Shift+↑", "Move up", "_move_up", show=False),
    _k("Shift+↓", "Move down", "_move_down", show=False),
    _k("/", "Find race", "_focus_filter", show=False, typable=True),
    _k("End", "Unlisted race", "_end_unlisted", show=False, typable=True),
    _k("Return", "Start race", "_start_key", show=False, typable=True),
    _k("Enter", "Start race", "_start_key", show=False, typable=True),
    _k("SPACE", "Record crossing", "_crossing_key", show=False, typable=True),
]

_RECORDING_KEYS = [
    _k("SPACE", "Record crossing", "_crossing_key", hot=True, typable=True),
    _k("F12", "End race", "_end_key"),
    _k("Ctrl+Z", "Undo last", "_undo_last"),
    _k("Ctrl+Q", "Quit", "_quit", show=False),
    _k("Esc", "Cancel", "_esc", show=False),
    _k("F1", "About", "_toggle_about", show=False),
    _k("Ctrl+S", "Arm", "_arm_start", show=False),
    _k("E", "Rename race", "_on_e", show=False, typable=True),
    _k("L", "Load race", "_load_selected_race", show=False, typable=True),
    _k("R", "Review crossings", "_on_r", show=False, typable=True),
    _k("N", "Next race", "_next_race", show=False, typable=True),
    _k("D", "Save DB HTML", "_export_html", show=False, typable=True),
    _k("C", "Calibrate", "_calibrate", show=False, typable=True),
    _k("Shift+↑", "Move up", "_move_up", show=False),
    _k("Shift+↓", "Move down", "_move_down", show=False),
    _k("/", "Find race", "_focus_filter", show=False, typable=True),
    _k("End", "Unlisted race", "_end_unlisted", show=False, typable=True),
    _k("Return", "Start race", "_start_key", show=False, typable=True),
    _k("Enter", "Start race", "_start_key", show=False, typable=True),
]

# REVIEW keeps Enter/Space/Return for the focused bow field, so they are absent
# here (the review screen owns them). The arrows / Tab / Del / Ins and the
# restore/clone letters are informational caps handled by the screen, not
# application-wide shortcuts — making them shortcuts would steal Del from a
# focused bow field. Their action names still resolve to MainWindow forwarders
# so a keybar click does the same thing as the key (plan step 7.2).
_REVIEW_KEYS = [
    _k("↑/↓", "Select crossing", "_noop", hot=True, shortcut=False),
    _k("Tab", "Next bow field", "_noop", shortcut=False),
    _k("Del", "Remove crossing", "_review_remove", shortcut=False),
    _k("Shift+Del", "Restore crossing", "_review_restore", show=False,
       shortcut=False),
    _k("U", "Restore crossing", "_review_restore", shortcut=False),
    _k("Ins", "Clone crossing", "_review_clone", shortcut=False),
    _k("Shift+D", "Clone crossing", "_review_clone", show=False, shortcut=False),
    _k("E", "Edit race", "_on_e", typable=True),
    _k("Esc", "Back to Ready", "_esc"),
    _k("Ctrl+Q", "Quit", "_quit", show=False),
    _k("Ctrl+Z", "Undo last", "_undo_last", show=False),
    _k("F1", "About", "_toggle_about", show=False),
    _k("F12", "End race", "_end_key", show=False),
    _k("C", "Calibrate", "_calibrate", show=False, typable=True),
    _k("D", "Save DB HTML", "_export_html", show=False, typable=True),
    _k("L", "Load race", "_load_selected_race", show=False, typable=True),
    _k("R", "Review crossings", "_on_r", show=False, typable=True),
    _k("N", "Next race", "_next_race", show=False, typable=True),
    _k("Ctrl+S", "Arm", "_arm_start", show=False),
    _k("Shift+↑", "Move up", "_move_up", show=False),
    _k("Shift+↓", "Move down", "_move_down", show=False),
    _k("/", "Find race", "_focus_filter", show=False, typable=True),
    _k("End", "Unlisted race", "_end_unlisted", show=False, typable=True),
]

KEYMAP: dict[AppState, list[Key]] = {
    AppState.READY: _READY_KEYS,
    AppState.STREAM_DOWN: _READY_KEYS,
    AppState.RECALIBRATE: _RECALIBRATE_KEYS,
    AppState.RACE_OVER: _RACE_OVER_KEYS,
    AppState.ARMED: _ARMED_KEYS,
    AppState.RECORDING: _RECORDING_KEYS,
    AppState.REVIEW: _REVIEW_KEYS,
}

# Static key-bar notes; ARMED and RECORDING are config-dependent and are
# composed by MainWindow from the live trigger config.
KEYBAR_NOTE: dict[AppState, str] = {
    AppState.STREAM_DOWN: "Stream down — race starts timing-only (no photos)",
}
