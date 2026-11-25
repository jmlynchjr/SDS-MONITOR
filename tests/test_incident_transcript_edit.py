# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.104: editing a call's transcript from the Incidents window."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from support import mon, monitor
from test_incident_fixes import write_day, write_csv, at, title

DAY = "2026-10-23"


def make_day():
    write_day(DAY, [
        (at(8, 0, 0, DAY), 8, "Fire Dispatch", "44002", ["3100001"], "Engine 12 respond to a fire"),
        (at(8, 0, 30, DAY), 4, "Fire Dispatch", "44002", ["3100001"], ""),          # recorded, not transcribed
        (at(8, 3, 0, DAY), 6, "Fireground 2", "44010", ["3100001"], "Engine 12 on scene"),
        (at(8, 5, 0, DAY), 5, "Fireground 2", "44010", ["3100001"], ""),            # no recording at all
    ])
    index = []
    for hh, mm, ss, ch, tg in ((8, 0, 0, "Fire Dispatch", "44002"), (8, 0, 30, "Fire Dispatch", "44002"),
                               (8, 3, 0, "Fireground 2", "44010")):
        start = f"{DAY} {hh:02d}:{mm:02d}:{ss:02d}"
        index.append([start, start, "5.0", "Palmetto 800", "Greenville County", ch, "859.262500MHz",
                      f"TGID:{tg}", "", "UID:3100001", "P25", "-52", "6.0", f"{hh:02d}{mm:02d}{ss:02d}.mp3"])
    write_csv(mon.audio_index_path(datetime.strptime(DAY, "%Y-%m-%d")), mon.AUDIO_HEADERS, index)


class EditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_day()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(mon, "INCIDENT_EDITS_FILE", Path(self.tmp.name) / "incident_edits.json")
        p.start()
        self.addCleanup(p.stop)

    def calls(self):
        return mon.receptions_with_units(DAY)

    def test_correct_and_add(self):
        first, second, third, fourth = self.calls()
        tr, audio = mon.call_transcript(first["row"])
        self.assertEqual(tr[8], "Engine 12 respond to a fire")
        saved = mon.save_call_transcript(first["row"], " Engine 12 respond structure fire 100 Main ")
        self.assertEqual(saved[8], "Engine 12 respond structure fire 100 Main")
        self.assertTrue(saved[9].endswith("(corrected)"))
        again = mon.save_call_transcript(first["row"], "Engine 12 respond structure fire 100 Main St")
        self.assertEqual(again[9].count("(corrected)"), 1)
        # a recorded call without a transcript gets one
        mon.save_call_transcript(second["row"], "Engine 12 responding")
        texts = [c["text"] for c in self.calls()]
        self.assertEqual(texts[:2], ["Engine 12 respond structure fire 100 Main St", "Engine 12 responding"])
        with self.assertRaises(ValueError):
            mon.save_call_transcript(fourth["row"], "anything")

    def test_from_the_window(self):
        w = monitor(fresh=True)
        w.unit_title = title
        w.transcription_window = mock.Mock()
        win = mon.IncidentsWindow(w)
        win.reload()
        win.days.setCurrentText(DAY)
        win.recompute()
        inc = win.incidents[0]
        win.current = lambda: inc
        win.show_incident()
        r = inc["members"][2]
        dialog = mock.Mock()
        dialog.exec.return_value = True
        dialog.text.return_value = "Engine 12 on scene, working fire"
        with mock.patch.object(mon, "TranscriptEditDialog", return_value=dialog):
            win.edit_transcript(r)
        w.transcription_window.add_transcript.assert_called_once()
        self.assertIn("Engine 12 on scene, working fire", [c["text"] for c in self.calls()])
        fourth = self.calls()[3]
        with mock.patch.object(mon.QMessageBox, "information") as info, \
                mock.patch.object(mon, "TranscriptEditDialog") as dlg:
            win.edit_transcript(fourth)
        info.assert_called_once()
        dlg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
