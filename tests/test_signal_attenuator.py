# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.111: the signal log, the attenuator advice and the attenuator test."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from support import mon, monitor
from test_incident_fixes import write_csv

DAY = "2026-10-27"
WHEN = datetime(2026, 10, 27, 12, 0, 0)


def line(minute, system, site, channel, freq, tg, att, rssi):
    start = (WHEN + timedelta(minutes=minute)).strftime("%Y-%m-%d %H:%M:%S")
    return [start, system, site, "Dept", channel, freq, tg, att, str(rssi - 2), str(rssi), str(rssi + 2), "8"]


class LogTests(unittest.TestCase):
    def test_row_and_reading(self):
        mon.signal_path(DAY).unlink(missing_ok=True)
        event = {"rssi": [-60, -48, -52, -50], "att": ["Off", "Off", "On"], "site": "Simulcast", "info": {}}
        row = [f"{DAY} 12:00:00", "", "3.0", "Palmetto 800", "Greenville County", "Sheriff Dispatch",
               "859.262500MHz", "TGID:44002", "-48"]
        out = mon.signal_row(event, row)
        self.assertEqual(out[7], "Off")
        self.assertEqual(out[8:12], ["-60", "-50", "-48", "4"])
        self.assertIsNone(mon.signal_row({"rssi": []}, row))
        mon.save_signal_row(out)
        self.assertEqual(mon.signal_rows(DAY)[0][2], "Simulcast")

    def test_old_days_from_history(self):
        day = "2026-10-28"
        mon.DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        write_csv(mon.daily_log_path(datetime(2026, 10, 28)), mon.HEADERS, [
            [f"{day} 08:00:00", f"{day} 08:00:03", "3.0", "County Fire", "Fire", "Fire Dispatch", "154.430000MHz",
             "", "-40"]])
        rows = mon.signal_rows(day)
        self.assertEqual(rows[0][7], "?")
        self.assertEqual(rows[0][9], "-40")

    def test_from_a_reception(self):
        m = monitor(fresh=True)
        m.reception = {"key": ("a",), "start": WHEN, "last": WHEN, "rssi": [-70, -71], "att": ["G-Att"],
                       "info": {"System": "County Fire", "Department": "Fire", "Channel": "Tac 2",
                                "Frequency": "154.280000MHz", "Talkgroup": "", "RSSI": "-70"},
                       "site": "—", "units": [], "discovery": None, "disc_hits": ""}
        with mock.patch.object(mon, "save_signal_row") as saved:
            m.finish_event()
        self.assertEqual(saved.call_args[0][0][7], "G-Att")


class ReportTests(unittest.TestCase):
    def rows(self):
        return ([line(i, "Palmetto 800", "Simulcast", "Sheriff Dispatch", "859.2625", "TGID:44002", "Off", -42)
                 for i in range(6)]
                + [line(i, "County Fire", "—", "Tac 2", "154.2800", "", "On", -101) for i in range(5)]
                + [line(i, "County Fire", "—", "Dispatch", "154.4300", "", "Off", -75) for i in range(5)]
                + [line(i, "Custom Search", "—", "155.1000", "155.1000", "", "Off", -30) for i in range(5)]
                + [line(1, "Palmetto 800", "North", "x", "", "TGID:1", "Off", -40)])

    def report(self, health=None):
        with mock.patch.object(mon, "signal_rows", lambda day: self.rows() if day == DAY else []), \
                mock.patch.object(mon, "site_health_day",
                                  lambda day: [{"system": "Palmetto 800", "site": "Simulcast", "samples": 10,
                                                "quality": 45.0}] if day == DAY else []):
            return mon.signal_report(1, health=health, today=WHEN)

    def test_advice(self):
        items = {i["name"]: i for i in self.report(health={})}
        self.assertNotIn("155.1000 (155.1000)", items)                       # search hits aren't judged
        self.assertEqual(items["Tac 2 (154.2800)"]["level"], "fix")         # weak and attenuated
        self.assertEqual(items["Simulcast"]["level"], "try")                # strong, decoding poorly
        self.assertEqual(items["Dispatch (154.4300)"]["level"], "ok")
        self.assertEqual(items["North"]["level"], "few")
        self.assertEqual(self.report(health={})[0]["level"], "fix")         # fixes first

    def test_strong_without_health(self):
        items = {i["name"]: i for i in self.report(health=None)}
        self.assertEqual(items["Simulcast"]["level"], "maybe")

    def test_unknown_attenuator(self):
        item = {"calls": 5, "rssi": -101, "att_known": 0, "att_on": 0, "quality": None}
        self.assertEqual(mon.signal_advice(item)[1], "ok")


def session(label, minute, q, rssi):
    samples = [[i * 0.5, rssi, 4, 1, q, 80, 1, "m"] for i in range(20)]
    ref = {"name": "Simulcast", "kind": "site", "site_index": "3", "system": "Palmetto 800"}
    return {"label": label, "started": f"2026-10-27T12:{minute:02d}:00", "poll_s": 0.5,
            "results": [{"ref": ref, "kind": "site", "samples": samples}]}


class VerdictTests(unittest.TestCase):
    def test_on_wins(self):
        out = mon.attenuator_verdict([session(mon.ATT_OFF, 0, 55, -40), session(mon.ATT_ON, 5, 80, -58),
                                      session(mon.ATT_OFF, 10, 56, -41), session(mon.ATT_ON, 15, 79, -57)])
        self.assertTrue(out[0]["use"])
        self.assertIn("Better with the attenuator", out[0]["verdict"])
        q = next(r for r in out[0]["rows"] if r[0] == "q_med")
        self.assertAlmostEqual(q[1], 55.5)
        rssi = next(r for r in out[0]["rows"] if r[0] == "rssi_med")
        self.assertIsNone(rssi[3])                                          # signal level never decides

    def test_no_difference(self):
        out = mon.attenuator_verdict([session(mon.ATT_OFF, 0, 70, -60), session(mon.ATT_ON, 5, 71, -78)])
        self.assertFalse(out[0]["use"])
        self.assertIn("No clear difference", out[0]["verdict"])


class FakeTester(SimpleNamespace):
    def start(self, refs, label, notes, resume=True):
        self.started.append(label)
        self.running = True
        return True

    def stop(self):
        self.running = False


class TestRunTests(unittest.TestCase):
    def make(self, att="Off"):
        m = SimpleNamespace(connected=True, last_att=att, presses=0)
        m.antenna_test = FakeTester(started=[], running=False, finished=mock.Mock())

        def press(code, why):
            m.presses += 1
            m.last_att = "Off" if m.last_att == "G-Att" else "G-Att"
        m.send_function_long_press = press
        with mock.patch.object(mon.AttenuatorTest, "__init__", lambda self, monitor: None):
            t = mon.AttenuatorTest(m)
        t.monitor, t.tester = m, m.antenna_test
        t.active, t.labels, t.sessions, t.round, t.refs = False, [], [], -1, []
        t.want, t.waited, t.tries, t.original, t.status = None, 0.0, 0, False, ""
        t.timer = mock.Mock()
        t.changed, t.done = mock.Mock(), mock.Mock()
        return m, t

    def run_rounds(self, m, t):
        for _ in range(4):
            for _ in range(8):
                t.tick()
            t.tester.running = False
            with mock.patch.object(mon.QTimer, "singleShot", lambda ms, fn: fn()):
                t.round_done({"label": t.labels[t.round], "started": str(t.round)})

    def test_rounds_alternate_and_restore(self):
        m, t = self.make("Off")
        self.assertTrue(t.start([{"kind": "site"}], 4))
        self.run_rounds(m, t)
        self.assertEqual(m.antenna_test.started, [mon.ATT_OFF, mon.ATT_ON, mon.ATT_OFF, mon.ATT_ON])
        self.assertEqual(m.last_att, "Off")                                  # back as it was
        self.assertFalse(t.active)
        self.assertEqual(len(t.done.emit.call_args[0][0]), 4)

    def test_attenuator_that_will_not_change(self):
        m, t = self.make("Off")
        m.send_function_long_press = lambda code, why: None
        t.start([{"kind": "site"}], 2)
        for _ in range(8):
            t.tick()
        t.tester.running = False
        with mock.patch.object(mon.QTimer, "singleShot", lambda ms, fn: fn()):
            t.round_done({"label": mon.ATT_OFF, "started": "0"})
        for _ in range(60):
            t.tick()
        self.assertFalse(t.active)
        self.assertIn("didn't change", t.status)

    def test_not_connected(self):
        m, t = self.make()
        m.connected = False
        self.assertFalse(t.start([{"kind": "site"}]))


class ProgressTests(TestRunTests):            # v1.1.114: the test shows what it is doing
    def test_progress_and_log(self):
        m, t = self.make("Off")
        t.start([{"kind": "site", "name": "Simulcast", "site_index": "3"}], 4)
        tester = m.antenna_test
        tester.current_ref = lambda: {"kind": "site", "name": "Simulcast", "site_index": "3"}
        tester.running, t.waiting = True, False
        now = mon.time.monotonic()
        tester.phase, tester.deadline = "tune", now + 20
        self.assertIn("waiting for the scanner to reach", t.progress_text())
        tester.phase, tester.sample_start, tester.sample_end = "sample", now - 30, now + 90
        tester.result = {"samples": []}
        self.assertIn("No readings are arriving", t.progress_text())
        tester.result = {"samples": [[1.0, -62, 4, 1, 72, 80, 1, "m"]]}
        self.assertIn("decode quality 72, signal 80, -62 dBm", t.progress_text())
        t.tester_changed()
        t.changed.emit.assert_called()
        t.ref_done({"ref": {"name": "Simulcast"}, "status": "not reached", "samples": [],
                    "message": "The scanner didn't confirm it"})
        self.assertIn("not reached, 0 readings: The scanner didn't confirm it", t.log[-1])

    def test_results_after_each_pair(self):
        m, t = self.make("Off")
        t.start([{"kind": "site"}], 4)
        t.tester.running = False
        with mock.patch.object(mon.QTimer, "singleShot", lambda ms, fn: None):
            t.round_done({"label": mon.ATT_OFF, "started": "0"})
            t.done.emit.assert_not_called()
            t.round_done({"label": mon.ATT_ON, "started": "1"})
        self.assertEqual(len(t.done.emit.call_args[0][0]), 2)


class WindowTests(unittest.TestCase):
    def test_tab(self):
        m = monitor(fresh=True)
        w = mon.LevelCheckWindow(m)
        w.show_signal([{"kind": "site", "system": "Palmetto 800", "name": "Simulcast", "calls": 6, "rssi": -42,
                        "low": -45, "att_known": 6, "att_on": 0, "global": 0, "quality": 45.0,
                        "advice": "Strong", "level": "try"}])
        self.assertIn("1 worth an attenuator test", w.signal_note.text())
        w.att_test.status = "Done."
        w.show_att_result([session(mon.ATT_OFF, 0, 55, -40), session(mon.ATT_ON, 5, 80, -58)])
        self.assertIn("SET IN SDS-MANAGER", w.att_status.text())



class ManagerTests(unittest.TestCase):          # v1.1.113: the attenuator set in SDS-Manager
    def test_requests(self):
        r = mon.attenuator_request(True, system="Palmetto 800", site="Greenville County Simulcast")
        self.assertEqual((r["action"], r["on"], r["frequency"]), ("attenuator", True, ""))
        r = mon.attenuator_request(False, frequency="154.430000MHz", channel="Fire Dispatch")
        self.assertEqual(r["frequency"], "154.430000")

    def test_report_and_verdict_items(self):
        m = monitor(fresh=True)
        w = mon.LevelCheckWindow(m)
        item = {"kind": "channel", "system": "County Fire", "name": "Tac 2 (154.2800)", "site": "",
                "channel": "Tac 2", "frequency": "154.280000MHz", "calls": 5, "rssi": -101, "low": -104,
                "att_known": 5, "att_on": 5, "global": 0, "quality": None, "advice": "Weak", "level": "fix"}
        self.assertEqual(w.item_request(item, False)["frequency"], "154.280000")
        verdict = mon.attenuator_verdict([session(mon.ATT_OFF, 0, 55, -40), session(mon.ATT_ON, 5, 80, -58)])[0]
        self.assertEqual(verdict["ref"]["system"], "Palmetto 800")
        req = w.verdict_request(verdict)
        self.assertEqual((req["on"], req["system"]), (True, "Palmetto 800"))
        with mock.patch.object(mon, "edit_in_manager", return_value="Sent to SDS-Manager.") as sent:
            w.send_attenuator(req)
        self.assertEqual(sent.call_args[0][0]["action"], "attenuator")

    def test_edit_in_manager_lets_it_through(self):
        import sys
        socket = mock.Mock()
        socket.return_value.waitForConnected.return_value = True
        net = mock.Mock(QLocalSocket=socket)
        with mock.patch.dict(sys.modules, {"PyQt6.QtNetwork": net}):
            self.assertEqual(mon.edit_in_manager(mon.attenuator_request(True, system="P", site="S")),
                             "Sent to SDS-Manager.")
        sent = socket.return_value.write.call_args[0][0]
        self.assertIn(b'"action": "attenuator"', sent)


if __name__ == "__main__":
    unittest.main()
