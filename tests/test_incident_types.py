# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.88: incident types, your notes, the alert when an incident grows, and the alert log in the report."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from support import mon, monitor
from test_incident_fixes import DAY, Base, at, incidents, title


def fake(texts, channel="Dispatch", department="County"):
    members = [{"text": t, "channel": channel, "department": department} for t in texts]
    return {"members": members}


class TypeTests(Base):
    def test_from_words(self):
        fire, ems = incidents()
        self.assertEqual(fire["summary"]["type"], "fire")
        self.assertEqual(ems["summary"]["type"], "ems")
        self.assertEqual(mon.incident_type(fake(["Engine 12 respond MVC with injuries, two vehicles"])), "traffic")
        self.assertEqual(mon.incident_type(fake(["Units respond, shots fired, suspect fled on foot"])), "law")
        self.assertEqual(mon.incident_type(fake(["Tree down across the road, wires down"])), "hazard")
        self.assertEqual(mon.incident_type(fake(["Copy", "10-4"])), "other")
        self.assertEqual(mon.incident_type(fake(["Copy"], channel="Sheriff Ops 2")), "law")

    def test_ten_code_meanings_count(self):
        inc = fake(["Engine 12 10-50"], channel="Fire Dispatch")
        codes = [[("10-50", "10-50", "Motor vehicle accident", "x")]]
        self.assertEqual(mon.incident_type(inc, codes), "traffic")      # 4 points against 1 for the channel
        self.assertEqual(mon.incident_type(inc), "fire")

    def test_counts(self):
        found = incidents()
        self.assertEqual(mon.incident_type_counts(found), "1 fire, 1 EMS")

    def test_set_by_you_and_notes(self):
        fire, ems = incidents()
        edits = mon.empty_incident_edits()
        edits["types"][ems["ids"][0]] = "traffic"
        edits["notes"][fire["ids"][0]] = "Two-storey house, crews held it to one room"
        fire2, ems2 = incidents(edits)
        self.assertEqual(ems2["summary"]["type"], "traffic")
        self.assertEqual(ems2["summary"]["automatic_type"], "ems")
        self.assertTrue(ems2["edited"])
        self.assertEqual(fire2["note"], "Two-storey house, crews held it to one room")
        self.assertFalse(fire2["edited"])                          # a note isn't a fix to the incident
        undone = mon.forget_incident_edits(edits, ems2)
        self.assertEqual(undone["types"], {})
        self.assertEqual(len(undone["notes"]), 1)                  # notes stay
        self.assertEqual(mon.clean_incident_edits({"types": {"a": "volcano", "b": "fire"}})["types"], {"b": "fire"})


class GrowthTests(Base):
    def test_growth(self):
        fire, ems = incidents()
        now = at(9, 12)
        found = mon.incident_growth([fire, ems], set(), now, min_units=2)
        self.assertEqual([(i is fire, k) for i, k, _t in found], [(True, "units")])
        self.assertEqual(found[0][2], "2 units")
        self.assertEqual(mon.incident_growth([fire], {("units", fire["ids"][3])}, now, min_units=2), [])
        self.assertEqual(mon.incident_growth([fire], set(), at(10, 0), min_units=2), [])   # over for 50 min
        fire["flags"].append("more than one agency")
        self.assertEqual([k for _i, k, _t in mon.incident_growth([fire], set(), now, min_units=9)], ["agencies"])
        self.assertEqual(mon.incident_growth([fire], set(), now, min_units=9, agencies=False), [])
        fire["hidden"] = True
        self.assertEqual(mon.incident_growth([fire], set(), now, min_units=2), [])

    def test_watch(self):
        w = monitor(fresh=True)
        w.unit_title = title
        w.settings.setValue("incidents/grow_alert", "true")
        w.settings.setValue("incidents/grow_units", 2)
        watch = w.incident_watch
        self.assertTrue(watch.enabled())
        found = watch.compute(DAY, 600, 3)
        self.assertEqual(len(found), 2)
        sent = []
        w.notify = lambda title_, text, category="alert": sent.append((title_, text, category))
        watch.check(found, now=at(9, 12))
        watch.check(found, now=at(9, 13))                     # not again
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "Incident growing: 2 units")
        self.assertTrue(sent[0][1].startswith("09:00 "))
        self.assertEqual(sent[0][2], "incident")
        w.settings.setValue("incidents/grow_alert", "false")
        watch.busy = False
        with mock.patch.object(mon.threading, "Thread") as thread:
            watch.tick()
        thread.assert_not_called()

    def test_alert_kinds(self):
        self.assertIn("incident", [k for k, _l in mon.ALERT_SOUND_KINDS])
        self.assertIn("incident", [k for k, _l in mon.PHONE_CATEGORIES])
        self.assertNotIn("incident", mon.PHONE_DEFAULT_CATEGORIES | mon.PHONE_LATER_CATEGORIES)


class AlertLogTests(Base):
    def test_written_and_read(self):
        day = "2026-10-26"
        mon.write_alert_log("new_unit", "New radio heard", "UID:1 on Fire Dispatch", at(8, 0, 0, day))
        mon.write_alert_log("incident", "Incident growing: 5 units", "08:00 Fire", at(8, 5, 0, day))
        rows = mon.read_alert_log(day)
        self.assertEqual([r[1] for r in rows], ["new_unit", "incident"])
        self.assertEqual(rows[0][0], f"{day} 08:00:00")
        self.assertEqual(mon.read_alert_log("1999-01-01"), [])

    def test_notify_writes_it(self):
        w = monitor(fresh=True)
        w.settings.setValue("alerts/notify", "false")
        w.settings.setValue("alerts/sound", "false")
        w.phone = None
        today = datetime.now().strftime("%Y-%m-%d")
        before = len(mon.read_alert_log(today))
        w.notify("Keyword heard", "structure fire", "keyword")
        w.notify("Test", "test", "test")
        rows = mon.read_alert_log(today)
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(rows[-1][1:], ["keyword", "Keyword heard", "structure fire"])


class ReportAndWindowTests(Base):
    def test_report(self):
        fire, _ems = incidents()
        mon.save_incident_edits(DAY, {"notes": {fire["ids"][0]: "Held to one room"}})
        mon.write_alert_log("new_talkgroup", "New talkgroup heard: Fireground 2", "", at(9, 4))
        mon.write_alert_log("wx_alert", "Weather alert", "already in its own section", at(9, 5))
        data = mon.daily_report_data(DAY, unit_title=title)
        self.assertEqual([a[1] for a in data["alert_log"]], ["new_talkgroup"])
        html = mon.daily_report_html(data)
        self.assertIn("<th>Type</th>", html)
        self.assertIn("2 (1 fire, 1 EMS)", html)
        self.assertIn("Note: Held to one room", html)
        self.assertIn("<h2>Alerts (1)</h2>", html)
        self.assertIn("New talkgroup: 1", html)

    def test_window(self):
        w = monitor(fresh=True)
        w.unit_title = title
        win = mon.IncidentsWindow(w)
        win.reload()
        win.days.setCurrentText(DAY)
        win.recompute()
        self.assertIn("By type: 1 fire, 1 EMS", win.summary.text())
        win.type_filter.setCurrentIndex(win.type_filter.findData("ems"))
        win.fill()
        self.assertEqual([i["summary"]["type"] for i in win.visible], ["ems"])
        ems = win.visible[0]
        win.set_type(ems, "law")
        self.assertEqual(win.visible, [])                      # no longer EMS
        win.type_filter.setCurrentIndex(0)
        win.fill()
        ems = next(i for i in win.incidents if i["start"].hour == 11)
        self.assertEqual(ems["summary"]["type"], "law")
        with mock.patch.object(mon.QInputDialog, "getMultiLineText", return_value=("Patient refused", True)):
            win.edit_note(ems)
        ems = next(i for i in win.incidents if i["start"].hour == 11)
        self.assertEqual(ems["note"], "Patient refused")
        win.current = lambda: ems
        win.show_incident()
        self.assertIn("Type: Law (set by you)", win.story.text())
        self.assertIn("Note: Patient refused", win.story.text())
        win.grow_alert.setChecked(True)
        win.grow_units.setValue(7)
        win.grow_changed()
        self.assertEqual(w.settings.value("incidents/grow_alert"), "true")
        self.assertEqual(w.settings.value("incidents/grow_units"), 7)


if __name__ == "__main__":
    unittest.main()
