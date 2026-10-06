"""Roster editing dialogs (mockups F4/F6; plan step 2.6).

Modal ``QDialog``s launched from Ready (add / rename) and Review (edit race).
Writes go through the single atomic path in :mod:`hallofframe.roster`; the fresh
``RosterLoad`` is emitted on ``result_applied``. Nothing here is reachable while
armed or recording — the caller enforces that (spec §7.5).
"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout,
                               QLineEdit, QMessageBox, QPushButton, QVBoxLayout)
from ..roster import RosterWriteError, add_row, race_key, rename_race, rename_races
def _button(text, callback):
    b = QPushButton(text)  # §4: NoFocus — Space/Return must never activate it.
    b.setFocusPolicy(Qt.NoFocus)
    b.clicked.connect(callback)
    return b

class _BaseDialog(QDialog):
    """Enter fires the primary action despite NoFocus buttons (§4)."""
    result_applied = Signal(object)
    _primary = None
    def __init__(self, csv_path, expected=None, title="", logger=None, parent=None):
        super().__init__(parent)
        self.csv_path, self.expected, self.logger = csv_path, expected, logger
        self.setWindowTitle(title); self.setModal(True)
    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._primary:
            self._primary()
            event.accept()
            return
        super().keyPressEvent(event)
    def _done(self, result, action, **fields):
        if self.logger is not None:  # BEHAVIOUR §10: one audit line per mutation
            self.logger.info("roster", action, file=self.csv_path, **fields)
        if result is not None:
            self.result_applied.emit(result)
        self.accept()

class AddRaceDialog(_BaseDialog):
    """Add a race/heat (F4); inserted after the selected row — file order, never sorted."""
    def __init__(self, csv_path, race_no="", heat_no="", name="", expected=None,
                 logger=None, parent=None, after_key=None):
        super().__init__(csv_path, expected, "Add race", logger, parent)
        self.rn, self.hn, self.name = QLineEdit(race_no), QLineEdit(heat_no), QLineEdit(name)
        self.name.setFocus(); self.after_key = after_key
        form = QFormLayout()
        for cap, w in (("Race no.", self.rn), ("Heat", self.hn), ("Name", self.name)):
            form.addRow(cap, w)
        row = QHBoxLayout()
        row.addWidget(_button("Add race", self._add))
        row.addWidget(_button("Cancel", self.reject))
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(row)
        self._primary = self._add
    def _add(self):
        rn, hn, name = (self.rn.text().strip(), self.hn.text().strip(),
                        self.name.text().strip())
        if not rn or not name:
            QMessageBox.warning(self, "Roster", "Race number and name are required.")
            return
        try:
            result, outcome = add_row(self.csv_path, rn, hn, name,
                                      after_key=self.after_key, expected=self.expected)
        except RosterWriteError as exc:
            QMessageBox.warning(self, "Roster", str(exc))
            return
        if outcome == "collision":
            QMessageBox.warning(self, "Roster", "Already in the roster.")
            return
        self._done(result, "add", race_no=rn, heat_no=hn, name=name)

class RenameDialog(_BaseDialog):
    """Correct a race name (F6) or, with *editable_numbers*, identify an unlisted
    race (step 2.6): writes ``storage.identify_race`` and, when ticked, appends the
    row with ``roster.add``. Numbers are the key and read-only for a plain rename."""
    def __init__(self, csv_path, race_no, heat_no, name, recorded, storage,
                 expected=None, logger=None, parent=None, *, editable_numbers=False,
                 race_id=None, roster=None):
        super().__init__(csv_path, expected, "Edit race", logger, parent)
        self.storage, self.race_id, self.roster = storage, race_id, roster; self.editable_numbers = editable_numbers
        self.key = race_key(race_no, heat_no, name)
        self.rn, self.hn, self.name = QLineEdit(race_no), QLineEdit(heat_no), QLineEdit(name)
        for w in (self.rn, self.hn): w.setReadOnly(not editable_numbers)
        self.name.setFocus()
        form = QFormLayout()
        for cap, w in (("Race no.", self.rn), ("Heat", self.hn), ("Name", self.name)):
            form.addRow(cap, w)
        self.add_to_roster = QCheckBox("Also add to roster")
        self.add_to_roster.setVisible(bool(editable_numbers and roster and roster.path and roster.result.ok))
        self._recorded = (not editable_numbers) and self.key in recorded
        self.amber = QCheckBox("This race is already recorded — also update it?"); self.amber.setVisible(self._recorded)
        row = QHBoxLayout()
        row.addWidget(_button("Save", self._save)); row.addWidget(_button("Cancel", self.reject))
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self.add_to_roster)
        root.addWidget(self.amber)
        root.addLayout(row)
        self._primary = self._save
    def _save(self):
        rn, hn, name = (self.rn.text().strip(), self.hn.text().strip(),
                        self.name.text().strip())
        if self.editable_numbers:
            if not rn or not name:
                QMessageBox.warning(self, "Roster", "Race number and name are required.")
                return
            row = self.storage.get_race(self.race_id)
            if row and (row["race_no"] or ""):
                QMessageBox.warning(self, "Roster", "This race has already been identified.")
                return
            if self.add_to_roster.isChecked():
                try:
                    result, outcome = self.roster.add(rn, hn, name)
                except RosterWriteError as exc:
                    QMessageBox.warning(self, "Roster", str(exc))
                    return
                if outcome == "collision":
                    QMessageBox.warning(self, "Roster",
                                        "Already in the roster — race left un-identified.")
                    return
                self.storage.identify_race(self.race_id, rn, hn, name)
                self._done(result, "identify_add", race_no=rn, heat_no=hn, name=name)
                return
            self.storage.identify_race(self.race_id, rn, hn, name)
            self._done(None, "identify", race_no=rn, heat_no=hn, name=name)
            return
        if not name:
            QMessageBox.warning(self, "Roster", "Name cannot be empty.")
            return
        try:
            result, _ = rename_race(self.csv_path, self.key, name, expected=self.expected)
        except RosterWriteError as exc:
            QMessageBox.warning(self, "Roster", str(exc))
            return
        if self.amber.isChecked():
            rename_races(self.storage, self.key, name)
        self._done(result, "rename", key=str(self.key), after=name)
