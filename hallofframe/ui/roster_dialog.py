"""Roster editing dialogs (WP4/WP6/WP7; mockups F4–F9).

Modal ``QDialog``s launched from the Ready screen (add / rename) and the Review
screen (identify / merge). Each performs its roster write via the single atomic
path in :mod:`hallofframe.races` and emits the fresh ``RosterLoad`` on
``result_applied`` so the caller refreshes what is displayed. Nothing here is
reachable while armed or recording — the caller enforces that.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..races import (RosterWriteError, _cell, add_row, load_races, race_key,
                     rename_race)
from . import styles


def _caption(text, color=styles.TEXT_DIM):
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{color}; font-size:13px; letter-spacing:.14em;"
        " text-transform:uppercase;")
    return lbl


def _field(value="", editable=True):
    edit = QLineEdit(value)
    edit.setReadOnly(not editable)
    return edit


def _primary_button(text, callback, accent=styles.GREEN_TEXT):
    btn = QPushButton(text)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.clicked.connect(callback)
    btn.setStyleSheet(
        f"background:#151d16; border:1px solid #2f6b45; color:{accent};"
        " border-radius:4px; padding:10px 18px; font-size:18px;")
    return btn


def _muted_button(text, callback):
    btn = QPushButton(text)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.clicked.connect(callback)
    btn.setStyleSheet(
        f"background:{styles.PANEL}; border:1px solid {styles.PANEL_BORDER};"
        f" color:{styles.TEXT_SECONDARY}; border-radius:4px;"
        " padding:10px 18px; font-size:18px;")
    return btn


class _BaseDialog(QDialog):
    result_applied = Signal(object)  # RosterLoad

    def __init__(self, csv_path, expected=None, title="", logger=None,
                 parent=None):
        super().__init__(parent)
        self.csv_path = csv_path
        self.expected = expected
        self.logger = logger
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(f"QDialog{{background:{styles.BG};}}")

    def _log(self, action: str, **fields) -> None:
        # BEHAVIOUR §10: one line per roster mutation — the audit trail.
        if self.logger is not None:
            self.logger.info("roster", action, file=self.csv_path, **fields)

    def _warn(self, msg):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Roster", msg)

    def _emit_result(self, result):
        self.result_applied.emit(result)
        self.accept()


class AddRaceDialog(_BaseDialog):
    """Add a race or heat (F4). Key arrives pre-parsed; the name is the only
    field the operator types. A collision routes into the F5 panel instead of an
    error — the existing row is shown and can be selected."""

    def __init__(self, csv_path, race_no="", heat_no="", name="", expected=None,
                 logger=None, parent=None):
        super().__init__(csv_path, expected, "Add race to roster", logger,
                         parent)
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)
        root.addWidget(_caption("Add race to roster"))

        self.sub = QLabel("")
        self.sub.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        self.sub.setWordWrap(True)
        root.addWidget(self.sub)

        fields = QHBoxLayout()
        fields.setSpacing(16)
        self.rn = _field(race_no)
        self.hn = _field(heat_no)
        self.name = _field(name)
        self.name.setFocus()
        for w, cap in ((self.rn, "Race no."), (self.hn, "Heat"), (self.name, "Name")):
            col = QVBoxLayout()
            col.setSpacing(8)
            col.addWidget(_caption(cap))
            col.addWidget(w)
            fields.addLayout(col)
        root.addLayout(fields)

        self.collision = QWidget()
        self.collision_v = QVBoxLayout(self.collision)
        self.collision_v.setContentsMargins(16, 16, 16, 16)
        self.collision_v.setSpacing(10)
        self.collision.hide()
        root.addWidget(self.collision)

        row = QHBoxLayout()
        row.addWidget(_primary_button("Add race", self._add))
        row.addWidget(_muted_button("Cancel", self.reject))
        row.addStretch(1)
        src = QLabel("source=added")
        src.setProperty("mono", True)
        src.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:13px;"
                          f" color:{styles.TEXT_FAINT};")
        row.addWidget(src)
        root.addLayout(row)

    def _values(self):
        return (self.rn.text().strip(), self.hn.text().strip(),
                self.name.text().strip())

    def _add(self):
        rn, hn, name = self._values()
        if not rn or not name:
            self._warn("Race number and name are required.")
            return
        self.sub.setText(f"Written to {self.csv_path}, "
                         f"inserted by {rn}-H{hn if hn else ''}.")
        try:
            result, outcome = add_row(self.csv_path, rn, hn, name,
                                      expected=self.expected)
        except RosterWriteError as exc:
            self._warn(str(exc))
            return
        if outcome == "collision":
            self._show_collision(rn, hn)
            return
        self._log("add", race_no=rn, heat_no=hn, name=name)
        self._emit_result(result)

    def _find_existing(self, rn, hn):
        key = race_key(rn, hn, "x")
        for r in load_races(self.csv_path).races:
            if r.key == key:
                return r
        return None

    def _show_collision(self, rn, hn):
        existing = self._find_existing(rn, hn)
        while self.collision_v.count():
            item = self.collision_v.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        key_disp = f"{rn}-H{hn}" if hn else str(rn)
        self.collision_v.addWidget(
            _caption(f"Race {key_disp} is already in the roster",
                     styles.AMBER_TEXT))
        info = QLabel("Matched on the normalised key — leading zeros dropped, "
                      "H1 and 1 equal.")
        info.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        info.setWordWrap(True)
        self.collision_v.addWidget(info)
        if existing is not None:
            val = QLabel(existing.display)
            val.setProperty("mono", True)
            val.setStyleSheet(
                f"font-family:'{styles.FONT_MONO}'; font-size:19px;"
                f" color:{styles.TEXT_PRIMARY};")
            self.collision_v.addWidget(val)
        btnrow = QHBoxLayout()
        btnrow.addWidget(_primary_button(
            "Select it", lambda: self._select(existing)))
        # Only offer to correct the name when the typed name differs (§8).
        if existing is not None and \
                _cell(self.name.text()) != _cell(existing.name):
            btnrow.addWidget(_muted_button(
                "Select it and correct the name",
                lambda: self._select_and_rename(existing)))
        btnrow.addWidget(_muted_button("Cancel", self.reject))
        self.collision_v.addLayout(btnrow)
        self.collision.show()

    def _select(self, existing):
        self._chosen = existing
        self.accept()

    def _select_and_rename(self, existing):
        if existing is not None:
            try:
                result, _ = rename_race(self.csv_path, existing.key,
                                        self.name.text().strip() or existing.name,
                                        expected=self.expected)
            except RosterWriteError as exc:
                self._warn(str(exc))
                return
            self._log("rename", key=str(existing.key), name=existing.name)
            self._chosen = existing
            self._emit_result(result)

    @property
    def chosen_race(self):
        return getattr(self, "_chosen", None)


class RenameDialog(_BaseDialog):
    """Correct a race name (F6). Numbers are the key and are read-only; the
    stored recorded result keeps its name unless *Also update recorded race*."""

    def __init__(self, csv_path, race, recorded: set, storage, expected=None,
                 logger=None, parent=None):
        super().__init__(csv_path, expected, "Correct race name", logger,
                         parent)
        self.race = race
        self.storage = storage
        self.setMinimumWidth(600)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)
        root.addWidget(_caption("Correct race name"))
        key_lbl = QLabel(f"{race.race_no} · H{race.heat_no}"
                         if race.heat_no else str(race.race_no))
        key_lbl.setProperty("mono", True)
        key_lbl.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:22px;"
                              f" color:{styles.TEXT_PRIMARY};")
        root.addWidget(key_lbl)
        note = QLabel("Race and heat number are the key and cannot be edited here.")
        note.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        root.addWidget(note)

        name_cap = _caption("Name")
        root.addWidget(name_cap)
        self.name = _field(race.name)
        self.name.setFocus()
        self.name.selectAll()
        root.addWidget(self.name)
        orig = QLabel(f"In the file: {race.name}")
        orig.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:14px;")
        orig.setWordWrap(True)
        root.addWidget(orig)

        self._recorded = race.key in recorded
        self.amber = QWidget()
        av = QVBoxLayout(self.amber)
        av.setContentsMargins(18, 14, 18, 14)
        av.setSpacing(6)
        av.addWidget(QLabel("This race is already recorded."))
        av.addWidget(QLabel("The stored result keeps the name it was recorded "
                            "under. Also update the recorded race?"))
        for lbl in (av.itemAt(0).widget(), av.itemAt(1).widget()):
            lbl.setStyleSheet(f"color:{styles.AMBER_TEXT if lbl is av.itemAt(0).widget() else styles.TEXT_SECONDARY};"
                              f" font-size:{16 if lbl is av.itemAt(0).widget() else 15}px;")
        self.amber.setStyleSheet(f"background:#1e1608; border-left:3px solid {styles.AMBER};")
        self.amber.setVisible(self._recorded)
        root.addWidget(self.amber)

        row = QHBoxLayout()
        row.addWidget(_primary_button("Save correction", self._save))
        if self._recorded:
            row.addWidget(_muted_button("Also update recorded race",
                                        self._save_and_recorded))
        row.addStretch(1)
        esc = QLabel("Esc cancel")
        esc.setProperty("mono", True)
        esc.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:13px;"
                          f" color:{styles.TEXT_FAINT};")
        row.addWidget(esc)
        root.addLayout(row)

    def _save(self):
        new = self.name.text().strip()
        if not new:
            self._warn("Name cannot be empty.")
            return
        try:
            result, _ = rename_race(self.csv_path, self.race.key, new,
                                    expected=self.expected)
        except RosterWriteError as exc:
            self._warn(str(exc))
            return
        self._log("rename", key=str(self.race.key), before=self.race.name,
                  after=new)
        self._emit_result(result)

    def _save_and_recorded(self):
        new = self.name.text().strip()
        if not new:
            self._warn("Name cannot be empty.")
            return
        try:
            result, _ = rename_race(self.csv_path, self.race.key, new,
                                    expected=self.expected)
        except RosterWriteError as exc:
            self._warn(str(exc))
            return
        self.storage.rename_races(self.race.key, new)
        self._log("rename", key=str(self.race.key), before=self.race.name,
                  after=new, also_recorded=True)
        self._emit_result(result)


class IdentifyDialog(_BaseDialog):
    """Identify an unlisted race (F8): set number/heat/name once. Optionally also
    write the row to the roster."""

    def __init__(self, csv_path, race_id, provisional, storage, expected=None,
                 logger=None, parent=None):
        super().__init__(csv_path, expected, "Identify race", logger, parent)
        self.race_id = race_id
        self.storage = storage
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)
        root.addWidget(_caption("Identify race"))
        pv = QLabel(provisional)
        pv.setProperty("mono", True)
        pv.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:20px;"
                         f" color:{styles.TEXT_SECONDARY};")
        root.addWidget(pv)

        fields = QHBoxLayout()
        fields.setSpacing(16)
        self.rn = _field()
        self.hn = _field()
        self.name = _field()
        self.rn.setFocus()
        for w, cap in ((self.rn, "Race no."), (self.hn, "Heat"), (self.name, "Name")):
            col = QVBoxLayout()
            col.setSpacing(8)
            col.addWidget(_caption(cap))
            col.addWidget(w)
            fields.addLayout(col)
        root.addLayout(fields)

        row = QHBoxLayout()
        row.addWidget(_primary_button("Identify", self._identify))
        row.addWidget(_muted_button("Identify and add to roster",
                                    self._identify_and_add))
        root.addLayout(row)

    def _fields(self):
        return (self.rn.text().strip(), self.hn.text().strip(),
                self.name.text().strip())

    def _apply(self, write_roster: bool):
        rn, hn, name = self._fields()
        if not rn or not name:
            self._warn("Race number and name are required.")
            return
        row = self.storage.get_race(self.race_id)
        if row and (row["race_no"] or ""):
            self._warn("This race has already been identified.")
            return
        if write_roster:
            # Check the roster key BEFORE committing the DB identify, so an
            # identify-into-an-existing-key never lands the race on a colliding
            # number (BEHAVIOUR §8). On collision the race stays un-identified.
            try:
                result, outcome = add_row(self.csv_path, rn, hn, name,
                                          expected=self.expected)
            except RosterWriteError as exc:
                self._warn(str(exc))
                return
            if outcome == "collision":
                self._warn(f"{rn}-H{hn if hn else ''} is already in the roster — "
                           "the race was left un-identified.")
                return
            self.storage.identify_race(self.race_id, rn, hn, name)
            self._log("identify_add", race_no=rn, heat_no=hn, name=name)
            self._emit_result(result)
        else:
            self.storage.identify_race(self.race_id, rn, hn, name)
            self._log("identify", race_no=rn, heat_no=hn, name=name)
            self.accept()

    def _identify(self):
        self._apply(False)

    def _identify_and_add(self):
        self._apply(True)


class MergeDialog(_BaseDialog):
    """Merge two duplicate roster rows (F9). Offered only when exactly one of the
    two has no captures; re-points the recorded race at the kept row."""

    def __init__(self, csv_path, keep, remove, storage, expected=None,
                 recorded_count: int = 0, logger=None, parent=None):
        super().__init__(csv_path, expected, "Merge duplicate rows", logger,
                         parent)
        self.keep = keep
        self.remove = remove
        self.storage = storage
        self.recorded_count = recorded_count
        self.setMinimumWidth(600)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(18)
        root.addWidget(_caption("Two roster rows, one race", styles.TEXT_DIM))
        sub = QLabel("Keep one; the recorded race is re-pointed at it.")
        sub.setStyleSheet(f"color:{styles.TEXT_FAINT}; font-size:15px;")
        root.addWidget(sub)
        root.addWidget(self._option_block(keep, True))
        root.addWidget(self._option_block(remove, False))

        row = QHBoxLayout()
        row.addWidget(_primary_button("Merge", self._merge))
        row.addWidget(_muted_button("Keep both", self.reject))
        root.addLayout(row)

    def _option_block(self, race, is_keep):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(4)
        name = QLabel(race.display)
        name.setProperty("mono", True)
        name.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:18px;"
                           f" color:{styles.TEXT_PRIMARY if is_keep else styles.TEXT_SECONDARY};")
        lay.addWidget(name)
        tag = QLabel("keep" if is_keep else "remove")
        tag.setProperty("mono", True)
        tag.setStyleSheet(f"font-family:'{styles.FONT_MONO}'; font-size:13px;"
                          f" color:{styles.GREEN_TEXT if is_keep else styles.TEXT_FAINT};")
        lay.addWidget(tag)
        w.setStyleSheet(
            f"background:{styles.BG}; border:1px solid"
            f" {styles.GREEN if is_keep else styles.PANEL_BORDER};"
            f" border-left:3px solid {styles.GREEN if is_keep else styles.PANEL_BORDER};")
        return w

    def _merge(self):
        # CSV first, then DB (BEHAVIOUR §8). Remove the specific losing row
        # (the two rows share a normalised key, so removal must match literals);
        # re-point the single recorded race (if any) to the kept row's
        # formatting. No DB write when neither row is recorded.
        from ..races import remove_row_exact
        try:
            result = remove_row_exact(
                self.csv_path, self.remove.race_no, self.remove.heat_no,
                self.remove.name, expected=self.expected)
        except RosterWriteError as exc:
            self._warn(str(exc))
            return
        if self.recorded_count == 1:
            rows = [r for r in self.storage.all_races()
                    if race_key(r["race_no"], r["heat_no"], r["name"]) == self.keep.key]
            if rows:
                self.storage.repoint_race(rows[0]["id"], self.keep.race_no,
                                          self.keep.heat_no)
        self._log("merge", key=str(self.keep.key), keep=self.keep.name,
                  remove=self.remove.name)
        self._emit_result(result)
