# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""v1.1.101: the scanner's icon codes in the screen mirror (from a real SDS100 GST reply)."""
import unittest

from support import mon

CAPTURE = (b'GST,00001010100000000,              Nov20 17:07 \xac\xad  ,,F0:0********- DATA          \x1a\x1b,,'
           b'S0:0---------   VOL: 5 SQL: 3 ,,D0:0*2*------   Tag:01.00.--- ,,Palmetto 800            ,,'
           b'Greenville Police             ,,,,Greenville County Simulc\x06\x07    ,,ID Scanning...          ,,,,'
           b'                P25 NAC:1DBh  ,,Sys ID: 1D9h     853.862500MHz,,RFSS ID: 2      Site ID: 23   ,,'
           b'WACN: BEE00h    Batt:3.98V    ,,UID: ---        RSSI: -60dBm  ,,,, SYSTEM      DEPT     CHANNEL ,'
           b'********* ********** *********,1,OFF,3,,,,,,,,,\r')


class GlyphTests(unittest.TestCase):
    def screen(self):
        text = CAPTURE.decode("latin-1").strip()
        return mon.parse_screen_reply(text[4:])

    def test_columns_kept(self):
        s = self.screen()
        lines = [t for _l, t, _m in s["lines"]]
        self.assertEqual(len(lines), 17)
        self.assertEqual(lines[0][-4:-2], "\xac\xad")
        self.assertEqual(lines[7], "Greenville County Simulc\x06\x07    ")
        self.assertEqual(s["extra"]["LED1"], "OFF")

    def test_glyphs(self):
        line = "Greenville County Simulc\x06\x07    "
        self.assertEqual(mon.screen_glyph(line, 24), ("ellipsis", 2, None))
        self.assertEqual(mon.screen_glyph("x\xac\xad", 1), ("signal", 2, 4))
        self.assertEqual(mon.screen_glyph("\xa4\xa5", 0), ("signal", 2, 0))
        self.assertEqual(mon.screen_glyph("\x1a\x1b", 0), ("unknown", 1, 0x1A))
        self.assertEqual(mon.screen_glyph("A", 0), ("text", 1, "A"))
        self.assertEqual(mon.screen_glyph("\x06", 0), ("unknown", 1, 6))      # half a pair

    def test_copied_text(self):
        self.assertEqual(mon.screen_line_text("Greenville County Simulc\x06\x07    ").rstrip(),
                         "Greenville County Simulc…")
        self.assertEqual(mon.screen_line_text("17:07 \xac\xad").rstrip(), "17:07 ▂▄▆█")
        self.assertEqual(mon.screen_line_text("DATA \x1a\x1b"), "DATA {0x1A}{0x1B}")

    def test_reply_read_byte_for_byte(self):
        with open(mon.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('raw.decode("latin-1")', source)


if __name__ == "__main__":
    unittest.main()
