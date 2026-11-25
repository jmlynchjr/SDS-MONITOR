# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""Weekly, monthly and date-range reports (v1.1.91), from made-up history in August–September 2026.

Week of Aug 24–30: Fire Dispatch 10 calls a day, Ops 51 4 a day.
Week of Aug 31–Sep 6: Fire Dispatch the same, Ops 51 up to 12 a day (rising), a new talkgroup
(Water Rescue) and a new radio on Sep 2, and an incident with Engine 12 and Medic 4 on Sep 3.
"""
import csv
import unittest
from datetime import datetime, timedelta

from support import mon, monitor


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_day(day, calls):
    """calls: (seconds after 08:00, length, channel, tgid, [unit ids], transcript)"""
    base = datetime.strptime(day, "%Y-%m-%d").replace(hour=8)
    receptions, units, transcripts = [], [], []
    for offset, length, channel, tg, uids, text in calls:
        start = base + timedelta(seconds=offset)
        end = start + timedelta(seconds=length)
        s, e = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
        receptions.append([s, e, f"{length:.1f}", "Palmetto 800", "Greenville County", channel,
                           " 859.262500MHz", f"TGID:{tg}", "-52"])
        for u in uids:
            units.append([(start + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), f"UID:{u}", "",
                          "Palmetto 800", "Simulcast", "Greenville County", channel, f"TGID:{tg}", "859.2625"])
        if text:
            transcripts.append([s, "Palmetto 800", "Greenville County", channel, f"TGID:{tg}",
                                "; ".join(f"UID:{u}" for u in uids), str(length), f"{offset}.mp3", text,
                                "small.en", s])
    write_csv(mon.daily_log_path(base), mon.HEADERS, receptions)
    write_csv(mon.unit_log_path(base), mon.UNIT_HEADERS, units)
    if transcripts:
        write_csv(mon.transcript_path(day), mon.TRANSCRIPT_HEADERS, transcripts)
    # Listening 08:00–17:59 every day: 10 hours.
    mon.COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    mon.coverage_path(day).write_text("".join(f"{day} {h:02d}:{m:02d}\n" for h in range(8, 18) for m in range(60)))


def routine(n_fire, n_ops):
    calls = [(i * 1800, 4, "Fire Dispatch", "44002", ["3100001"], "") for i in range(n_fire)]
    calls += [(i * 2400 + 600, 3, "Ops 51 (GFD)", "44353", ["3100002"], "") for i in range(n_ops)]
    return calls


def make_history():
    first = datetime(2026, 8, 24)
    for i in range(14):
        day = (first + timedelta(days=i)).strftime("%Y-%m-%d")
        calls = routine(10, 4 if i < 7 else 12)
        if day == "2026-09-02":
            calls.append((30000, 5, "Water Rescue", "44999", ["3999999"], ""))
        if day == "2026-09-03":
            calls += [(20000, 8, "Fire Dispatch", "44002", ["3100001"], "Engine 12 respond structure fire 100 Main"),
                      (20040, 5, "Fire Dispatch", "44002", ["3100001", "3200004"], "Engine 12 en route"),
                      (20300, 6, "Fireground 2", "44400", ["3100001", "3200004"], "Engine 12 on scene, smoke showing"),
                      (20420, 4, "Fireground 2", "44400", ["3200004"], "Medic 4 on scene")]
        make_day(day, calls)


class PeriodMathTests(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(mon.period_bounds("week", "2026-09-03"), ("2026-08-31", "2026-09-06"))
        self.assertEqual(mon.period_bounds("week", "2026-08-31"), ("2026-08-31", "2026-09-06"))
        self.assertEqual(mon.period_bounds("month", "2026-02-14"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(mon.period_bounds("month", "2026-12-31"), ("2026-12-01", "2026-12-31"))
        self.assertEqual(mon.period_bounds("day", "2026-09-03"), ("2026-09-03", "2026-09-03"))

    def test_previous_period(self):
        self.assertEqual(mon.previous_period("2026-08-31", "2026-09-06"), ("2026-08-24", "2026-08-30"))
        self.assertEqual(mon.previous_period("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(mon.previous_period("2026-09-01", "2026-09-10"), ("2026-08-22", "2026-08-31"))

    def test_choices_newest_first(self):
        days = ["2026-08-30", "2026-08-31", "2026-09-06", "2026-09-07"]
        self.assertEqual([a for _l, a in mon.period_choices("week", days)],
                         ["2026-09-07", "2026-08-31", "2026-08-24"])
        self.assertEqual(mon.period_choices("month", days), [("September 2026", "2026-09-01"),
                                                             ("August 2026", "2026-08-01")])

    def test_titles(self):
        self.assertEqual(mon.period_title("month", "2026-09-01", "2026-09-30"), "Monthly report — September 2026")
        self.assertEqual(mon.period_title("week", "2026-08-31", "2026-09-06"),
                         "Weekly report — Aug 31 to Sep 6, 2026")


class PeriodReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_history()
        cls.data = mon.period_report_data("week", "2026-08-31", "2026-09-06", today="2026-11-17")

    def test_totals_and_days(self):
        d = self.data
        self.assertEqual(d["days_with_history"], 7)
        self.assertEqual(d["calls"], 7 * 22 + 1 + 4)
        self.assertEqual(len(d["days"]), 7)
        self.assertEqual(d["listened_hours"], 70)
        self.assertEqual(d["busiest_day"][0], "2026-09-03")
        self.assertEqual(d["previous"]["calls"], 7 * 14)
        self.assertEqual(d["previous"]["days"], 7)
        self.assertAlmostEqual(d["rate"], d["calls"] / 70)

    def test_rising_talkgroup(self):
        rising = [r[0] for r in self.data["rising"]]
        self.assertIn("Ops 51 (GFD) (TGID:44353)", rising)
        self.assertNotIn("Fire Dispatch (TGID:44002)", rising)

    def test_first_heard(self):
        self.assertTrue(self.data["first_heard_ready"])
        labels = [l for l, _n in self.data["new_talkgroups"]]
        self.assertIn("Water Rescue (TGID:44999)", labels)
        self.assertIn("Fireground 2 (TGID:44400)", labels)
        self.assertNotIn("Fire Dispatch (TGID:44002)", labels)
        self.assertIn("3999999", [u for u, _t, _n in self.data["new_units"]])

    def test_incident(self):
        self.assertGreaterEqual(self.data["incident_count"], 1)
        big = self.data["biggest"][0]
        self.assertEqual(big["day"], "2026-09-03")
        self.assertEqual(big["type"], "fire")
        self.assertGreaterEqual(big["units"], 2)

    def test_html(self):
        html = mon.period_report_html(self.data, "1.1.91")
        for part in ("Weekly report — Aug 31 to Sep 6, 2026", "Day by day", "Activity by hour",
                     "Busiest talkgroups", "Rising and falling", "Heard for the first time", "Water Rescue",
                     "Biggest incidents", "159 (up 62% from 98)", "Compared with Aug 24"):
            self.assertIn(part, html)

    def test_empty_period(self):
        data = mon.period_report_data("month", "2026-06-01", "2026-06-30", today="2026-11-17")
        self.assertEqual(data["calls"], 0)
        self.assertIn("No receptions were logged in this period", mon.period_report_html(data))

    def test_month_without_previous_history(self):
        data = mon.period_report_data("month", "2026-08-01", "2026-08-31", today="2026-11-17")
        self.assertEqual(data["previous"]["days"], 0)
        html = mon.period_report_html(data)
        self.assertIn("Monthly report — August 2026", html)
        self.assertNotIn("Rising and falling", html)

    def test_finished_days_are_cached(self):
        a = mon.day_summary("2026-08-25", today="2026-11-17")
        self.assertIs(mon.day_summary("2026-08-25", today="2026-11-17"), a)
        make_day("2026-08-25", routine(3, 0))           # the file changes: worked out again
        b = mon.day_summary("2026-08-25", today="2026-11-17")
        self.assertEqual(b["calls"], 3)
        make_day("2026-08-25", routine(10, 4))           # put it back for the other tests
        mon.day_summary("2026-08-25", today="2026-11-17")


class AutoSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_history()

    def test_due_periods(self):
        w = monitor(fresh=True)
        s = w.settings
        s.setValue("report/auto_pdf", "true")
        saver = mon.ReportSaver(w)
        self.assertEqual(saver.due_periods(today="2026-11-17"), [])     # weeks and months off
        s.setValue("report/auto_period", "true")
        due = saver.due_periods(today="2026-09-08")
        kinds = [(k, a) for k, a, _b, _m in due]
        self.assertIn(("week", "2026-08-31"), kinds)
        self.assertIn(("week", "2026-08-24"), kinds)
        self.assertIn(("month", "2026-08-01"), kinds)
        self.assertNotIn(("month", "2026-09-01"), kinds)              # September isn't over
        self.assertEqual(mon.period_report_path("month", "2026-08-01", "pdf").name, "monthly_report_2026-08.pdf")
        self.assertEqual(mon.period_report_path("week", "2026-08-31", "html").name, "weekly_report_2026-08-31.html")


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_history()

    def test_switching_report_kind(self):
        w = monitor(fresh=True)
        win = mon.DailyReportWindow(w)
        win.kind.setCurrentIndex(win.kind.findData("week"))
        index = win.days.findData("2026-08-31")
        self.assertGreaterEqual(index, 0)
        win.days.setCurrentIndex(index)
        self.assertEqual(win.period(), ("week", "2026-08-31", "2026-09-06"))
        win.make()
        self.assertIn("Weekly report", win.html)
        self.assertEqual(win.file_stem(), "weekly_report_2026-08-31")
        self.assertEqual(w.settings.value("report/kind"), "week")
        win.kind.setCurrentIndex(win.kind.findData("range"))
        win.days.setCurrentIndex(win.days.findData("2026-09-06"))
        win.days_to.setCurrentIndex(win.days_to.findData("2026-09-01"))
        self.assertEqual(win.period(), ("range", "2026-09-01", "2026-09-06"))
        self.assertEqual(win.file_stem(), "report_2026-09-01_to_2026-09-06")
        win.kind.setCurrentIndex(win.kind.findData("day"))
        win.days.setCurrentIndex(win.days.findData("2026-09-03"))
        win.make()
        self.assertIn("Daily report", win.html)


if __name__ == "__main__":
    unittest.main()
