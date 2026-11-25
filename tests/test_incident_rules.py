# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.106: two new joining rules — the same place joins calls, and a unit back in service leaves."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from support import mon
from test_incident_fixes import write_day

DAY = "2026-10-25"
T0 = datetime(2026, 10, 25, 14, 0, 0)
OFF = {"places": False, "clear": False}


def call(minute, channel, units, text="", second=0):
    start = T0 + timedelta(minutes=minute, seconds=second)
    return {"start": start, "end": start + timedelta(seconds=5), "duration": 5.0, "system": "Palmetto 800",
            "department": "Greenville County", "channel": channel, "frequency": "", "talkgroup": "",
            "units": list(units), "key": channel, "label": channel, "text": text,
            "row": [start.strftime("%Y-%m-%d %H:%M:%S"), "", "5", "Palmetto 800", "", channel],
            "source": mon.SOURCE_SCANNER, "parts": None}


def groups(incs):
    return sorted(sorted(r["channel"] + "@" + r["start"].strftime("%M") for r in i["members"]) for i in incs)


class ClearTests(unittest.TestCase):
    def recs(self, said="Engine 12 in service"):
        return [call(0, "Fire Dispatch", ["E12"], "Engine 12 respond structure fire"),
                call(3, "Fireground 2", ["E12", "L3"], "Engine 12 on scene"),
                call(6, "Fireground 2", ["E12"], said),
                call(12, "Fire Dispatch", ["E12", "M4"], "Engine 12 and Medic 4 respond to a fall"),
                call(15, "EMS 1", ["M4"], "Medic 4 on scene"),
                call(18, "EMS 1", ["M4", "E12"], "Medic 4 transporting")]

    def test_back_in_service_ends_the_unit_part(self):
        old = mon.build_incidents(self.recs(), set(), rules=OFF)
        self.assertEqual(len(old), 1)                           # everything chained through Engine 12
        new = mon.build_incidents(self.recs(), set(), rules={"places": False, "clear": True})
        self.assertEqual(len(new), 2)
        self.assertEqual(len(new[1]["members"]), 3)             # the fire: dispatch, on scene, in service

    def test_other_words_do_not_end_it(self):
        new = mon.build_incidents(self.recs("Engine 12 still working"), set(),
                                  rules={"places": False, "clear": True})
        self.assertEqual(len(new), 1)

    def test_ten_code(self):
        decode = lambda text, ctx: [("10-8", "10-8", "In service", "APCO")] if "10-8" in text else []
        new = mon.build_incidents(self.recs("Engine 12 10-8"), set(),
                                  rules={"places": False, "clear": True, "decode": decode})
        self.assertEqual(len(new), 2)

    def test_cleared_with_speakers(self):
        r = call(6, "Fireground 2", ["D1", "E12", "L3"], "x")
        r["parts"] = [{"unit": "E12", "at": 0, "text": "Engine 12 available"},
                      {"unit": "L3", "at": 2, "text": "Ladder 3 still on scene"}]
        self.assertEqual(mon.call_cleared(r, {"D1"}), {"E12"})
        r["parts"] = None
        r["text"] = "Engine 12 available"
        self.assertEqual(mon.call_cleared(r, {"D1"}), set())     # two field radios, can't tell who
        r["units"] = ["D1", "E12"]
        self.assertEqual(mon.call_cleared(r, {"D1"}), {"E12"})


class PlaceTests(unittest.TestCase):
    def recs(self, minute=20):
        return [call(0, "Fire Dispatch", ["E12"], "Engine 12 respond 100 N Main St smoke showing"),
                call(2, "Fire Dispatch", ["E12"], "Engine 12 copy"),
                call(4, "Fireground 2", ["E12"], "Engine 12 on scene"),
                call(minute, "Sheriff Dispatch", ["C40"], "Charlie 40 respond to 100 North Main Street traffic"),
                call(minute + 2, "Sheriff Dispatch", ["C40"], "Charlie 40 copy"),
                call(minute + 5, "Sheriff Dispatch", ["C40"], "Charlie 40 on scene")]

    def test_keys(self):
        self.assertEqual(mon.call_place_keys({"text": "respond 100 N Main St"}),
                         mon.call_place_keys({"text": "go to 100 North Main Street"}))
        self.assertEqual(mon.call_place_keys({"text": "Pleasantburg Drive and Wade Hampton Boulevard"}),
                         mon.call_place_keys({"text": "Wade Hampton Blvd at Pleasantburg Dr"}))
        self.assertEqual(mon.call_place_keys({"text": "Engine 12 and Medic 4"}), set())

    def test_same_place_joins(self):
        self.assertEqual(len(mon.build_incidents(self.recs(), set(), rules=OFF)), 2)
        joined = mon.build_incidents(self.recs(), set(), rules={"places": True, "clear": False})
        self.assertEqual(len(joined), 1)
        self.assertEqual(len(joined[0]["units"]), 2)

    def test_too_far_apart(self):
        far = mon.build_incidents(self.recs(minute=60), set(), rules={"places": True, "clear": False})
        self.assertEqual(len(far), 2)


class AccuracyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(mon, "INCIDENT_EDITS_FILE", Path(self.tmp.name) / "incident_edits.json")
        p.start()
        self.addCleanup(p.stop)
        t = lambda m: T0 + timedelta(minutes=m)
        write_day(DAY, [
            (t(0), 5, "Fire Dispatch", "44002", ["3100012"], "Engine 12 respond structure fire"),
            (t(3), 5, "Fireground 2", "44010", ["3100012"], "Engine 12 on scene"),
            (t(6), 5, "Fireground 2", "44010", ["3100012"], "Engine 12 in service"),
            (t(12), 5, "Fire Dispatch", "44002", ["3100012", "3100004"], "Engine 12 Medic 4 respond to a fall"),
            (t(15), 5, "EMS 1", "44020", ["3100004"], "Medic 4 on scene"),
            (t(18), 5, "EMS 1", "44020", ["3100004"], "Medic 4 transporting"),
        ])

    def test_rules_scored_on_your_split(self):
        old = mon.build_incidents(mon.receptions_with_units(DAY), set(), rules=OFF)
        self.assertEqual(len(old), 1)
        edits = mon.empty_incident_edits()
        edits["splits"].append(old[0]["ids"][3])          # you split the fall off the fire
        mon.save_incident_edits(DAY, edits)
        t = mon.incident_accuracy(rules={"places": True, "clear": True})
        rows = t["by_rules"]
        before = rows["Before 1.1.106 (radios and answers only)"]
        both = rows["Both (to begin with)"]
        self.assertLess(before[1] / before[0], 1.0)        # the old way ran them together
        self.assertEqual(both[1], both[0])                 # the new rules keep only pairs you kept
        self.assertEqual(t["kept"], 1.0)
        text = mon.incident_accuracy_text(t)
        self.assertIn("Joining rules compared on your fixes", text)
        self.assertIn("Both (to begin with): 100% / 100%", text)


if __name__ == "__main__":
    unittest.main()
