# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""GQRX as a second receiver (v1.1.95): remote control, squelch receptions, UDP audio, bookmarks."""
import json
import shutil
import socket
import socketserver
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from support import mon, monitor, HOME

BOOKMARKS = """# Tag name          ;  color
Untagged            ; #c0c0c0
Amateur             ; #ff0000
Aviation            ; #00ff00

# Frequency ; Name                     ; Modulation          ;  Bandwidth; Tags
     7200000; 40m SSB net              ; USB                 ;       2800; Amateur
     5696000; Coast Guard HF           ; USB                 ;       2800; Untagged
   121500000; Guard                    ; AM                  ;      10000; Aviation,Untagged
"""


class FakeGqrx(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self):
        self.hz, self.mode, self.level, self.sql = 7200000, "USB", -60.0, -70.0
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        g = self.server
        for line in self.rfile:
            cmd = line.decode().strip()
            if cmd == "f":
                out = f"{g.hz}\n"
            elif cmd == "m":
                out = f"{g.mode}\n2800\n"
            elif cmd == "l STRENGTH":
                out = f"{g.level}\n"
            elif cmd == "l SQL":
                out = f"{g.sql}\n"
            elif cmd.startswith("F "):
                g.hz = int(cmd[2:])
                out = "RPRT 0\n"
            elif cmd.startswith("M "):
                g.mode = cmd.split()[1]
                out = "RPRT 0\n"
            else:
                out = "RPRT 1\n"
            self.wfile.write(out.encode())


class PartsTests(unittest.TestCase):
    def test_bookmarks(self):
        path = HOME / "bookmarks_test.csv"
        path.write_text(BOOKMARKS)
        marks = mon.gqrx_bookmarks(path)
        self.assertEqual([m["name"] for m in marks], ["40m SSB net", "Coast Guard HF", "Guard"])
        self.assertEqual(marks[2]["tags"], ["Aviation", "Untagged"])
        self.assertEqual(mon.gqrx_bookmark_for(7200500, marks)["name"], "40m SSB net")
        self.assertIsNone(mon.gqrx_bookmark_for(7210000, marks))
        self.assertEqual(mon.gqrx_bookmark_for(121503000, marks)["name"], "Guard")      # inside 10 kHz
        self.assertEqual(mon.gqrx_bookmarks(HOME / "missing.csv"), [])

    def test_channel_text(self):
        self.assertEqual(mon.gqrx_channel_text(7200000, "USB"), "7200.0 kHz USB")
        self.assertEqual(mon.gqrx_channel_text(146520000, "FM"), "146.5200 MHz FM")
        self.assertEqual(mon.gqrx_frequency_text(7200000), "7.200000MHz")


class TrackerTests(unittest.TestCase):
    t0 = datetime(2026, 11, 18, 21, 0, 0)

    def run_levels(self, tracker, levels, hz=7200000, sql=-70.0, step=0.25):
        done = []
        for i, level in enumerate(levels):
            done += tracker.update(self.t0 + timedelta(seconds=i * step), hz, "USB", level, sql)
        return done

    def test_open_then_hang(self):
        tr = mon.SquelchTracker(hang=2.0, shortest=1.0)
        # 3 s open, a 1 s dip (inside the hang), 1 s open, then quiet.
        levels = [-60] * 12 + [-80] * 4 + [-60] * 4 + [-80] * 12
        done = self.run_levels(tr, levels)
        self.assertEqual(len(done), 1)
        rec = done[0]
        self.assertEqual(rec["start"], self.t0)
        self.assertEqual(rec["end"], self.t0 + timedelta(seconds=19 * 0.25))
        self.assertEqual(rec["peak"], -60)

    def test_too_short_is_dropped(self):
        tr = mon.SquelchTracker(hang=1.0, shortest=1.0)
        self.assertEqual(self.run_levels(tr, [-60] * 2 + [-80] * 10), [])

    def test_squelch_off_logs_nothing(self):
        tr = mon.SquelchTracker()
        self.assertEqual(self.run_levels(tr, [-60] * 40, sql=mon.GQRX_SQUELCH_OFF), [])
        self.assertIsNone(tr.current)

    def test_retune_and_longest(self):
        tr = mon.SquelchTracker(hang=2.0, shortest=0.5, longest=5.0)
        done = self.run_levels(tr, [-60] * 8)
        done += tr.update(self.t0 + timedelta(seconds=2.25), 7250000, "USB", -60, -70)   # retuned
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["hz"], 7200000)
        tr2 = mon.SquelchTracker(hang=2.0, shortest=0.5, longest=5.0)
        pieces = self.run_levels(tr2, [-60] * 60) + tr2.finish()          # 15 s broadcast
        self.assertEqual(len(pieces), 3)


class AudioTests(unittest.TestCase):
    def test_segment(self):
        a = mon.GqrxAudio(keep=10)
        t0 = datetime(2026, 11, 18, 21, 0, 0)
        for i in range(16):
            a.add(t0 + timedelta(seconds=i * 0.5), bytes([i]) * 4)
        part = a.segment(t0 + timedelta(seconds=5), t0 + timedelta(seconds=6), before=0.5, after=0.5)
        self.assertEqual(part, b"".join(bytes([i]) * 4 for i in range(9, 14)))
        for i in range(16, 40):
            a.add(t0 + timedelta(seconds=i * 0.5), bytes([i]) * 4)
        self.assertLessEqual((a.chunks[-1][0] - a.chunks[0][0]).total_seconds(), 10)     # only 10 s kept

    def test_receives_udp(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        a = mon.GqrxAudio(port)
        self.assertTrue(a.start())
        try:
            out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for _ in range(20):
                out.sendto(b"\x00\x01" * 100, ("127.0.0.1", port))
                if a.receiving():
                    break
                threading.Event().wait(0.05)
            out.close()
            self.assertTrue(a.receiving())
        finally:
            a.stop()


class LinkTests(unittest.TestCase):
    def test_status_and_errors(self):
        server = FakeGqrx()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            link = mon.GqrxLink("127.0.0.1", server.server_address[1])
            self.assertEqual(link.status(), (7200000, "USB", -60.0, -70.0))
            self.assertEqual(link.command("F 7100000"), ["RPRT 0"])
            self.assertEqual(link.status()[0], 7100000)
            with self.assertRaises(OSError):
                link.command("X nonsense")
            link.close()
        finally:
            server.shutdown()
            server.server_close()


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.w = monitor(fresh=True)
        self.w.history_rows = []
        self.r = self.w.gqrx
        self.sent = []
        self.w.rdio.call_saved = lambda path, row: self.sent.append(row)
        self.saved = []
        self.r.saved = type("S", (), {"emit": lambda _s, *a: self.saved.append(a)})()
        mon.GQRX_BOOKMARKS.parent.mkdir(parents=True, exist_ok=True)
        mon.GQRX_BOOKMARKS.write_text(BOOKMARKS)
        self.start = datetime.now().replace(microsecond=0) - timedelta(minutes=3)

    def tearDown(self):
        mon.GQRX_BOOKMARKS.unlink(missing_ok=True)

    def test_reception_goes_into_history_with_recording(self):
        self.r.audio = mon.GqrxAudio()
        t = self.start
        for i in range(30):                                   # 3 s of 48 kHz audio in 0.1 s packets
            self.r.audio.add(t + timedelta(seconds=i * 0.1), b"\x10\x00" * 4800)
        rec = {"start": t, "end": t + timedelta(seconds=2.5), "last": t, "hz": 7200300, "mode": "USB", "peak": -55.0}
        self.r.reception(rec, wait=True)
        rows = [r for r in mon.read_reception_day(f"{t:%Y-%m-%d}") if r[0] == f"{t:%Y-%m-%d %H:%M:%S}"
                and r[3] == "GQRX"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row[4], row[5], row[6]), ("Amateur", "40m SSB net", "7.200300MHz"))
        self.assertEqual(mon.call_source(row), mon.SOURCE_GQRX)
        self.assertEqual(len(self.saved), 1)
        path, audio_row = self.saved[0]
        self.assertTrue(Path(path).exists())
        self.assertEqual(mon.find_recording(row), Path(path))
        self.assertEqual(audio_row[10], "USB")
        self.r.recording_saved(path, audio_row)
        self.assertEqual(self.sent, [])                       # not sent on to Rdio Scanner
        self.r.audio = None

    def test_no_bookmark_and_no_audio(self):
        self.r.audio = None
        t = self.start + timedelta(seconds=30)
        self.r.reception({"start": t, "end": t + timedelta(seconds=4), "last": t, "hz": 9580000, "mode": "AM",
                          "peak": -40.0}, wait=True)
        row = [r for r in mon.read_reception_day(f"{t:%Y-%m-%d}") if r[5] == "9580.0 kHz AM"][0]
        self.assertEqual(row[4], "Shortwave")
        self.assertIsNone(mon.find_recording(row))
        self.assertIn("no audio", self.r.last_text)

    def test_status_text_and_report(self):
        self.w.settings.setValue("gqrx/enabled", "true")
        self.r.reading = {"hz": 7200000, "mode": "USB", "level": -60.0, "sql": mon.GQRX_SQUELCH_OFF,
                          "open": False, "when": datetime.now()}
        self.assertIn("squelch is off", self.r.status_text())
        self.assertEqual(mon.report_sources_text({mon.SOURCE_SCANNER: 4, mon.SOURCE_GQRX: 2}),
                         "Scanner only 4 • GQRX 2")


class LiveTests(unittest.TestCase):
    def test_reads_gqrx_and_logs_a_reception(self):
        server = FakeGqrx()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        w = monitor(fresh=True)
        s = w.settings
        for key, value in (("enabled", "true"), ("port", str(server.server_address[1])), ("audio", "false"),
                           ("hang", "0.3"), ("shortest", "0.2"), ("poll_ms", "50")):
            s.setValue(f"gqrx/{key}", value)
        r = w.gqrx
        got, readings = [], []
        r.finished = type("S", (), {"emit": lambda _s, rec: got.append(rec)})()
        r.state = type("S", (), {"emit": lambda _s, rd: readings.append(rd)})()
        try:
            r.start()
            threading.Event().wait(0.6)                       # signal above squelch
            server.level = -90.0
            for _ in range(40):
                if got:
                    break
                threading.Event().wait(0.05)
        finally:
            r.running = False
            r.thread.join(timeout=3)
            server.shutdown()
            server.server_close()
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["hz"], got[0]["mode"]), (7200000, "USB"))
        self.assertTrue(any(rd.get("open") for rd in readings))

    def test_dialog_and_menu(self):
        w = monitor(fresh=True)
        d = mon.GqrxDialog(w)
        d.save()
        d.disconnect_receiver()
        w.gqrx.shutdown()
        with open(mon.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('("gqrx", "GQRX", "receivers")', source)          # v1.2.0: a Settings page
        self.assertIn("self.gqrx.shutdown()", source)
        json.dumps(w.gqrx.counts)


class Recorder:
    def __init__(self):
        self.items = []

    def emit(self, *args):
        self.items.append(args[0] if len(args) == 1 else args)

    def connect(self, *args):
        pass


def wait_for(check, seconds=3.0):
    for _ in range(int(seconds / 0.05)):
        if check():
            return True
        threading.Event().wait(0.05)
    return check()


class TuneTests(unittest.TestCase):
    """v1.1.96: Tune GQRX Here and following the scanner."""

    def setUp(self):
        self.server = FakeGqrx()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.w = monitor(fresh=True)
        self.w.settings.setValue("gqrx/port", str(self.server.server_address[1]))
        self.w.settings.setValue("gqrx/audio", "false")
        self.r = self.w.gqrx
        self.r.note = Recorder()

    def tearDown(self):
        self.r.running = False
        if self.r.thread is not None:
            self.r.thread.join(timeout=3)
        self.server.shutdown()
        self.server.server_close()

    def test_modes(self):
        self.assertEqual(mon.gqrx_mode_for(7.2), "AM")
        self.assertEqual(mon.gqrx_mode_for(7.2, "USB"), "USB")
        self.assertEqual(mon.gqrx_mode_for(121.5), "AM")
        self.assertEqual(mon.gqrx_mode_for(155.34), "FM")
        self.assertEqual(mon.gqrx_mode_for(98.1), "WFM")
        self.assertEqual(mon.gqrx_mode_for(851.0125, "P25"), "FM")
        self.assertEqual(mon.gqrx_mode_for(121.5, "AUTO"), "AM")
        self.assertEqual(mon.gqrx_mode_for(154.0, "NFM"), "FM")

    def test_tune_without_logging(self):
        self.r.tune(7.25, "USB")                               # nothing running: its own connection
        self.assertTrue(wait_for(lambda: self.server.hz == 7250000 and self.server.mode == "USB"))
        self.assertTrue(wait_for(lambda: self.r.note.items))
        self.assertIn("7.2500 MHz USB", self.r.note.items[-1])

    def test_unreachable(self):
        self.w.settings.setValue("gqrx/port", "1")
        self.r.tune(7.25)
        self.assertTrue(wait_for(lambda: self.r.note.items))
        self.assertIn("not reachable", self.r.note.items[-1])

    def test_follow_the_scanner(self):
        s = self.w.settings
        s.setValue("gqrx/follow", "true")
        s.setValue("gqrx/enabled", "true")
        s.setValue("gqrx/poll_ms", "50")
        self.r.state = Recorder()
        self.r.finished = Recorder()
        self.r.start()
        self.assertFalse(self.r.log_now)                       # not logged while following
        self.r.follow_scanner({"Frequency": "155.340000MHz", "Talkgroup": "", "Mode": "NFM"})
        self.assertTrue(wait_for(lambda: self.server.hz == 155340000 and self.server.mode == "FM"))
        self.r.follow_scanner({"Frequency": "853.862500MHz", "Talkgroup": "TGID:44381", "Mode": "P25"})
        threading.Event().wait(0.3)
        self.assertEqual(self.server.hz, 155340000)            # trunked: not followed
        s.setValue("gqrx/follow_trunked", "true")
        self.r.follow_scanner({"Frequency": "853.862500MHz", "Talkgroup": "TGID:44381", "Mode": "P25"})
        self.assertTrue(wait_for(lambda: self.server.hz == 853862500))
        self.server.level, self.server.sql = -50.0, -70.0      # GQRX hears it: still nothing logged
        threading.Event().wait(0.4)
        self.server.level = -90.0
        threading.Event().wait(2.5)
        self.assertEqual(self.r.finished.items, [])
        self.assertIn("Following the scanner", self.r.status_text())

    def test_not_following(self):
        self.r.follow_scanner({"Frequency": "155.340000MHz", "Talkgroup": "", "Mode": "NFM"})
        threading.Event().wait(0.3)
        self.assertEqual(self.server.hz, 7200000)

    def test_menus(self):
        action = self.w.gqrx_action(mon.QMenu(), 7.2, "")
        self.assertIsNotNone(action)
        self.assertIsNone(self.w.gqrx_action(mon.QMenu(), None))
        with open(mon.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(source.count("gqrx_action(menu"), 5)    # history, Discovery, Close Call, search, info
        self.assertIn("gqrx_requested.connect", source)


if __name__ == "__main__":
    unittest.main()
