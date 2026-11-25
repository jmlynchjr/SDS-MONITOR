# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.102: the incident accuracy report, worked out from your own fixes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import mon, monitor
from test_incident_fixes import DAY, make_day, incidents

DAY2 = "2026-10-22"


class AccuracyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_day()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "incident_edits.json"
        p = mock.patch.object(mon, "INCIDENT_EDITS_FILE", self.file)
        p.start()
        self.addCleanup(p.stop)

    def save(self, edits, day=DAY):
        mon.save_incident_edits(day, edits)

    def test_no_fixes(self):
        t = mon.incident_accuracy()
        self.assertEqual(t["days"], [])
        self.assertIn("No fixes yet", mon.incident_accuracy_text(t))
        self.assertIsNone(mon.incident_accuracy_day(DAY, edits=mon.empty_incident_edits()))

    def test_pairs(self):
        found = [{"ids": ["a", "b", "c"], "hidden": False}, {"ids": ["d", "e"], "hidden": True}]
        self.assertEqual(len(mon.incident_pairs(found)), 3)

    def test_split_means_run_together(self):
        fire = incidents()[0]
        edits = mon.empty_incident_edits()
        edits["splits"].append(fire["ids"][2])           # the fireground part becomes its own incident
        self.save(edits)
        t = mon.incident_accuracy()
        self.assertEqual(t["days"], [DAY])
        self.assertEqual(t["split"], 1)
        self.assertEqual(t["found"], 1.0)                # nothing had to be merged
        self.assertLess(t["kept"], 1.0)
        self.assertIn("run together", mon.accuracy_verdict(t))
        self.assertEqual(t["channels"]["Fireground 2"]["split"], 1)
        text = mon.incident_accuracy_text(t)
        self.assertIn("1 splits", text)
        self.assertIn("Fireground 2: 0 / 1 / 0 / 0", text)

    def test_merge_means_broken_up(self):
        fire, ems = incidents()
        edits = mon.empty_incident_edits()
        edits["joins"].append([fire["ids"][0], ems["ids"][0]])
        self.save(edits)
        t = mon.incident_accuracy()
        self.assertEqual(t["kept"], 1.0)
        self.assertLess(t["found"], 1.0)
        self.assertIn("broken up", mon.accuracy_verdict(t))

    def test_hidden_and_types(self):
        fire, ems = incidents()
        edits = mon.empty_incident_edits()
        edits["hidden"].append(ems["ids"][0])
        edits["types"][fire["ids"][0]] = "hazard"
        self.save(edits)
        t = mon.incident_accuracy()
        self.assertEqual(t["hidden"], 1)
        self.assertIn("weren't real", mon.accuracy_verdict(t))
        self.assertEqual(sum(t["types"].values()), 1)
        (auto, yours), = t["types"]
        self.assertEqual(yours, "hazard")
        self.assertIn("→ " + mon.INCIDENT_TYPE_LABELS["hazard"], mon.incident_accuracy_text(t))

    def test_period_and_missing_days(self):
        fire = incidents()[0]
        edits = mon.empty_incident_edits()
        edits["splits"].append(fire["ids"][2])
        self.save(edits)
        self.save(edits, day=DAY2)                       # a day with no calls left on record
        self.assertEqual(mon.incident_edit_days(), [DAY, DAY2])
        self.assertEqual(mon.incident_accuracy()["days"], [DAY])
        self.assertEqual(mon.incident_accuracy(first=DAY2)["days"], [])

    def test_bad_file(self):
        self.file.write_text("not json")
        self.assertEqual(mon.incident_edit_days(), [])
        self.file.write_text(json.dumps(["x"]))
        self.assertEqual(mon.incident_edit_days(), [])

    def test_window(self):
        w = mon.IncidentAccuracyWindow(monitor())
        self.assertIn("Incident accuracy", w.text)
        fire = incidents()[0]
        edits = mon.empty_incident_edits()
        edits["splits"].append(fire["ids"][2])
        self.save(edits)
        w.refresh()
        self.assertIn("Kept together", w.text)


if __name__ == "__main__":
    unittest.main()
