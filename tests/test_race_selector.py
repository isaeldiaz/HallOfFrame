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

    def _add_race(self, name, i):
        self.storage.create_race(
            name, t0_monotonic=1000.0 + i, t0_wall=1000.0 + i,
            start_mode="direct", radio_delay_ms=0.0, delta_used=0.0,
            viewing_mode="screen")

    def test_empty_returns_empty_set(self):
        self.assertEqual(self.storage.race_names(), set())

    def test_distinct_names(self):
        self._add_race("Heat 1", 1)
        self._add_race("Heat 1", 2)  # overwrite: same name twice
        self._add_race("Heat 2", 3)
        self.assertEqual(self.storage.race_names(), {"Heat 1", "Heat 2"})


@unittest.skipUnless(_QT, "PySide6 unavailable")
class TestReadyScreenRaceSelector(unittest.TestCase):
    def setUp(self):
        self.rs = ReadyScreen(FrameBuffer(assumed_fps=30))
        self.names = ["Heat 1", "Heat 2", "Heat 3", "Heat 4"]

    def test_default_skips_recorded_to_next_open(self):
        self.rs.set_races(self.names, recorded={"Heat 1", "Heat 3"})
        self.assertEqual(self.rs.current_race_name(), "Heat 2")

    def test_all_recorded_wraps_to_first(self):
        self.rs.set_races(self.names, recorded=set(self.names))
        self.assertEqual(self.rs.current_race_name(), "Heat 1")

    def test_none_recorded_defaults_to_first(self):
        self.rs.set_races(self.names, recorded=set())
        self.assertEqual(self.rs.current_race_name(), "Heat 1")

    def test_recorded_items_grayed_still_selectable(self):
        self.rs.set_races(self.names, recorded={"Heat 1", "Heat 3"})
        for i, name in enumerate(self.names):
            fg = self.rs.race_combo.itemData(i, Qt.ItemDataRole.ForegroundRole)
            if name in {"Heat 1", "Heat 3"}:
                self.assertIsNotNone(fg, f"{name} should be grayed")
            else:
                self.assertIsNone(fg, f"{name} should keep default color")
        # Overwrite: a recorded race can still be selected.
        self.rs.race_combo.setCurrentIndex(0)
        self.assertEqual(self.rs.current_race_name(), "Heat 1")

    def test_next_race_skips_recorded(self):
        self.rs.set_races(self.names, recorded={"Heat 1", "Heat 3"})
        # default lands on first unrecorded (Heat 2)
        self.assertEqual(self.rs.current_race_name(), "Heat 2")
        self.rs.next_race()  # skip recorded Heat 3
        self.assertEqual(self.rs.current_race_name(), "Heat 4")
        self.rs.next_race()  # wrap: skip Heat 1, land back on Heat 2
        self.assertEqual(self.rs.current_race_name(), "Heat 2")

    def test_prev_race_skips_recorded(self):
        self.rs.set_races(self.names, recorded={"Heat 1", "Heat 3"})
        self.rs.select_first_unrecorded()  # Heat 2
        self.rs.prev_race()  # wrap: skip Heat 1 and Heat 3, land on Heat 4
        self.assertEqual(self.rs.current_race_name(), "Heat 4")

    def test_next_race_all_recorded_falls_back(self):
        self.rs.set_races(self.names, recorded=set(self.names))
        self.rs.next_race()
        self.assertEqual(self.rs.current_race_name(), "Heat 1")

    def test_refresh_recorded_preserves_selection(self):
        self.rs.set_races(self.names, recorded={"Heat 1"})
        self.rs.race_combo.setCurrentIndex(2)  # Heat 3
        self.rs.refresh_recorded({"Heat 1", "Heat 3"})
        self.assertEqual(self.rs.current_race_name(), "Heat 3")
        # Heat 3 is now grayed, Heat 2 still open
        self.assertIsNotNone(
            self.rs.race_combo.itemData(2, Qt.ItemDataRole.ForegroundRole))
        self.assertIsNone(
            self.rs.race_combo.itemData(1, Qt.ItemDataRole.ForegroundRole))

    def test_delegate_grayed_rendering(self):
        # The combo must carry a foreground delegate so recorded items actually
        # render dimmer than open ones despite the stylesheet color.
        self.rs.set_races(self.names, recorded={"Heat 1", "Heat 3"})
        dlg = self.rs.race_combo.view().itemDelegate()
        from PySide6.QtWidgets import QStyleOptionViewItem
        for i, name in enumerate(self.names):
            opt = QStyleOptionViewItem()
            dlg.initStyleOption(opt, self.rs.race_combo.model().index(i, 0))
            c = opt.palette.color(opt.palette.ColorRole.Text)
            if name in {"Heat 1", "Heat 3"}:
                self.assertLessEqual(c.lightness(), 110, f"{name} should be dim")
            else:
                self.assertGreater(c.lightness(), 180, f"{name} should be bright")


if __name__ == "__main__":
    unittest.main()
