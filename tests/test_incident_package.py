# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""Per-unit timeline bars, the incident package and today's summary for the Plasma widget (v1.1.93).

Uses the made-up history of test_period_reports: on 2026-09-03 Engine 12 (3100001) goes en route and on
scene, and Medic 4 (3200004) goes on scene, on Fire Dispatch and Fireground 2.
"""
import csv
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from support import mon, monitor
from test_period_reports import make_day, make_history

DAY = "2026-09-03"


def the_incident(day=DAY):
    recs = mon.receptions_with_units(day)
    hubs = mon.find_hubs(recs, {})
    incs = [i for i in mon.build_incidents(recs, hubs, gap=600, min_calls=3) if not i["hidden"]]
    inc = max(incs, key=lambda i: len(i["units"]))
    inc["summary"] = mon.summarize_incident(inc, None, ())
    return inc


class UnitBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_history()
        cls.inc = the_incident()
        cls.phases = mon.unit_phases(cls.inc, cls.inc["summary"]["story"])

    def test_phases(self):
        by_unit = {p["unit"]: p for p in self.phases}
        self.assertEqual(set(by_unit), {"3100001", "3200004"})
        engine = [phase for _a, _b, phase in by_unit["3100001"]["segments"]]
        self.assertEqual(engine, ["heard", "en_route", "on_scene"])
        medic = by_unit["3200004"]
        self.assertEqual(medic["segments"][-1][2], "on_scene")
        self.assertEqual(len(medic["calls"]), 3)
        for p in self.phases:                                       # segments run in order without gaps
            for (a1, b1, _x), (a2, _b2, _y) in zip(p["segments"], p["segments"][1:]):
                self.assertEqual(b1, a2)
                self.assertLessEqual(a1, b1)

    def test_clear_ends_the_bar(self):
        inc = self.inc
        story = {"units": {"3100001": {"en_route": inc["start"] + timedelta(seconds=40),
                                       "clear": inc["start"] + timedelta(seconds=200)}}}
        p = [x for x in mon.unit_phases(inc, story) if x["unit"] == "3100001"][0]
        self.assertEqual(p["segments"][-1][1], inc["start"] + timedelta(seconds=200))
        self.assertEqual(p["clear"], inc["start"] + timedelta(seconds=200))
        html = mon.unit_bars_html(inc, [p])
        self.assertIn("back in service", html)
        self.assertIn("✓", html)

    def test_no_steps_is_one_grey_bar(self):
        p = mon.unit_phases(self.inc, {"units": {}})
        for unit in p:
            self.assertEqual([phase for _a, _b, phase in unit["segments"]], ["heard"])

    def test_html(self):
        html = mon.unit_bars_html(self.inc, self.phases, lambda u: {"3100001": "Engine 12"}.get(u, u))
        self.assertIn("Engine 12", html)
        self.assertIn("en route", html)
        self.assertIn("#c8553f", html)                              # on scene, on a white page
        self.assertEqual(html.count("<tr>"), 2 * len(self.phases))          # units, gaps, the time row
        self.assertIn("#ff9e91", mon.unit_bars_html(self.inc, self.phases, dark=True))
        self.assertEqual(mon.unit_bars_html(self.inc, []), "")


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_history()
        cls.inc = the_incident()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        audio = self.tmp / "source"
        audio.mkdir()
        self.mp3 = []
        for i in range(2):
            f = audio / f"call{i}.mp3"
            f.write_bytes(b"ID3fake" + bytes([i]))
            self.mp3.append(f)
        members = self.inc["members"]
        found = {members[0]["row"][0]: self.mp3[0], members[1]["row"][0]: self.mp3[1]}
        self.find = mock.patch.object(mon, "find_recording", lambda row: found.get(row[0]))
        self.find.start()
        self.addCleanup(self.find.stop)
        self.pdf_html = []

        def fake_pdf(html, path, title=None):
            self.pdf_html.append(html)
            Path(path).write_bytes(b"%PDF-1.4 fake")
        self.pdf = mock.patch.object(mon, "save_report_pdf", fake_pdf)
        self.pdf.start()
        self.addCleanup(self.pdf.stop)

    def test_name(self):
        name = mon.incident_package_name(self.inc, self.inc["summary"])
        self.assertTrue(name.startswith(f"incident_{DAY}_"))
        self.assertIn("_fire", name)
        self.assertRegex(name, r"^[a-z0-9_\-]+$")

    def test_package(self):
        result = mon.save_incident_package(self.inc, self.inc["summary"], parent=self.tmp / "out", version="1.1.93")
        folder = result["folder"]
        self.assertEqual(result["recordings"], 2)
        self.assertEqual(result["missing"], len(self.inc["members"]) - 2)
        names = sorted(p.name for p in (folder / "recordings").iterdir())
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("01_"))
        page = (folder / "index.html").read_text()
        self.assertIn(f'<audio controls preload="none" src="recordings/{names[0]}">', page)
        for part in ("What happened", "Units over time", "Calls", "Fire Dispatch", "SDS100 Monitor v1.1.93"):
            self.assertIn(part, page)
        self.assertTrue((folder / "incident.pdf").exists())
        self.assertNotIn("<audio", self.pdf_html[0])                # the PDF lists the files instead
        self.assertIn(names[0], self.pdf_html[0])
        with (folder / "calls.csv").open(newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), len(self.inc["members"]) + 1)
        self.assertEqual(rows[0][-2:], ["Recording", "Source"])
        self.assertEqual(rows[1][-2:], [f"recordings/{names[0]}", "Scanner"])
        with zipfile.ZipFile(result["zip"]) as z:
            inside = set(z.namelist())
        self.assertIn(f"{folder.name}/index.html", inside)
        self.assertIn(f"{folder.name}/recordings/{names[0]}", inside)
        again = mon.save_incident_package(self.inc, self.inc["summary"], parent=self.tmp / "out")
        self.assertTrue(again["folder"].name.endswith("_2"))        # never overwrites

    def test_no_recordings_and_pdf_failure(self):
        with mock.patch.object(mon, "find_recording", lambda row: None), \
                mock.patch.object(mon, "save_report_pdf", side_effect=RuntimeError("no printer")):
            result = mon.save_incident_package(self.inc, self.inc["summary"], parent=self.tmp / "out2")
        self.assertEqual(result["recordings"], 0)
        self.assertIsNone(result["pdf"])
        self.assertFalse((result["folder"] / "recordings").exists())
        self.assertIn("—", (result["folder"] / "index.html").read_text())

    def test_window_button(self):
        w = monitor(fresh=True)
        win = mon.IncidentsWindow(w)
        with mock.patch.object(mon, "INCIDENT_DIR", self.tmp / "win"):
            result = win.save_package(self.inc, ask=False)
        self.assertEqual(result["folder"].parent, self.tmp / "win")


class BackupTests(unittest.TestCase):
    def test_packages_go_with_recordings(self):
        self.assertEqual(mon.backup_part_of("incidents/incident_2026-09-03_1333_fire/index.html"), "recordings")
        self.assertEqual(mon.backup_part_of("incidents/incident_2026-09-03_1333_fire.zip"), "recordings")


class TodayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_history()

    def test_stats_and_text(self):
        inc = the_incident()
        stats = mon.today_stats(DAY, now=inc["end"] + timedelta(minutes=2))
        self.assertEqual(stats["calls"], len(mon.receptions_with_units(DAY)))
        self.assertGreaterEqual(stats["incidents"], 1)
        self.assertEqual(len(stats["going_on"]), 1)
        lines = mon.today_text(stats)
        self.assertTrue(lines[0].startswith(f"Today: {stats['calls']:,} calls"))
        self.assertIn("(1 going on)", lines[0])
        self.assertTrue(lines[1].startswith("▶ "))
        later = mon.today_stats(DAY, now=inc["end"] + timedelta(hours=2))
        self.assertEqual(later["going_on"], [])

    def test_last_alert(self):
        when = datetime(2026, 9, 4, 13, 5)
        mon.write_alert_log("new_talkgroup", "New talkgroup", "Swift Water", now=when)
        stats = mon.today_stats("2026-09-04", now=when)
        self.assertEqual(stats["last_alert"]["time"], "13:05")
        self.assertIn("Last alert 13:05: New talkgroup — Swift Water", mon.today_text(stats))

    def test_widget_state(self):
        w = monitor(fresh=True)
        w.today_stats.data = {"day": DAY, "calls": 12, "incidents": 1, "going_on": [], "last_alert": None}
        kicked = []
        w.today_stats.kick = mock.MagicMock(emit=lambda: kicked.append(1))
        state = mon.widget_state(w)
        self.assertEqual(state["today"], ["Today: 12 calls • 1 incident"])
        self.assertEqual(kicked, [1])                               # the first question starts a computation
        mon.widget_state(w)
        self.assertEqual(kicked, [1])                               # not again while it keeps asking
        self.assertTrue(w.today_stats.wanted())

    def test_not_computed_unless_asked(self):
        w = monitor(fresh=True)
        stats = mon.TodayStats(w)
        with mock.patch.object(mon.threading, "Thread") as thread:
            stats.tick()
            thread.assert_not_called()
            stats.asked = mon.time.monotonic()
            stats.tick()
            thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
