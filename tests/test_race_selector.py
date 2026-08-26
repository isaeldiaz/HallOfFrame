"""Race selector (Ready screen) + Storage.race_keys: recorded races are grayed
out, still selectable to overwrite, and the default jumps to the next
not-yet-recorded race."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from hallofframe.framebuffer import FrameBuffer
from hallofframe.races import RaceInfo
from hallofframe.storage import Storage
from hallofframe.ui.ready_screen import ReadyScreen

try:
    _QT = QApplication.instance() is not None or bool(QApplication([]))
except Exception:  # pragma: no cover
    _QT = False


class TestStorageRaceNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name))

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def _add_race(self, name, i, race_no="", heat_no=""):
        self.storage.create_race(
            name, t0_monotonic=1000.0 + i, t0_wall=1000.0 + i,
            start_mode="direct", radio_delay_ms=0.0, delta_used=0.0,
            viewing_mode="screen", race_no=race_no, heat_no=heat_no)

    def test_empty_returns_empty_set(self):
        self.assertEqual(self.storage.race_keys(), set())

    def test_distinct_names(self):
        self._add_race("Final", 1, race_no="101", heat_no="1")
        self._add_race("Final", 2, race_no="101", heat_no="1")  # overwrite
        self._add_race("Heat", 3, race_no="102", heat_no="1")
        self.assertEqual(
            self.storage.race_keys(),
            {("num", "101", "1"), ("num", "102", "1")})

    def test_legacy_rows_key_on_name(self):
        self._add_race("Heat 1", 1)  # no race_no/heat_no
        self.assertEqual(self.storage.race_keys(), {("name", "heat 1", "")})

    def test_identify_race_once_then_blocked(self):
        rid = self.storage.create_race(
            "Race-20260829-134812", t0_monotonic=1000.0, t0_wall=1000.0,
            start_mode="direct", radio_delay_ms=0.0, delta_used=0.0,
            viewing_mode="screen")  # no race_no/heat_no -> unlisted
        self.assertTrue(self.storage.identify_race(rid, "124", "1", "D U17 2x"))
        row = self.storage.get_race(rid)
        self.assertEqual((row["race_no"], row["heat_no"], row["name"]),
                         ("124", "1", "D U17 2x"))
        # exactly once
        self.assertFalse(self.storage.identify_race(rid, "999", "2", "X"))

    def test_repoint_race_restyles_key_only(self):
        rid = self.storage.create_race(
            "X", t0_monotonic=1000.0, t0_wall=1000.0, start_mode="direct",
            radio_delay_ms=0.0, delta_used=0.0, viewing_mode="screen",
            race_no="0102", heat_no="1")
        self.storage.repoint_race(rid, "102", "1")   # restyle, same key
        self.assertEqual(self.storage.get_race(rid)["race_no"], "102")
        with self.assertRaises(ValueError):
            self.storage.repoint_race(rid, "999", "1")  # normalised-key change

    def test_rename_races_updates_matching_rows(self):
        self._add_race("Final", 1, race_no="101", heat_no="1")
        n = self.storage.rename_races(("num", "101", "1"), "Renamed")
        self.assertEqual(n, 1)
        row = self.storage.get_race(1)
        self.assertEqual(row["name"], "Renamed")


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestReadyScreenRaceSelector(unittest.TestCase):
    def setUp(self):
        self.rs = ReadyScreen(FrameBuffer(assumed_fps=30))
        self.races = [
            RaceInfo("101", "1", "Final A"),
            RaceInfo("102", "1", "Final B"),
            RaceInfo("103", "1", "Heat"),
            RaceInfo("104", "1", "Repechage"),
        ]
        self.rec1 = ("num", "101", "1")   # recorded (grayed)
        self.rec3 = ("num", "103", "1")

    def test_display_string_format(self):
        self.rs.set_races(self.races, recorded=set())
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

    def test_default_skips_recorded_to_next_open(self):
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        self.assertEqual(self.rs.current_race_name(), "102-H1 - Final B")

    def test_all_recorded_lands_on_unlisted(self):
        self.rs.set_races(self.races,
                          recorded={r.key for r in self.races})
        self.assertTrue(self.rs.selected_is_unlisted())

    def test_none_recorded_defaults_to_first(self):
        self.rs.set_races(self.races, recorded=set())
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

    def test_recorded_items_grayed_still_selectable(self):
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        for i, race in enumerate(self.races):
            key = race.key
            fg = self.rs.race_combo.itemData(i, Qt.ItemDataRole.ForegroundRole)
            if key in {self.rec1, self.rec3}:
                self.assertIsNotNone(fg, f"{key} should be grayed")
            else:
                self.assertIsNone(fg, f"{key} should keep default color")
        # Overwrite: a recorded race can still be selected.
        self.rs.race_combo.setCurrentIndex(0)
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

    def test_next_race_skips_recorded(self):
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        # default lands on first unrecorded (102)
        self.assertEqual(self.rs.current_race_name(), "102-H1 - Final B")
        self.rs.next_race()  # skip recorded 103
        self.assertEqual(self.rs.current_race_name(), "104-H1 - Repechage")
        self.rs.next_race()  # wrap: skip 101, land back on 102
        self.assertEqual(self.rs.current_race_name(), "102-H1 - Final B")

    def test_prev_race_skips_recorded(self):
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        self.rs.select_first_unrecorded()  # 102
        self.rs.prev_race()  # wrap: skip 101 and 103, land on 104
        self.assertEqual(self.rs.current_race_name(), "104-H1 - Repechage")

    def test_next_race_all_recorded_falls_back_to_unlisted(self):
        self.rs.set_races(self.races,
                          recorded={r.key for r in self.races})
        self.rs.next_race()
        self.assertTrue(self.rs.selected_is_unlisted())

    def test_refresh_recorded_preserves_selection(self):
        self.rs.set_races(self.races, recorded={self.rec1})
        self.rs.race_combo.setCurrentIndex(2)  # 103
        self.rs.refresh_recorded({self.rec1, self.rec3})
        self.assertEqual(self.rs.current_race_name(), "103-H1 - Heat")
        # 103 is now grayed, 102 still open
        self.assertIsNotNone(
            self.rs.race_combo.itemData(2, Qt.ItemDataRole.ForegroundRole))
        self.assertIsNone(
            self.rs.race_combo.itemData(1, Qt.ItemDataRole.ForegroundRole))

    def _filter_races(self, races):
        self.rs.set_races(races, recorded=set())
        self.rs.begin_filter()
        return self.rs

    def test_filter_shows_whole_heat_group(self):
        self._filter_races([
            RaceInfo("217", "3", "H U15 1x"),
            RaceInfo("217", "4", "H U15 1x"),
            RaceInfo("227", "1", "D U15 2x"),
            RaceInfo("271", "1", "D U17 4x"),
        ])
        self.rs.filter_edit.setText("217")
        race_rows = [r for r in self.rs._rows if r.kind == "race"]
        self.assertEqual([r.race.display for r in race_rows],
                         ["217-H3 - H U15 1x", "217-H4 - H U15 1x"])

    def test_filter_no_exact_match_shows_create_row(self):
        self._filter_races([
            RaceInfo("217", "3", "H U15 1x"),
            RaceInfo("227", "1", "D U15 2x"),
        ])
        self.rs.filter_edit.setText("271")
        kinds = [r.kind for r in self.rs._rows]
        self.assertIn("create", kinds)
        create = next(r for r in self.rs._rows if r.kind == "create")
        self.assertEqual(create.text, "Add race 271 to the roster…")
        self.assertEqual((create.create_race_no, create.create_heat_no),
                         ("271", ""))

    def test_filter_non_numeric_has_no_create_row(self):
        self._filter_races([
            RaceInfo("217", "3", "H U15 1x"),
            RaceInfo("227", "1", "D U15 2x"),
        ])
        self.rs.filter_edit.setText("senior")
        self.assertNotIn("create", [r.kind for r in self.rs._rows])
        self.assertTrue(any(r.kind == "unlisted" for r in self.rs._rows))

    def test_filter_one_char_has_no_create_row(self):
        self._filter_races([RaceInfo("217", "3", "H U15 1x")])
        self.rs.filter_edit.setText("2")
        self.assertNotIn("create", [r.kind for r in self.rs._rows])

    def test_down_reaches_create_row_but_up_does_not(self):
        self._filter_races([RaceInfo("217", "3", "H U15 1x")])
        self.rs.filter_edit.setText("271")   # no exact match -> create row present
        # default lands on the suggestion (217); a deliberate ↓ reaches create.
        self.rs.next_race()
        self.assertEqual(self.rs.current_row().kind, "create")
        # but ↑ never lands on it (goes back to the suggestion).
        self.rs.prev_race()
        self.assertEqual(self.rs.current_row().kind, "race")

    def test_clear_filter_restores_selection(self):
        self._filter_races([
            RaceInfo("217", "3", "H U15 1x"),
            RaceInfo("227", "1", "D U15 2x"),
        ])
        self.assertEqual(self.rs.current_race_name(), "217-H3 - H U15 1x")
        self.rs.filter_edit.setText("227")
        self.assertEqual(self.rs.current_race_name(), "227-H1 - D U15 2x")
        self.rs.clear_filter()
        self.assertEqual(self.rs.current_race_name(), "217-H3 - H U15 1x")

    def test_roster_chip_text(self):
        self.rs.set_races(self.races, recorded=set())
        self.rs.set_roster("lørdag-29aug.csv", 40, "08:12")
        self.assertEqual(self.rs.roster_chip.text(),
                         "lørdag-29aug.csv · 40 races · 08:12")
        self.assertFalse(self.rs.roster_chip.isHidden())
        self.assertTrue(self.rs.roster_dup.isHidden())

    def test_roster_chip_duplicates(self):
        self.rs.set_races(self.races, recorded=set())
        self.rs.set_roster("races.csv", 40, "08:12", duplicates=2)
        self.assertEqual(self.rs.roster_dup.text(), "2 possible duplicates")
        self.assertFalse(self.rs.roster_dup.isHidden())
        # a successful load clears the duplicate chip
        self.rs.set_roster("races.csv", 40, "08:13", duplicates=0)
        self.assertTrue(self.rs.roster_dup.isHidden())

    def test_delegate_grayed_rendering(self):
        # The combo must carry a foreground delegate so recorded items actually
        # render dimmer than open ones despite the stylesheet color.
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        dlg = self.rs.race_combo.view().itemDelegate()
        from PySide6.QtWidgets import QStyleOptionViewItem
        for i, race in enumerate(self.races):
            key = race.key
            opt = QStyleOptionViewItem()
            dlg.initStyleOption(opt, self.rs.race_combo.model().index(i, 0))
            c = opt.palette.color(opt.palette.ColorRole.Text)
            if key in {self.rec1, self.rec3}:
                self.assertLessEqual(c.lightness(), 110, f"{key} should be dim")
            else:
                self.assertGreater(c.lightness(), 180, f"{key} should be bright")


if __name__ == "__main__":
    unittest.main()
