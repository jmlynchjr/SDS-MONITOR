# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""More daily report sections, the SDS-Manager link and the morning phone summary (v1.1.92).

Made-up days in July 2026: Jul 1–14 routine traffic and a Close Call hit on 154.4300; Jul 15 adds a new
talkgroup, a new Close Call frequency (155.1000), ADS-B alerts and callsigns, and a site health log.
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from support import mon, monitor
from test_period_reports import make_day, routine, write_csv

DAY = "2026-07-15"


def cc_row(day, time, mhz):
    return [f"{day} {time}", f"{day} {time}", "2.0", "Close Call Hits", "", f"{mhz:.4f}MHz", f"{mhz:.6f}MHz", "", "-70"]


def append_rows(day, rows):
    path = mon.daily_log_path(datetime.strptime(day, "%Y-%m-%d"))
    import csv
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def make_july():
    first = datetime(2026, 7, 1)
    for i in range(15):
        day = (first + timedelta(days=i)).strftime("%Y-%m-%d")
        calls = routine(6, 2)
        if day == DAY:
            calls.append((9000, 4, "Swift Water Rescue", "45555", ["3888888"], ""))
        make_day(day, calls)
        append_rows(day, [cc_row(day, "09:00:00", 154.43)])
    append_rows(DAY, [cc_row(DAY, "11:00:00", 155.1), cc_row(DAY, "11:05:00", 155.1)])
    write_csv(mon.adsb_alert_path(DAY), mon.ADSB_ALERT_HEADERS,
              [[f"{DAY} 13:10:00", "emergency squawk", "a1b2c3", "N123AB", "N123AB", "C172", "7700", "3500", "95",
                "34.9", "-82.2", "12.0", "7700 emergency"]])
    write_csv(mon.adsb_heard_path(DAY), mon.ADSB_HEARD_HEADERS,
              [[f"{DAY} 14:00:00", "GSP Tower", "120.1", "a.mp3", "delta 1234", "DAL1234", "abc", "", "", "", "", "",
                "", "", "callsign"]] * 2)
    write_csv(mon.SITE_HEALTH_DIR / f"site_health_{DAY}.csv", mon.SITE_HEALTH_HEADERS,
              [[f"{DAY} 10:00:00", "Palmetto 800", "Greenville Simulcast", "4.0", "92.0", "88", "30", "60"],
               [f"{DAY} 10:05:00", "Palmetto 800", "Greenville Simulcast", "3.0", "70.0", "35", "30", "60"],
               [f"{DAY} 10:10:00", "Palmetto 800", "Greenville Simulcast", "4.5", "95.0", "90", "30", "60"]])


class SectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_july()
        cls.data = mon.daily_report_data(DAY)

    def test_new_close_call_frequencies(self):
        new = mon.close_call_new(DAY)
        self.assertEqual([(k, n, t) for k, n, t, _name in new], [("155.1000", 2, "11:00")])
        self.assertEqual(mon.close_call_new("2026-07-14"), [])                  # 154.43 was hit before

    def test_new_talkgroups(self):
        new, had_history = self.data["new_talkgroups"]
        self.assertTrue(had_history)
        self.assertEqual(new, [("Swift Water Rescue (TGID:45555)", 1)])

    def test_aircraft_and_site_health(self):
        self.assertEqual(len(self.data["aircraft"]["alerts"]), 1)
        self.assertEqual(len(self.data["aircraft"]["heard"]), 2)
        site = self.data["site_health"][0]
        self.assertEqual((site["samples"], site["worst"], site["worst_time"], site["low"]), (3, 35.0, "10:05", 1))
        self.assertAlmostEqual(site["quality"], (92 + 70 + 95) / 3)

    def test_html(self):
        html = mon.daily_report_html(self.data, "1.1.92")
        for part in ("Talkgroups and channels not heard in the previous 30 days", "Swift Water Rescue",
                     "New Close Call frequencies (1)", "155.1000 MHz", "Aircraft", "7700", "DAL1234 ×2",
                     "Site health", "Greenville Simulcast", "Talkgroups heard first time"):
            self.assertIn(part, html)
        self.assertNotIn("sdsmanager:", html)                                   # only in the window
        linked = mon.daily_report_html(self.data, "1.1.92", manager_link=True)
        self.assertIn('href="sdsmanager:heard-check?days=', linked)
        self.assertNotIn("sdsmanager:", mon.strip_app_links(linked))
        self.assertIn("Check talkgroups that aren't in your Favorites Lists in SDS-Manager", mon.strip_app_links(linked))

    def test_saved_files_have_no_app_links(self):
        linked = mon.daily_report_html(self.data, "1.1.92", manager_link=True)
        path = mon.REPORTS_DIR / "link_test.html"
        mon.save_report_html(linked, path)
        self.assertNotIn("sdsmanager:", path.read_text())


class ManagerLinkTests(unittest.TestCase):
    def test_requests(self):
        self.assertEqual(mon.manager_link_request("sdsmanager:heard-check?days=3"), {"action": "heard_check", "days": 3})
        self.assertEqual(mon.manager_link_request("sdsmanager:heard-check"), {"action": "heard_check", "days": 30})
        self.assertIsNone(mon.manager_link_request("https://www.openstreetmap.org/"))
        self.assertEqual(mon.heard_check_request(9999)["days"], 365)

    def test_heard_check_is_sent_without_a_talkgroup(self):
        from test_manager_link import Socket
        Socket.running, Socket.sent = True, []
        with mock.patch.dict("sys.modules", {"PyQt6.QtNetwork": mock.MagicMock(QLocalSocket=Socket)}):
            text = mon.edit_in_manager(mon.heard_check_request(5))
        self.assertEqual(text, "Sent to SDS-Manager.")
        self.assertIn(b'"action": "heard_check"', Socket.sent[0][1])

    def test_report_window_link(self):
        make_july()
        w = monitor(fresh=True)
        win = mon.DailyReportWindow(w)
        url = mock.MagicMock()
        url.toString.return_value = "sdsmanager:heard-check?days=2"
        with mock.patch.object(mon, "edit_in_manager", return_value="Sent to SDS-Manager.") as send, \
                mock.patch.object(mon, "open_in_browser") as browser:
            win.link_clicked(url)
            send.assert_called_once()
            self.assertEqual(send.call_args[0][0], {"action": "heard_check", "days": 2})
            url.toString.return_value = "https://example.org/"
            win.link_clicked(url)
            browser.assert_called_once_with("https://example.org/")


class PhoneSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_july()

    def test_text(self):
        title, text = mon.daily_summary_text(mon.daily_report_data(DAY))
        self.assertEqual(title, "SDS100 daily summary — Wed Jul 15")
        self.assertIn("calls", text.splitlines()[0])
        self.assertIn("1 new talkgroup", text)
        self.assertIn("1 new Close Call frequency", text)
        self.assertLess(len(text), 600)
        self.assertEqual(mon.daily_summary_text(mon.daily_report_data("2026-06-01"))[1], "No receptions were logged.")

    def test_due_and_sent_once(self):
        w = monitor(fresh=True)
        s = w.settings
        saver = mon.ReportSaver(w)
        morning = datetime(2026, 7, 16, 8, 0)
        self.assertIsNone(saver.phone_summary_due(morning))                      # off by default
        s.setValue("report/phone_summary", "true")
        s.setValue("report/phone_hour", 9)
        self.assertIsNone(saver.phone_summary_due(morning))                      # before 9:00
        later = datetime(2026, 7, 16, 9, 30)
        self.assertEqual(saver.phone_summary_due(later), DAY)
        sent = []
        w.phone.send_report = lambda title, text: sent.append((title, text)) or True
        self.assertTrue(saver.send_phone_summary(later))
        self.assertEqual(sent[0][0], "SDS100 daily summary — Wed Jul 15")
        self.assertFalse(saver.send_phone_summary(later))                        # once a day
        self.assertIsNone(saver.phone_summary_due(datetime(2026, 6, 2, 10, 0)))  # no history that day

    def test_phone_off(self):
        w = monitor(fresh=True)
        w.settings.setValue("phone/enabled", "false")
        self.assertFalse(w.phone.send_report("t", "x"))


if __name__ == "__main__":
    unittest.main()
