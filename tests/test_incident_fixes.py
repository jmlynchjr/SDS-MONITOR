# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.87: your own fixes to incidents, the step-by-step summary with times on scene, and the daily report's
listening time and comparison with normal traffic."""
import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from support import mon, monitor

DAY = "2026-10-21"
NAMES = {"3100001": "Engine 12", "3100002": "Ladder 3", "3200004": "Medic 4", "3900009": "PW 9"}


def title(u):
    return NAMES.get(str(u).split(":")[-1], str(u))


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_day(day, calls):
    """calls: (datetime, length, channel, tgid, [uids], transcript)."""
    receptions, units, transcripts = [], [], []
    for start, length, channel, tg, uids, text in calls:
        end = start + timedelta(seconds=length)
        s, e = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
        receptions.append([s, e, f"{length:.1f}", "Palmetto 800", "Greenville County", channel,
                           " 859.262500MHz", f"TGID:{tg}", "-52"])
        for u in uids:
            units.append([(start + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), f"UID:{u}", "",
                          "Palmetto 800", "Simulcast", "Greenville County", channel, f"TGID:{tg}", "859.2625"])
        if text:
            transcripts.append([s, "Palmetto 800", "Greenville County", channel, f"TGID:{tg}",
                                "; ".join(f"UID:{u}" for u in uids), str(length), f"{start:%H%M%S}.mp3", text, "small.en", s])
    when = datetime.strptime(day, "%Y-%m-%d")
    write_csv(mon.daily_log_path(when), mon.HEADERS, receptions)
    write_csv(mon.unit_log_path(when), mon.UNIT_HEADERS, units)
    write_csv(mon.transcript_path(day), mon.TRANSCRIPT_HEADERS, transcripts)


def at(hh, mm, ss=0, day=DAY):
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, second=ss)


def make_day():
    write_day(DAY, [
        (at(9, 0, 0), 8, "Fire Dispatch", "44002", ["3100001"], "Engine 12 respond structure fire 100 Main"),
        (at(9, 0, 30), 4, "Fire Dispatch", "44002", ["3100001"], "Engine 12 responding"),
        (at(9, 4, 0), 6, "Fireground 2", "44010", ["3100001", "3100002"], "Engine 12 on scene, working fire"),
        (at(9, 5, 0), 5, "Fireground 2", "44010", ["3100002"], "Ladder 3 on scene"),
        (at(9, 10, 0), 4, "Fireground 2", "44010", ["3100001"], "Engine 12 back in service"),
        (at(11, 0, 0), 5, "EMS 1", "45001", ["3200004"], "Medic 4 en route"),
        (at(11, 6, 0), 5, "EMS 1", "45001", ["3200004"], "Medic 4 arrived"),
        (at(11, 12, 0), 5, "EMS 1", "45001", ["3200004"], "Medic 4 transporting one"),
        (at(13, 0, 0), 3, "Public Works", "46000", ["3900009"], ""),
    ])


def incidents(edits=None, min_calls=3):
    recs = mon.receptions_with_units(DAY)
    found = mon.build_incidents(recs, set(), min_calls=min_calls, edits=edits)
    for inc in found:
        inc["summary"] = mon.summarize_incident(inc, None, (), title)
    return sorted(found, key=lambda i: i["start"])


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_day()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(mon, "INCIDENT_EDITS_FILE", Path(self.tmp.name) / "incident_edits.json")
        p.start()
        self.addCleanup(p.stop)


class StoryTests(Base):
    def test_steps(self):
        self.assertEqual(mon.call_step("Engine 12 responding"), "en_route")
        self.assertEqual(mon.call_step("Medic 4 en route to the hospital"), "transport")
        self.assertEqual(mon.call_step("Engine 12 <b>10-23</b>", [("10-23", "10-23", "Arrived on scene", "x")]),
                         "on_scene")
        self.assertEqual(mon.call_step("10-8", [("10-8", "10-8", "In service", "x")]), "clear")
        self.assertEqual(mon.call_step("respond to 100 Main"), "")

    def test_fire(self):
        fire, ems = incidents()
        story = fire["summary"]["story"]
        text = mon.incident_story_text(story)
        self.assertIn("09:00  Started on", text)
        self.assertIn("structure fire 100 Main", text)
        self.assertIn("09:00  Engine 12 en route", text)
        self.assertIn("09:04  Moved to", text)
        self.assertIn("09:04  Engine 12 on scene (+4 min)", text)
        self.assertIn("Ladder 3 on scene", text)
        self.assertIn("09:10  Engine 12 back in service", text)
        self.assertEqual(story["first_on_scene"], 240)
        self.assertEqual(title(story["first_unit"]), "Engine 12")
        self.assertIn("first on scene after 4 min (Engine 12)", story["sentence"])
        ems_story = ems["summary"]["story"]
        self.assertEqual(ems_story["first_on_scene"], 360)
        self.assertIn("Medic 4 transporting", mon.incident_story_text(ems_story))

    def test_no_steps(self):
        inc = {"members": [], "start": at(9, 0), "end": at(9, 0), "units": []}
        self.assertIsNone(mon.incident_story(inc, [])["first_on_scene"])
        self.assertEqual(mon.incident_story_text(mon.incident_story(inc, [])), "")

    def test_median(self):
        self.assertIsNone(mon.median([]))
        self.assertEqual(mon.median([5, 1, 3]), 3)
        self.assertEqual(mon.median([4, 1, 3, 2]), 2.5)


class FixTests(Base):
    def ids(self):
        return [i["ids"] for i in incidents()]

    def test_automatic(self):
        found = incidents()
        self.assertEqual(len(found), 2)                       # the lone Public Works call isn't one
        self.assertFalse(any(i["edited"] or i["hidden"] for i in found))
        self.assertEqual(found[0]["ids"][0], f"{DAY} 09:00:00|Fire Dispatch")

    def test_take_a_call_out(self):
        fire = incidents()[0]
        edits = mon.empty_incident_edits()
        edits["removed"].append(fire["ids"][-1])
        fire2 = incidents(edits)[0]
        self.assertEqual(len(fire2["members"]), 4)
        self.assertEqual(fire2["end"], at(9, 5, 5))
        self.assertTrue(fire2["edited"])
        self.assertIn("changed by you", fire2["flags"])

    def test_split_keeps_small_pieces(self):
        ems = incidents()[1]
        edits = mon.empty_incident_edits()
        edits["splits"].append(ems["ids"][1])
        found = incidents(edits)
        self.assertEqual([len(i["members"]) for i in found], [5, 1, 2])
        self.assertTrue(found[1]["edited"] and found[2]["edited"])
        self.assertFalse(found[0]["edited"])

    def test_merge(self):
        fire, ems = incidents()
        edits = mon.empty_incident_edits()
        edits["joins"].append([fire["ids"][0], ems["ids"][0]])
        found = incidents(edits)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]["members"]), 8)

    def test_hide_and_headline(self):
        fire, ems = incidents()
        edits = mon.empty_incident_edits()
        edits["hidden"].append(ems["ids"][0])
        edits["headlines"][fire["ids"][0]] = "House fire, 100 Main St"
        fire2, ems2 = incidents(edits)
        self.assertTrue(ems2["hidden"])
        self.assertEqual(fire2["summary"]["headline"], "House fire, 100 Main St")
        self.assertNotEqual(fire2["summary"]["automatic_headline"], "House fire, 100 Main St")

    def test_saved_per_day_and_undone(self):
        fire = incidents()[0]
        edits = mon.load_incident_edits(DAY)
        edits["removed"].append(fire["ids"][-1])
        edits["headlines"][fire["ids"][0]] = "Mine"
        mon.save_incident_edits(DAY, edits)
        mon.save_incident_edits("2026-10-22", {"hidden": ["x|y"]})
        self.assertEqual(mon.load_incident_edits(DAY)["headlines"], {fire["ids"][0]: "Mine"})
        self.assertEqual(mon.load_incident_edits("2026-10-22")["hidden"], ["x|y"])
        fixed = incidents(mon.load_incident_edits(DAY))[0]
        undone = mon.forget_incident_edits(mon.load_incident_edits(DAY), fixed)
        self.assertEqual(undone, mon.empty_incident_edits())   # the call taken out is put back too
        mon.save_incident_edits(DAY, undone)
        data = json.loads(mon.INCIDENT_EDITS_FILE.read_text())
        self.assertNotIn(DAY, data)                            # nothing left for the day
        self.assertIn("2026-10-22", data)

    def test_bad_file(self):
        mon.INCIDENT_EDITS_FILE.write_text("not json")
        self.assertEqual(mon.load_incident_edits(DAY), mon.empty_incident_edits())
        clean = mon.clean_incident_edits({"removed": "x", "joins": [["a"], ["a", "b"], 5],
                                          "headlines": {"a": " ", "b": "B"}})
        self.assertEqual(clean["removed"], [])
        self.assertEqual(clean["joins"], [["a", "b"]])
        self.assertEqual(clean["headlines"], {"b": "B"})


class WindowTests(Base):
    def window(self):
        w = monitor(fresh=True)
        w.unit_title = title
        win = mon.IncidentsWindow(w)
        win.reload()
        win.days.setCurrentText(DAY)
        win.recompute()
        return win

    def test_fixes_from_the_window(self):
        win = self.window()
        self.assertEqual(len(win.incidents), 2)
        fire = sorted(win.incidents, key=lambda i: i["start"])[0]
        win.current = lambda: fire
        win.show_incident()
        self.assertIn("Engine 12 on scene (+4 min)", win.story.text())
        win.split_at(fire["members"][2])
        self.assertEqual(len(win.incidents), 3)
        win.forget(sorted(win.incidents, key=lambda i: i["start"])[0])
        self.assertEqual(len(win.incidents), 2)
        ems = sorted(win.incidents, key=lambda i: i["start"])[1]
        win.set_hidden(ems, True)
        self.assertEqual(len(win.visible), 1)
        win.show_hidden.setChecked(True)
        win.fill()
        self.assertEqual(len(win.visible), 2)
        fire = sorted(win.incidents, key=lambda i: i["start"])[0]
        with mock.patch.object(mon.QInputDialog, "getText", return_value=("Structure fire", True)):
            win.edit_headline(fire)
        self.assertEqual(sorted(win.incidents, key=lambda i: i["start"])[0]["summary"]["headline"],
                         "Structure fire")
        fire = sorted(win.incidents, key=lambda i: i["start"])[0]
        with mock.patch.object(mon.QInputDialog, "getItem", side_effect=lambda *a, **k: (a[3][0], True)):
            win.merge_with(fire)
        self.assertEqual(len(win.incidents), 1)


class CoverageTests(Base):
    def test_log_one_line_a_minute(self):
        log = mon.CoverageLog()
        day = "2026-10-23"
        for second in range(0, 150, 1):
            log.mark(at(10, 0, 0, day) + timedelta(seconds=second))
        self.assertEqual(mon.coverage_path(day).read_text().splitlines(),
                         [f"{day} 10:00", f"{day} 10:01", f"{day} 10:02"])
        self.assertEqual(mon.read_coverage(day), {600, 601, 602})
        self.assertIsNone(mon.read_coverage("1999-01-01"))

    def test_spans_and_hours(self):
        minutes = set(range(60, 120)) | {122, 125} | set(range(600, 640))
        self.assertEqual(mon.coverage_spans(minutes), [(60, 125), (600, 639)])
        day = "2026-10-24"
        mon.COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
        mon.coverage_path(day).write_text("".join(f"{day} {m // 60:02d}:{m % 60:02d}\n" for m in sorted(minutes)))
        self.assertEqual(mon.listening_hours(day, []), ({1, 10}, True))
        self.assertEqual(mon.listening_hours("1999-01-01", [["1999-01-01 05:10:00"]]), ({5}, False))

    def test_compare_with_normal(self):
        normal = {"days": 10, "hours": 100, "calls": 1000,
                  "by_label": mon.Counter({"Fire Dispatch": 500, "EMS 1": 400, "Public Works": 100})}
        today = mon.Counter({"Fire Dispatch": 120, "EMS 1": 10, "New TG": 12})
        result = mon.compare_with_normal(today, 10, normal)
        self.assertTrue(result["ready"])
        self.assertEqual(result["expected"], 100)
        self.assertEqual([b[0] for b in result["busier"]], ["Fire Dispatch", "New TG"])
        self.assertEqual([q[0] for q in result["quieter"]], ["EMS 1"])
        self.assertFalse(mon.compare_with_normal(today, 10, dict(normal, days=3))["ready"])
        self.assertFalse(mon.compare_with_normal(today, 0, normal)["ready"])

    def test_texts(self):
        self.assertIn("not recorded", mon.report_listening_text({"measured": False, "hours": [1, 2]}))
        self.assertEqual(mon.report_listening_text({"measured": True, "minutes": 90, "spans": [(60, 149)]}),
                         "1 h 30 min (01:00–02:30)")
        self.assertIn("needs 5 days", mon.report_normal_text({"ready": False}))
        self.assertIn("much busier than usual", mon.report_normal_text({"ready": True, "expected": 100,
                                                                         "today": 200}))
        self.assertIn("about usual", mon.report_normal_text({"ready": True, "expected": 100, "today": 105}))
        self.assertIn("median 5 min, from 2 incidents",
                      mon.report_on_scene_text({"count": 2, "median": 300}))


class ReportTests(Base):
    def test_report(self):
        mon._NORMAL_CACHE.clear()
        for back in range(1, 7):                              # six earlier days: Fire Dispatch twice an hour
            day = (datetime.strptime(DAY, "%Y-%m-%d") - timedelta(days=back)).strftime("%Y-%m-%d")
            write_day(day, [(at(h, m, 0, day), 3, "Fire Dispatch", "44002", ["3100001"], "")
                            for h in (9, 11, 13) for m in (0, 30)])
        mon.COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
        mon.coverage_path(DAY).write_text("".join(f"{DAY} {h:02d}:{m:02d}\n" for h in (9, 10, 11, 12, 13)
                                                  for m in range(60)))
        fire = incidents()[0]
        mon.save_incident_edits(DAY, {"headlines": {fire["ids"][0]: "Structure fire, 100 Main"}})
        data = mon.daily_report_data(DAY, unit_title=title)
        self.assertTrue(data["coverage"]["measured"])
        self.assertEqual(data["coverage"]["minutes"], 300)
        self.assertEqual(data["coverage"]["spans"], [(540, 839)])
        self.assertEqual(data["normal"]["days"], 6)
        self.assertEqual(data["on_scene"], {"count": 2, "median": 300.0})
        html = mon.daily_report_html(data)
        self.assertIn("5 h 00 min (09:00–14:00)", html)
        self.assertIn("First unit on scene", html)
        self.assertIn("median 5 min", html)
        self.assertIn("Structure fire, 100 Main", html)
        self.assertIn("<th>On scene</th>", html)
        mon.save_incident_edits(DAY, {"hidden": [fire["ids"][0]]})
        self.assertEqual(len(mon.daily_report_data(DAY, unit_title=title)["incidents"]), 1)


if __name__ == "__main__":
    unittest.main()
