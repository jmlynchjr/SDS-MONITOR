# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""Exporting what the scanner heard to GQRX's bookmarks (v1.1.97)."""
import csv
import json
import unittest
from datetime import datetime, timedelta

from support import mon, monitor, HOME

USER_FILE = """# Tag name          ;  color
Untagged            ; #c0c0c0
Amateur             ; #ff0000

# Frequency ; Name                     ; Modulation          ;  Bandwidth; Tags
     7200000; 40m SSB net              ; USB                 ;       2800; Amateur
   154430000; My fire channel          ; Narrow FM           ;      10000; Untagged
"""


def write_history(day, rows):
    path = mon.daily_log_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(mon.HEADERS)
        w.writerows(rows)


def row(day, hhmmss, system, department, channel, freq, talkgroup="—"):
    t = f"{day:%Y-%m-%d} {hhmmss}"
    return [t, t, "3.0", system, department, channel, freq, talkgroup, "-60"]


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        monitor(fresh=True)
        self.day = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        write_history(self.day, [
            row(self.day, "10:00:00", "Greenville County Fire", "Fire", "Fire Dispatch", "154.430000MHz"),
            row(self.day, "10:01:00", "Greenville County Fire", "Fire", "Fire Dispatch", "154.430000MHz"),
            row(self.day, "10:02:00", "Close Call Only", "—", "155.3400", "155.340000MHz"),
            row(self.day, "10:03:00", "Close Call Only", "—", "462.5625", "462.562500MHz"),
            row(self.day, "10:04:00", "Custom Search", "Air", "121.9000", "121.900000MHz"),
            row(self.day, "10:05:00", "Palmetto 800", "Greer PD", "Dispatch", "853.862500MHz", "TGID:44381"),
            row(self.day, "10:06:00", "Close Call Only", "—", "151.2800", "151.280000MHz"),
            row(self.day, "10:07:00", "Greenville County Fire", "Fire", "Fireground 2", "151.280000MHz"),
        ])
        mon.CC_FREQ_FILE.write_text(json.dumps({"155.3400": {"name": "Ambulance ops", "status": "known"},
                                                "462.5625": {"name": "", "status": "ignored"}}))
        index = mon.audio_index_path(self.day)
        index.parent.mkdir(parents=True, exist_ok=True)
        with index.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(mon.AUDIO_HEADERS)
            w.writerow([f"{self.day:%Y-%m-%d} 10:04:00", "", "", "Custom Search", "Air", "121.9000", "121.900000MHz",
                        "—", "—", "—", "AM", "-60", "3.0", "x.mp3"])

    def test_candidates(self):
        found = {e["hz"]: e for e in mon.bookmark_candidates(30)}
        self.assertEqual(found[154430000]["name"], "Fire Dispatch")
        self.assertEqual(found[154430000]["tags"], ["SDS Greenville County Fire"])
        self.assertEqual(found[154430000]["heard"], 2)
        self.assertEqual(found[155340000]["name"], "Ambulance ops")           # your Close Call name
        self.assertEqual(found[155340000]["tags"], ["SDS Close Call"])
        self.assertNotIn(462562500, found)                                   # ignored
        self.assertNotIn(853862500, found)                                   # trunked
        self.assertEqual(found[121900000]["mode"], "AM")
        self.assertEqual(found[121900000]["tags"], ["SDS Search hits"])
        self.assertEqual(found[151280000]["name"], "Fireground 2")           # the channel name beats an unnamed hit
        only_cc = mon.bookmark_candidates(30, ("close_call",))
        self.assertEqual({e["hz"] for e in only_cc}, {155340000, 151280000})

    def test_merge_keeps_the_users_bookmarks(self):
        entries = mon.bookmark_candidates(30)
        text, added, replaced, skipped = mon.merge_gqrx_bookmarks(USER_FILE, entries)
        self.assertIn("40m SSB net", text)
        self.assertIn("My fire channel", text)
        self.assertEqual(skipped, 1)                                         # 154.43 is bookmarked already
        self.assertEqual(added, len(entries) - 1)
        self.assertEqual(replaced, 0)
        self.assertIn("SDS Close Call      ; #ff8c00", text)
        marks = mon.gqrx_bookmarks_from_text(text)
        names = {m["name"] for m in marks}
        self.assertTrue({"Ambulance ops", "Fireground 2", "40m SSB net"} <= names)
        amb = [m for m in marks if m["name"] == "Ambulance ops"][0]
        self.assertEqual((amb["hz"], amb["mode"], amb["bandwidth"]), (155340000, "Narrow FM", 10000))
        # Exporting again replaces the earlier export and adds nothing twice.
        again, added2, replaced2, _ = mon.merge_gqrx_bookmarks(text, entries)
        self.assertEqual((added2, replaced2), (added, added))
        self.assertEqual(again.count("Ambulance ops"), 1)
        self.assertEqual(again.count("SDS Close Call      ;"), 1)

    def test_new_file_and_backup(self):
        path = HOME / "gqrx_test" / "bookmarks.csv"
        added, _r, _s, backup = mon.export_gqrx_bookmarks(mon.bookmark_candidates(30), path)
        self.assertIsNone(backup)
        self.assertGreater(added, 0)
        self.assertEqual(len(mon.gqrx_bookmarks(path)), added)
        added2, replaced2, _s2, backup2 = mon.export_gqrx_bookmarks(mon.bookmark_candidates(30), path)
        self.assertTrue(backup2.exists())
        self.assertEqual(replaced2, added)

    def test_line_format(self):
        line = mon.gqrx_bookmark_line(7200000, "Net; evening", "USB", ["SDS Search hits"])
        parts = [p.strip() for p in line.split(";")]
        self.assertEqual(parts, ["7200000", "Net, evening", "USB", "2800", "SDS Search hits"])

    def test_dialog(self):
        d = mon.GqrxBookmarksDialog(None)
        self.assertGreater(len(d.entries), 0)
        original = mon.gqrx_running
        mon.gqrx_running = lambda: True
        try:
            d.export()                                  # refused while GQRX runs: nothing written
        finally:
            mon.gqrx_running = original
        self.assertFalse(mon.GQRX_BOOKMARKS.exists())
        self.assertIsInstance(mon.gqrx_running(), bool)


if __name__ == "__main__":
    unittest.main()
