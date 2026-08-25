"""Race selector (Ready screen) + Storage.race_names: recorded races are grayed
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
        self.assertEqual(self.storage.race_names(), set())

    def test_distinct_names(self):
        self._add_race("Final", 1, race_no="101", heat_no="1")
        self._add_race("Final", 2, race_no="101", heat_no="1")  # overwrite
        self._add_race("Heat", 3, race_no="102", heat_no="1")
        self.assertEqual(
            self.storage.race_names(),
            {("101", "1", "Final"), ("102", "1", "Heat")})

    def test_legacy_rows_report_empty_fields(self):
        self._add_race("Heat 1", 1)  # no race_no/heat_no
        self.assertEqual(self.storage.race_names(), {("", "", "Heat 1")})


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
        self.rec1 = ("101", "1", "Final A")   # recorded (grayed)
        self.rec3 = ("103", "1", "Heat")

    def test_display_string_format(self):
        self.rs.set_races(self.races, recorded=set())
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

    def test_default_skips_recorded_to_next_open(self):
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        self.assertEqual(self.rs.current_race_name(), "102-H1 - Final B")

    def test_all_recorded_wraps_to_first(self):
        self.rs.set_races(self.races,
                          recorded={r.key for r in self.races})
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

    def test_none_recorded_defaults_to_first(self):
        self.rs.set_races(self.races, recorded=set())
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

    def test_recorded_items_grayed_still_selectable(self):
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        for i, race in enumerate(self.races):
            key = (race.race_no, race.heat_no, race.name)
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

    def test_next_race_all_recorded_falls_back(self):
        self.rs.set_races(self.races,
                          recorded={r.key for r in self.races})
        self.rs.next_race()
        self.assertEqual(self.rs.current_race_name(), "101-H1 - Final A")

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

    def test_delegate_grayed_rendering(self):
        # The combo must carry a foreground delegate so recorded items actually
        # render dimmer than open ones despite the stylesheet color.
        self.rs.set_races(self.races, recorded={self.rec1, self.rec3})
        dlg = self.rs.race_combo.view().itemDelegate()
        from PySide6.QtWidgets import QStyleOptionViewItem
        for i, race in enumerate(self.races):
            key = (race.race_no, race.heat_no, race.name)
            opt = QStyleOptionViewItem()
            dlg.initStyleOption(opt, self.rs.race_combo.model().index(i, 0))
            c = opt.palette.color(opt.palette.ColorRole.Text)
            if key in {self.rec1, self.rec3}:
                self.assertLessEqual(c.lightness(), 110, f"{key} should be dim")
            else:
                self.assertGreater(c.lightness(), 180, f"{key} should be bright")


if __name__ == "__main__":
    unittest.main()
