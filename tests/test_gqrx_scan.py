# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""Scanning with GQRX (v1.1.98): scan lists, stopping on signals, hold, skip."""
import threading
import unittest

from support import mon, monitor
from test_gqrx import FakeGqrx, Recorder, wait_for, BOOKMARKS


class ActiveGqrx(FakeGqrx):
    """A fake GQRX whose signal is strong only on the frequencies in `active`."""

    def __init__(self):
        super().__init__()
        self.active = set()
        self.tuned = []

    @property
    def level(self):
        return -40.0 if self.hz in self.active else -95.0

    @level.setter
    def level(self, value):
        pass


class ListTests(unittest.TestCase):
    def test_from_bookmarks(self):
        marks = mon.gqrx_bookmarks_from_text(BOOKMARKS)
        everything = mon.gqrx_scan_list_from_bookmarks(marks)
        self.assertEqual([e["hz"] for e in everything], [5696000, 7200000, 121500000])
        self.assertEqual(everything[2]["mode"], "AM")
        amateur = mon.gqrx_scan_list_from_bookmarks(marks, ["amateur"])
        self.assertEqual([(e["hz"], e["mode"], e["bandwidth"]) for e in amateur], [(7200000, "USB", 2800)])
        self.assertEqual(mon.gqrx_scan_list_from_bookmarks(marks, ["Untagged"])[0]["hz"], 5696000)

    def test_modes(self):
        self.assertEqual(mon.gqrx_remote_mode("Narrow FM"), "FM")
        self.assertEqual(mon.gqrx_remote_mode("WFM (stereo)"), "WFM_ST")
        self.assertEqual(mon.gqrx_remote_mode("CW-U"), "CWU")
        self.assertEqual(mon.gqrx_remote_mode("", 7.2), "AM")

    def test_range(self):
        r = mon.gqrx_range_list(6.2, 5.9, 5)                       # either way round
        self.assertEqual(len(r), 61)
        self.assertEqual((r[0]["hz"], r[-1]["hz"], r[0]["mode"]), (5900000, 6200000, "AM"))
        self.assertEqual(mon.gqrx_range_list(146.52, 146.52, 12.5, "FM")[0]["mode"], "FM")
        with self.assertRaises(ValueError):
            mon.gqrx_range_list(0.1, 30, 0.1)                      # too many
        with self.assertRaises(ValueError):
            mon.gqrx_range_list(1, 2, 0)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.server = ActiveGqrx()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.w = monitor(fresh=True)
        s = self.w.settings
        for key, value in (("port", str(self.server.server_address[1])), ("audio", "false"), ("hang", "0.4"),
                           ("shortest", "0.1"), ("poll_ms", "50")):
            s.setValue(f"gqrx/{key}", value)
        self.r = self.w.gqrx
        self.r.state = Recorder()
        self.r.finished = Recorder()
        self.r.scan_hit = Recorder()
        self.r.note = Recorder()
        self.entries = mon.gqrx_range_list(7.000, 7.020, 5, "USB")    # 5 frequencies

    def tearDown(self):
        self.r.scan = None
        self.r.running = False
        if self.r.thread is not None:
            self.r.thread.join(timeout=3)
        self.server.shutdown()
        self.server.server_close()

    def test_scans_and_stops_on_a_signal(self):
        self.r.start_scan(self.entries, 50)
        self.assertTrue(wait_for(lambda: self.r.scan["steps"] >= 6))   # went round the list
        self.assertEqual(self.r.scan_hit.items, [])
        self.assertEqual(self.server.mode, "USB")
        self.server.active = {7010000}
        self.assertTrue(wait_for(lambda: self.r.scan_hit.items))
        self.assertEqual(self.r.scan_hit.items[0]["hz"], 7010000)
        self.assertTrue(wait_for(lambda: self.r.scan["holding"] is not None))
        threading.Event().wait(0.3)
        self.assertEqual(self.server.hz, 7010000)                      # stays while the signal is there
        self.server.active = set()
        self.assertTrue(wait_for(lambda: self.r.scan["holding"] is None))
        self.assertTrue(wait_for(lambda: self.server.hz != 7010000))   # and moves on after the hang time

    def test_logs_when_logging_is_on(self):
        self.w.settings.setValue("gqrx/enabled", "true")
        self.server.active = {7015000}
        self.r.start_scan(self.entries, 50)
        self.assertTrue(self.r.log_now)
        self.assertTrue(wait_for(lambda: self.r.scan_hit.items))
        threading.Event().wait(0.4)
        self.server.active = set()
        self.assertTrue(wait_for(lambda: self.r.finished.items))
        self.assertEqual(self.r.finished.items[0]["hz"], 7015000)

    def test_hold_and_skip(self):
        self.r.start_scan(self.entries, 50)
        self.server.active = {7005000}
        self.assertTrue(wait_for(lambda: self.r.scan["holding"] is not None))
        self.r.scan_hold(True)
        self.server.active = set()
        threading.Event().wait(0.8)
        self.assertEqual(self.server.hz, 7005000)                      # your hold keeps it there
        self.r.scan_skip()
        self.assertIn(7005000, self.r.scan["skipped"])
        self.server.active = {7005000}                                 # skipped: never stops there again
        threading.Event().wait(0.8)
        self.assertEqual(len(self.r.scan_hit.items), 1)
        self.assertEqual(self.r.scan_status()["count"], 4)

    def test_squelch_off_stops_the_scan(self):
        self.server.sql = mon.GQRX_SQUELCH_OFF
        self.r.start_scan(self.entries, 50)
        self.assertTrue(wait_for(lambda: self.r.scan is None))
        self.assertTrue(any("squelch" in n for n in self.r.note.items))

    def test_follow_waits_while_scanning(self):
        self.w.settings.setValue("gqrx/follow", "true")
        self.r.start_scan(self.entries, 50)
        self.r.follow_scanner({"Frequency": "155.340000MHz", "Talkgroup": "", "Mode": "NFM"})
        threading.Event().wait(0.3)
        self.assertNotEqual(self.server.hz, 155340000)
        self.r.stop_scan()
        self.assertIsNone(self.r.scan)
        self.assertTrue(self.r.running)                                # still following


class WindowTests(unittest.TestCase):
    def test_window(self):
        w = monitor(fresh=True)
        w.open_gqrx_scan()
        win = w.gqrx_scan_window
        win.add_hit({"when": mon.datetime.now(), "hz": 7200000, "name": "net", "mode": "USB", "level": -50.0})
        self.assertEqual(len(win.hits), 1)
        win.show_reading({"error": "x"})
        win.show_reading({"hz": 1, "mode": "AM", "level": -50.0, "sql": -70.0, "scan": {
            "count": 3, "skipped": 0, "rate": 4.0, "holding": None, "manual": False, "hits": 0,
            "now": {"hz": 7200000, "name": "net"}, "level": -50.0, "sql": -70.0}})
        with open(mon.__file__, encoding="utf-8") as f:
            self.assertIn('tools_menu.addAction("GQRX Scan…"', f.read())


if __name__ == "__main__":
    unittest.main()
