"""Roster rendering and dialog orchestration (plan step 2.4, extracted in 3.3).

The main window used to carry the roster chip/banner rendering and the
Add/Rename/Skip/Move dialog plumbing inline. It is Qt-only glue around the
Qt-free ``hallofframe.roster.Roster``; this module owns it so ``main_window``
only forwards. Behaviour is unchanged: the banner text, the chip, the optimistic
concurrency checks and the "no roster edits while armed or recording" guard all
move here verbatim.
"""
from __future__ import annotations

import os

from ..roster import Roster, RosterWriteError, recorded_keys
from . import styles
from .ready_screen import choose_roster_file, roster_path
from .state import AppState
from .widgets import Banner


class RosterView:
    """Renders the roster and owns its dialogs; ``MainWindow`` owns one."""

    def __init__(self, ready, banner_host, roster: Roster, storage, toast,
                 parent, logger=None, state=None, focused_race_id=None,
                 recompute=None):
        self.ready = ready
        self.banner_host = banner_host
        self.roster = roster
        self.storage = storage
        self._toast = toast
        self.parent = parent
        self._logger = logger
        self._state = state or (lambda: None)
        self._focused_race_id = focused_race_id or (lambda: None)
        self._recompute = recompute or (lambda: None)

    # ------------------------------------------------------------------ loading
    def load(self, path: str | None = None) -> None:
        """Load the roster (startup or a manual Load roster…) and render it.

        A configured-but-missing path never auto-writes an example roster; it is
        offered as an action instead (BEHAVIOUR §4)."""
        if path is None:
            path = roster_path(self.parent.config)
        self.roster.load(path)
        self.render()

    def reload(self) -> None:
        self.roster.load()
        self.render()

    def load_dialog(self) -> None:
        if self._state() in (AppState.ARMED, AppState.RECORDING):
            self._toast("Can't load a roster while armed or recording.")
            return
        path = choose_roster_file(self.parent, self.parent.config.data_root)
        if path:
            self.roster.load(path)
            self.render()

    def write_example(self) -> None:
        if self.roster.path:
            try:
                self.roster.write_example()
            except OSError as exc:
                self._toast(f"Could not write example roster: {exc}")
                return
            self.render()

    # ----------------------------------------------------------------- render
    def render(self) -> None:
        """The single render step (plan step 2.4): push ``roster.races``, the
        recorded keys and the skipped keys into the picker, then rebuild the chip
        and banners. Display order is file order — never sorted."""
        result = self.roster.result
        recorded = recorded_keys(self.storage)
        self.ready.set_races(self.roster.races, recorded=recorded,
                             skipped=self.roster.skipped_keys())
        self.render_chip(result)
        self.render_banner(result, recorded)

    def render_chip(self, result) -> None:
        if not result.ok:
            self.ready.set_roster("", 0, "")
            return
        filename = os.path.basename(result.path)
        self.ready.set_roster(filename, len(result.races), result.loaded_at,
                              duplicates=len(result.duplicates),
                              dup_callback=self.show_duplicates)

    def render_banner(self, result, recorded: set) -> None:
        self.banner_host.clear()
        if not result.ok:
            if result.missing:
                self.banner_host.add_banner(Banner(
                    styles.AMBER,
                    f"No roster at {result.path}",
                    "Nothing was created. Racing without a roster is allowed.",
                    [("Load roster…", self.load_dialog),
                     ("Write an example roster", self.write_example)]))
            elif result.file_error:
                self.banner_host.add_banner(Banner(
                    styles.RED,
                    f"Roster unreadable · {os.path.basename(result.path)}",
                    result.file_error,
                    [("Reload", self.reload),
                     ("Load another roster…", self.load_dialog)]))
            elif result.errors:
                line = result.errors[0][0]
                self.banner_host.add_banner(Banner(
                    styles.RED,
                    f"Roster failed to parse · {os.path.basename(result.path)}"
                    f" line {line}",
                    "Expected race_no, heat_no, name. No roster is loaded.",
                    [("Reload", self.reload),
                     ("Load another roster…", self.load_dialog)]))
            return
        # A roster is loaded: duplicates and/or dropped recorded races.
        loaded_keys = {r.key for r in result.races}
        # Only numbered races count as "dropped"; provisional/unlisted races key
        # on a timestamp name ("name", ...) that can never be in the roster.
        dropped = sorted(k for k in (recorded - loaded_keys) if k[0] == "num")
        if result.duplicates:
            key, l_a, l_b = result.duplicates[0]
            headline = (f"Duplicate key {self.key_display(key)}"
                        f" · lines {l_a} and {l_b}")
            if len(result.duplicates) > 1:
                headline += f" (+{len(result.duplicates) - 1} more)"
            self.banner_host.add_banner(Banner(
                styles.AMBER, headline,
                "Rows are not silently dropped. Resolve in the file, or keep the first.",
                [("Show both", self.show_duplicates), ("Reload", self.reload)]))
        if dropped:
            self.banner_host.add_banner(Banner(
                styles.BLUE,
                f"{len(dropped)} recorded race{'s' if len(dropped) != 1 else ''}"
                " are not in this roster",
                "After a reload. Results are untouched; the running order changed.",
                [("List them", lambda: self.show_dropped(dropped))]))

    @staticmethod
    def key_display(key) -> str:
        if key and key[0] == "num":
            rn, hn = key[1], key[2]
            return f"{rn}-H{hn}" if hn else str(rn)
        return str(key[1]) if key else ""

    def show_duplicates(self) -> None:
        dupes = self.roster.result.duplicates
        if not dupes:
            return
        parts = [f"{self.key_display(k)} (lines {a}, {b})" for k, a, b in dupes]
        self._toast("Duplicates: " + "; ".join(parts))

    def show_dropped(self, dropped) -> None:
        names = " · ".join(self.key_display(d) for d in dropped[:10])
        if len(dropped) > 10:
            names += f" … +{len(dropped) - 10} more"
        self._toast(f"Recorded, not in roster: {names}", timeout_ms=12000)

    # ------------------------------------------------------------- race edits
    def move_selected(self, delta: int) -> None:
        """Shift+↑/↓ in READY: move the selected row one place in file order
        (plan step 2.4). The file is never re-sorted; the moved row stays
        selected."""
        if self._state() not in (AppState.READY, AppState.STREAM_DOWN,
                                 AppState.RECALIBRATE):
            return
        key = self.ready.selected_key()
        if key is None:
            return
        try:
            self.roster.move(key, delta)
        except RosterWriteError as exc:
            self._toast(f"Could not move race: {exc}")
            return
        self.render()
        self.ready.select_key(key)

    def toggle_skip(self) -> None:
        if self._state() in (AppState.ARMED, AppState.RECORDING):
            self._toast("Can't skip while armed or recording.")
            return
        if self.ready.selected_is_unlisted():
            return
        race, _ = self.ready.current_selection()
        if race is None or not self.roster.path:
            return
        skipping = race.key not in self.roster.skipped_keys()
        try:
            self.roster.skip(race.key, skip=skipping)
        except Exception as exc:
            self._toast(f"Could not update roster: {exc}")
            return
        if self._logger is not None:
            self._logger.info("roster", "skip" if skipping else "unskip",
                              key=str(race.key), name=race.name,
                              file=self.roster.path)
        self.render()

    def open_add_race(self, race_no: str = "", heat_no: str = "") -> None:
        if self._state() in (AppState.ARMED, AppState.RECORDING):
            self._toast("Can't edit the roster while armed or recording.")
            return
        if not self.roster.path:
            self._toast("No roster loaded — Load roster… first.")
            return
        from ..ui.roster_dialog import AddRaceDialog
        dlg = AddRaceDialog(self.roster.path, race_no, heat_no,
                            expected=self.roster.rows, logger=self._logger,
                            parent=self.parent,
                            after_key=self.ready.selected_key())
        dlg.result_applied.connect(lambda _r: self.reload())
        dlg.exec()

    def open_rename(self) -> None:
        """E in READY: correct the selected roster row's name (F6)."""
        if self._state() in (AppState.ARMED, AppState.RECORDING):
            self._toast("Can't rename while armed or recording.")
            return
        if not self.roster.path:
            self._toast("No roster loaded — Load roster… first.")
            return
        if self.ready.selected_is_unlisted():
            self._toast("Nothing to rename on an unlisted race.")
            return
        race, _ = self.ready.current_selection()
        if race is not None:
            self.rename_dialog(race.race_no, race.heat_no, race.name)

    def edit_race(self) -> None:
        """Review-side *Edit race* (step 2.6): an unlisted race gets its number
        after the fact via ``storage.identify_race``; a listed race is renamed.
        The dialog also offers to append the row to the roster."""
        if self._state() in (AppState.ARMED, AppState.RECORDING):
            self._toast("Can't edit the roster while armed or recording.")
            return
        race_id = self._focused_race_id()
        if race_id is None:
            return
        row = self.storage.get_race(race_id)
        if row is None:
            return
        if not (row["race_no"] or ""):
            self.rename_dialog(row["race_no"] or "", row["heat_no"] or "",
                               row["name"] or "", editable=True,
                               race_id=race_id)
        else:
            self.rename_dialog(row["race_no"], row["heat_no"], row["name"])
        self.reload()
        self._recompute()

    def rename_dialog(self, race_no, heat_no, name, *, editable=False,
                      race_id=None) -> None:
        from ..ui.roster_dialog import RenameDialog
        dlg = RenameDialog(self.roster.path, race_no, heat_no, name,
                           recorded_keys(self.storage),
                           self.storage,
                           expected=self.roster.rows, logger=self._logger,
                           parent=self.parent, editable_numbers=editable,
                           race_id=race_id, roster=self.roster)
        dlg.result_applied.connect(lambda _r: self.reload())
        dlg.exec()
