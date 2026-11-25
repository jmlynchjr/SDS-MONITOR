# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.103: the Incidents window keeps its list usable: tabs below it, and a short unit list above them."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import mon, monitor
from test_incident_fixes import DAY, make_day, title


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_day()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(mon, "INCIDENT_EDITS_FILE", Path(self.tmp.name) / "incident_edits.json")
        p.start()
        self.addCleanup(p.stop)

    def test_short_list(self):
        self.assertEqual(mon.short_list(["A", "B"], 6), "A, B")
        self.assertEqual(mon.short_list([str(i) for i in range(10)], 6), "0, 1, 2, 3, 4, 5 +4 more")
        self.assertEqual(mon.short_list([], 6), "")

    def test_many_units(self):
        w = monitor(fresh=True)
        w.unit_title = title
        win = mon.IncidentsWindow(w)
        win.reload()
        win.days.setCurrentText(DAY)
        win.recompute()
        fire = sorted(win.incidents, key=lambda i: i["start"])[0]
        with mock.patch.object(mon, "INCIDENT_UNITS_SHOWN", 1):
            win.current = lambda: fire
            win.show_incident()
        self.assertIn("+1 more", win.detail.text())
        self.assertNotIn("+1 more", win.detail_text)
        self.assertIn("Ladder 3", win.detail_text)          # the export keeps every unit
        self.assertIn("Units: Engine 12, Ladder 3", win.story.text())
        win.current = lambda: None
        win.show_incident()
        self.assertEqual(win.detail_text, "")
        win.geometry_restored = True
        win.save_settings(w.settings)                        # saves the splitter too


if __name__ == "__main__":
    unittest.main()
