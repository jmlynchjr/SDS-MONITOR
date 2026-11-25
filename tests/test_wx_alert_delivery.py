# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.116: a weather alert goes all the way to the desktop and the phone (the real notify(),
not a stand-in), says why when it doesn't, and the Phone alerts window doesn't squeeze its boxes."""
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from support import mon, monitor

# What the SDS100 sent on Wed 2026-11-25 at 11:09 (NOAA weekly test, Weather Alert Priority, SAME 1).
WX = ('<ScannerInfo Mode="WX Alert" V_Screen="wx_alert"><WxMode Mode="Weather Alert" SAME="SAME 1"/>'
      '<WxChannel Name="Weather" Freq="162.550000MHz"/><Property Mute="Unmute"/>'
      '<ViewDescription><PopupScreen Text="Warning WXWX Alert"/></ViewDescription></ScannerInfo>')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.w = w = monitor(fresh=True)
        self.calls = []
        w.settings.setValue("phone/enabled", "true")
        w.settings.setValue("phone/service", "ntfy")
        if w.modes_window is None:
            w.modes_window = mon.ScannerModesWindow(w)
        self.m = w.modes_window
        patches = [mock.patch.object(mon.subprocess, "Popen",
                                     side_effect=lambda cmd, **k: self.calls.append(("desktop", cmd[0]))),
                   mock.patch.object(mon, "phone_send",
                                     side_effect=lambda cfg, t, x: self.calls.append(("phone", t))),
                   mock.patch.object(mon.shutil, "which", side_effect=lambda n: "/usr/bin/" + n)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def wait_phone(self):
        for _ in range(50):
            if any(c[0] == "phone" for c in self.calls):
                return
            time.sleep(0.02)

    def test_alert_reaches_desktop_and_phone(self):
        self.m.wx_alert_box.setChecked(True)
        self.m.gsi(ET.fromstring(WX), True)
        self.wait_phone()
        self.assertIn(("desktop", "notify-send"), self.calls)
        self.assertIn(("phone", "Weather alert: Weather"), self.calls)
        rows = mon.read_alert_log(time.strftime("%Y-%m-%d"))
        self.assertTrue(any(r[1] == "wx_alert" for r in rows), rows)

    def test_control_characters_in_the_alert_text(self):
        """The 11:09 alert: a NUL in the pop-up text made notify-send refuse it, which stopped
        the phone alert too. Now the text is cleaned, and a failed pop-up doesn't stop the phone."""
        def popen(cmd, **k):
            if any("\x00" in str(a) for a in cmd):
                raise ValueError("embedded null byte")
            self.calls.append(("desktop", cmd[0]))
        self.m.wx_alert_box.setChecked(True)
        root = ET.fromstring(WX)
        root.find(".//PopupScreen").set("Text", "Warning WX\x00\rWX Alert")
        with mock.patch.object(mon.subprocess, "Popen", side_effect=popen):
            self.m.gsi(root, True)
        self.wait_phone()
        self.assertIn(("desktop", "notify-send"), self.calls)
        self.assertIn(("phone", "Weather alert: Weather"), self.calls)
        self.assertEqual(mon.clean_alert_text("Warning WX\x00\r\nWX Alert"), "Warning WX WX Alert")

    def test_a_failed_desktop_popup_still_sends_to_the_phone(self):
        with mock.patch.object(mon.subprocess, "Popen", side_effect=OSError("no D-Bus")):
            self.w.notify("Weather alert: Weather", "162.550000MHz", "wx_alert")
        self.wait_phone()
        self.assertIn(("phone", "Weather alert: Weather"), self.calls)

    def test_unticked_box_is_reported_not_silent(self):
        self.m.wx_alert_box.setChecked(False)
        self.m.gsi(ET.fromstring(WX), True)
        self.assertEqual(self.calls, [])
        log = (mon.MODE_ALERT_DIR / "problems.log").read_text(encoding="utf-8")
        self.assertIn("unticked", log)

    def test_failure_while_sending_is_reported(self):
        self.m.wx_alert_box.setChecked(True)
        with mock.patch.object(self.w, "notify", side_effect=RuntimeError("boom")):
            self.m.gsi(ET.fromstring(WX), True)
        log = (mon.MODE_ALERT_DIR / "problems.log").read_text(encoding="utf-8")
        self.assertIn("boom", log)


class PhoneWindowTests(unittest.TestCase):
    def test_settings_page_scrolls(self):
        with mock.patch.object(mon, "scroll_page", wraps=mon.scroll_page) as pages:
            d = mon.PhoneSettingsDialog(monitor(fresh=True))
        self.assertEqual(pages.call_count, 1)
        self.assertEqual((d.per_hour.value(), d.quiet_from.value(), d.quiet_to.value()), (20, 22, 7))


if __name__ == "__main__":
    unittest.main()
