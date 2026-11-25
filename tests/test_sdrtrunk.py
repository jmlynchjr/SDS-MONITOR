# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""SDRTrunk as a second receiver (v1.1.94): Rdio Scanner-style uploads, merging with the scanner's calls."""
import csv
import json
import socket
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from support import mon, monitor

FRAME = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)     # MPEG-1 layer III, 128 kbit/s, 44.1 kHz: 417 bytes


def mp3(seconds):
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + FRAME * int(round(seconds * 44100 / 1152))


def form(fields, audio=None, name="call.mp3"):
    if audio is None:
        boundary = "----t"
        body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                       for k, v in fields.items()) + f"--{boundary}--\r\n"
        return body.encode(), f"multipart/form-data; boundary={boundary}"
    return mon.multipart_body(fields, "audio", name, audio)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Recorder:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)

    def connect(self, *args):
        pass


def make_call(start, talkgroup=44381, seconds=4.0, radio=3235401, system="1", label="", group="", alias=""):
    return {"received": start + timedelta(seconds=seconds + 2), "start": start, "seconds": seconds,
            "system": system, "system_label": "", "talkgroup": talkgroup, "talkgroup_label": label,
            "talkgroup_group": group, "talkgroup_tag": "", "radio": radio, "talker_alias": alias,
            "frequency": 853862500, "patches": "", "audio": mp3(seconds), "audio_name": "x.mp3",
            "audio_type": "audio/mpeg"}


def scanner_row(start, seconds=4, talkgroup=44381, channel=None, system="Palmetto 800"):
    channel = channel or f"Dispatch {talkgroup}"         # each test its own channel (the data folder is shared)
    end = start + timedelta(seconds=seconds)
    return [f"{start:%Y-%m-%d %H:%M:%S}", f"{end:%Y-%m-%d %H:%M:%S}", f"{seconds:.1f}", system, "Greer PD",
            channel, "853.862500MHz", f"TGID:{talkgroup}", "-55"]


class UploadFormatTests(unittest.TestCase):
    def test_multipart_fields_and_file(self):
        body, ctype = form({"key": "k", "talkgroup": "44381", "talkgroupLabel": "Greer PD Dispatch"}, b"ID3abc")
        fields, files = mon.parse_upload(ctype, body)
        self.assertEqual(fields["talkgroup"], "44381")
        self.assertEqual(fields["talkgroupLabel"], "Greer PD Dispatch")
        self.assertEqual(files["audio"], ("call.mp3", "audio/mpeg", b"ID3abc"))

    def test_urlencoded_fields(self):
        fields, files = mon.parse_upload("application/x-www-form-urlencoded", b"key=k&test=1&system=2")
        self.assertEqual(fields, {"key": "k", "test": "1", "system": "2"})
        self.assertEqual(files, {})

    def test_replies_like_rdio_scanner_6(self):
        audio = {"audio": ("a.mp3", "audio/mpeg", mp3(1))}
        self.assertEqual(mon.upload_reply({"key": "bad", "talkgroup": "1"}, audio, "good")[:2],
                         (401, "Invalid API key"))
        # SDRTrunk's connection test: it only accepts a reply that says "no talkgroup".
        code, text, ok = mon.upload_reply({"key": "good", "system": "1", "test": "1"}, {}, "good")
        self.assertEqual((code, ok), (417, False))
        self.assertIn("no talkgroup", text)
        self.assertEqual(mon.upload_reply({"key": "good", "talkgroup": "5"}, {}, "good")[:2],
                         (417, "Incomplete call data: no audio"))
        self.assertEqual(mon.upload_reply({"key": "good", "talkgroup": "5"}, audio, "good"),
                         (200, "Call imported successfully.", True))
        self.assertFalse(mon.upload_reply({"key": ""}, audio, "")[2])      # no key set: nothing is taken

    def test_times(self):
        when = datetime(2026, 11, 18, 21, 5, 30)
        stamp = when.timestamp()
        self.assertEqual(mon.upload_time(str(int(stamp))), when)
        self.assertEqual(mon.upload_time(str(int(stamp * 1000))), when)
        self.assertEqual(mon.upload_time(when.astimezone().isoformat()), when)
        self.assertEqual(mon.upload_time("", when), when)
        self.assertEqual(mon.upload_time("not a time", when), when)

    def test_mp3_length(self):
        self.assertAlmostEqual(mon.mp3_seconds(mp3(3.0)), 3.0, delta=0.1)
        self.assertEqual(mon.mp3_seconds(b"not audio at all"), 0.0)

    def test_call_from_upload(self):
        stamp = int(datetime(2026, 11, 18, 21, 5, 30).timestamp())
        fields = {"key": "k", "system": "3", "talkgroup": "44381", "source": "3235401", "frequency": "853862500",
                  "dateTime": str(stamp), "talkgroupLabel": "Greer PD Dispatch", "talkgroupGroup": "Greer PD",
                  "talkerAlias": "Engine 12"}
        call = mon.upload_call(fields, {"audio": ("c.mp3", "audio/mpeg", mp3(2))})
        self.assertEqual((call["talkgroup"], call["radio"], call["frequency"], call["system"]),
                         (44381, 3235401, 853862500, "3"))
        self.assertEqual(call["start"], datetime(2026, 11, 18, 21, 5, 30))
        self.assertAlmostEqual(call["seconds"], 2.0, delta=0.1)
        self.assertEqual(call["talker_alias"], "Engine 12")
        self.assertIsNone(mon.upload_call({"source": "0"}, {"audio": ("c", "", b"")})["radio"])

    def test_arrivals_hide_the_key(self):
        rec = mon.arrival_record({"key": "secret", "talkgroup": "5"}, {"audio": ("a.mp3", "audio/mpeg", mp3(1))},
                                 200, "ok")
        self.assertNotIn("secret", json.dumps(rec))
        self.assertEqual(rec["fields"]["talkgroup"], "5")
        text = mon.arrivals_text([rec])
        self.assertIn("talkgroup = 5", text)
        self.assertIn("a.mp3", text)


class MatchTests(unittest.TestCase):
    start = datetime(2026, 11, 18, 21, 0, 0)

    def test_same_talkgroup_and_time(self):
        rows = [(scanner_row(self.start), [])]
        self.assertIs(mon.match_scanner_call(make_call(self.start + timedelta(seconds=1)), rows), rows[0])

    def test_other_talkgroup_or_time(self):
        rows = [(scanner_row(self.start), [])]
        self.assertIsNone(mon.match_scanner_call(make_call(self.start, talkgroup=1), rows))
        self.assertIsNone(mon.match_scanner_call(make_call(self.start + timedelta(seconds=30)), rows))

    def test_nearest_start_wins_and_system(self):
        a = (scanner_row(self.start), [])
        b = (scanner_row(self.start + timedelta(seconds=6)), [])
        call = make_call(self.start + timedelta(seconds=5), seconds=2)
        self.assertIs(mon.match_scanner_call(call, [a, b]), b)
        self.assertIsNone(mon.match_scanner_call(call, [a, b], system="Other System"))
        self.assertIs(mon.match_scanner_call(call, [a, b], system="Palmetto 800"), b)

    def test_scanner_call_holding_several_sdrtrunk_calls(self):
        rows = [(scanner_row(self.start, seconds=20), [])]
        self.assertIs(mon.match_scanner_call(make_call(self.start + timedelta(seconds=12)), rows), rows[0])


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.w = monitor(fresh=True)
        self.w.connected = False
        self.w.history_rows = []
        s = self.w.settings
        s.setValue("sdrtrunk/mode", "add")
        s.setValue("sdrtrunk/duplicates", "merge")
        self.r = self.w.sdrtrunk
        self.sent = []
        self.w.rdio.call_saved = lambda path, row: self.sent.append(row)
        self.day = datetime.now().replace(microsecond=0) - timedelta(minutes=5)

    def history(self):
        return mon.read_reception_day(f"{self.day:%Y-%m-%d}")

    def test_call_only_sdrtrunk_heard_is_added(self):
        call = make_call(self.day, talkgroup=777, label="County Fire", group="Fire", alias="Engine 12")
        self.r.take(call)
        rows = [r for r in self.history() if r[7] == "TGID:777"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row[:2], [f"{self.day:%Y-%m-%d %H:%M:%S}", f"{self.day + timedelta(seconds=4):%Y-%m-%d %H:%M:%S}"])
        self.assertEqual((row[4], row[5], row[6]), ("Fire", "County Fire", "853.862500MHz"))
        self.assertEqual(mon.call_source(row), mon.SOURCE_SDRTRUNK)
        self.assertEqual(mon.units_for_reception(row), ["3235401"])
        rec = mon.find_recording(row)
        self.assertIsNotNone(rec)
        self.assertTrue(rec.name.endswith("_sdrtrunk.mp3"))
        self.assertEqual(self.sent, [])                         # not sent on to Rdio Scanner
        self.assertEqual(self.w.history_rows[0], row)
        recs = [r for r in mon.receptions_with_units(f"{self.day:%Y-%m-%d}") if r["talkgroup"] == "TGID:777"]
        self.assertEqual(recs[0]["source"], mon.SOURCE_SDRTRUNK)

    def test_forward_to_rdio_when_asked(self):
        self.w.settings.setValue("sdrtrunk/forward_rdio", "true")
        self.r.take(make_call(self.day, talkgroup=778))
        self.assertEqual(len(self.sent), 1)

    def test_merged_into_the_scanners_call(self):
        row = scanner_row(self.day, talkgroup=779)
        self.w.scanner_calls.append((row, ["UID:1111"]))
        self.w.write_history_row(row)
        self.r.take(make_call(self.day + timedelta(seconds=1), talkgroup=779))
        self.assertEqual(len([r for r in self.history() if r[7] == "TGID:779"]), 1)     # not a second call
        self.assertEqual(mon.call_source(row), mon.SOURCE_BOTH)
        self.assertEqual(set(mon.units_for_reception(row)), {"3235401"})        # the radio the scanner missed
        # The scanner had no recording, so SDRTrunk's becomes the call's recording.
        self.assertIsNotNone(mon.find_recording(row))
        self.assertEqual(mon.second_recordings(row), [])
        self.assertEqual(self.r.counts["merged"], 1)

    def test_second_recording_when_the_scanner_has_one(self):
        row = scanner_row(self.day, talkgroup=780)
        self.w.scanner_calls.append((row, []))
        own = mon.audio_day_dir(self.day) / "own.mp3"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_bytes(mp3(1))
        self.r.index_audio(row[:8] + ["—", "—", "P25", "-55", "4.0", "own.mp3"])
        self.r.take(make_call(self.day, talkgroup=780))
        self.assertEqual(mon.find_recording(row), own)
        extra = mon.second_recordings(row)
        self.assertEqual(len(extra), 1)
        self.assertTrue(extra[0].name.endswith("_sdrtrunk.mp3"))

    def test_waits_for_the_scanners_copy(self):
        self.w.connected = True
        call = make_call(self.day, talkgroup=781)
        call["received"] = datetime.now()
        self.r.take(call)
        self.assertEqual(len(self.r.pending), 1)                # the scanner may still log it
        self.assertEqual([r for r in self.history() if r[7] == "TGID:781"], [])
        row = scanner_row(self.day, talkgroup=781)
        self.w.scanner_calls.append((row, []))
        self.r.process()
        self.assertEqual(self.r.pending, [])
        self.assertEqual(mon.call_source(row), mon.SOURCE_BOTH)

    def test_added_after_waiting(self):
        self.w.connected = True
        call = make_call(self.day, talkgroup=782)
        call["received"] = datetime.now() - timedelta(seconds=mon.SDRTRUNK_WAIT_SECONDS + 1)
        self.r.take(call)
        self.assertEqual(self.r.pending, [])
        self.assertEqual(len([r for r in self.history() if r[7] == "TGID:782"]), 1)

    def test_keep_both(self):
        self.w.settings.setValue("sdrtrunk/duplicates", "keep")
        row = scanner_row(self.day, talkgroup=783)
        self.w.scanner_calls.append((row, []))
        self.w.write_history_row(row)
        self.r.take(make_call(self.day, talkgroup=783))
        self.assertEqual(len([r for r in self.history() if r[7] == "TGID:783"]), 2)

    def test_record_mode_adds_nothing(self):
        self.w.settings.setValue("sdrtrunk/mode", "record")
        self.r.take(make_call(self.day, talkgroup=784))
        self.assertEqual([r for r in self.history() if r[7] == "TGID:784"], [])
        self.assertEqual(self.r.counts["received"], 1)

    def test_names_from_the_scanner(self):
        self.w.history_rows = [scanner_row(self.day, talkgroup=785, channel="Greer FD")]
        system, department, channel = self.r.names_for(make_call(self.day, talkgroup=785))
        self.assertEqual((department, channel), ("Greer PD", "Greer FD"))
        self.w.settings.setValue("sdrtrunk/systems", json.dumps({"1": "Palmetto 800"}))
        self.assertEqual(self.r.system_name(make_call(self.day)), "Palmetto 800")

    def test_report_counts_receivers(self):
        self.assertIsNone(mon.report_sources_text({mon.SOURCE_SCANNER: 9}))
        self.assertEqual(mon.report_sources_text({mon.SOURCE_SCANNER: 9, mon.SOURCE_BOTH: 3}),
                         "Scanner only 9 • heard by both 3")
        self.r.take(make_call(self.day, talkgroup=786))
        data = mon.daily_report_data(f"{self.day:%Y-%m-%d}")
        self.assertGreaterEqual(data["sources"].get(mon.SOURCE_SDRTRUNK, 0), 1)
        self.assertIn("SDRTrunk only", mon.daily_report_html(data))


class DialogTests(unittest.TestCase):
    def test_dialog_opens_and_saves(self):
        w = monitor(fresh=True)
        w.settings.setValue("sdrtrunk/seen", json.dumps({"1": "County P25"}))
        d = mon.SdrTrunkDialog(w)
        typed = "1 = County P25\n2 = Palmetto 800\nnonsense"
        d.systems = type("Box", (), {"toPlainText": lambda self: typed})()
        self.assertEqual(d.parse_systems(), {"1": "County P25", "2": "Palmetto 800"})
        d.save()
        # A name SDRTrunk sent itself isn't kept as a choice; the one typed is.
        self.assertEqual(json.loads(w.settings.value("sdrtrunk/systems")), {"2": "Palmetto 800"})
        d.disconnect_receiver()

    def test_menu_entry_and_shutdown(self):
        with open(mon.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('("sdrtrunk", "SDRTrunk", "receivers")', source)  # v1.2.0: a Settings page
        self.assertIn("self.sdrtrunk.shutdown()", source)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.w = monitor(fresh=True)
        s = self.w.settings
        s.setValue("sdrtrunk/enabled", "true")
        s.setValue("sdrtrunk/mode", "record")
        s.setValue("sdrtrunk/key", "k123")
        s.setValue("sdrtrunk/port", str(free_port()))
        self.r = self.w.sdrtrunk
        self.r.arrived = Recorder()
        self.r.start()
        self.assertTrue(self.r.listening(), self.r.problem)
        mon.SDRTRUNK_ARRIVALS.unlink(missing_ok=True)

    def tearDown(self):
        self.r.shutdown()

    def post(self, body, ctype):
        req = urllib.request.Request(self.r.address(), data=body, method="POST",
                                     headers={"Content-Type": ctype, "User-Agent": "sdrtrunk"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_sdrtrunk_connection_test_passes(self):
        code, text = self.post(*form({"key": "k123", "system": "1", "test": "1"}))
        self.assertEqual(code, 417)
        self.assertIn("no talkgroup", text)
        self.assertTrue(self.r.arrived.calls[0][0]["test"])

    def test_call_is_taken_and_logged(self):
        stamp = int(datetime.now().timestamp())
        code, text = self.post(*form({"key": "k123", "system": "1", "talkgroup": "44381", "source": "3235401",
                                      "dateTime": str(stamp)}, mp3(2)))
        self.assertEqual((code, text.strip()), (200, "Call imported successfully."))
        call = self.r.arrived.calls[0][0]
        self.assertEqual(call["talkgroup"], 44381)
        arrivals = mon.read_arrivals()
        self.assertEqual(arrivals[0]["fields"]["talkgroup"], "44381")
        self.assertNotEqual(arrivals[0]["fields"]["key"], "k123")
        self.assertTrue(arrivals[0]["saved"].endswith(".mp3"))       # record mode keeps the MP3 to check

    def test_wrong_key(self):
        code, _text = self.post(*form({"key": "nope", "talkgroup": "1"}, mp3(1)))
        self.assertEqual(code, 401)
        self.assertEqual(self.r.arrived.calls, [])


if __name__ == "__main__":
    unittest.main()
