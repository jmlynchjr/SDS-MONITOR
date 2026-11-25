# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.2.0: Tools → Settings…: every setting in one window."""
import inspect
import unittest
from pathlib import Path
from unittest import mock

from support import mon, monitor


class SettingsWindowTests(unittest.TestCase):
    def setUp(self):
        self.w = monitor(fresh=True)
        # Receivers and GPS mustn't open sockets or ports when their pages are saved in a test.
        for obj, name in ((self.w.sdrtrunk, "start"), (self.w.gqrx, "start"), (self.w.gps, "start")):
            p = mock.patch.object(obj, name)
            p.start()
            self.addCleanup(p.stop)

    def open(self, page=None):
        self.w.open_settings(page)
        return self.w.settings_window

    def test_every_page_builds(self):
        win = self.open()
        for pid, title, _parent in mon.SETTINGS_PAGES:
            win.show_page(pid)
            self.assertIn(pid, win.pages, f"page {title!r} couldn't be built")
        self.assertEqual(len(win.pages), len(mon.SETTINGS_PAGES))

    def test_every_page_saves(self):
        win = self.open()
        for pid, _t, _p in mon.SETTINGS_PAGES:
            win.show_page(pid)
        with mock.patch.object(mon.QTimer, "singleShot"):
            ok = win.apply()
        self.assertTrue(ok, win.message.text())

    def test_opens_at_the_page_asked_for(self):
        win = self.open("phone")
        self.assertEqual(win.current_page(), "phone")
        self.w.open_settings("storage")
        self.assertIs(self.w.settings_window, win)
        self.assertEqual(win.current_page(), "storage")

    def test_a_field_is_saved_and_kept_in_step_with_its_window(self):
        self.w.open_analysis("incidents_window", mon.IncidentsWindow)
        inc = self.w.incidents_window
        win = self.open("incidents")
        gap = next(f for f in win.pages["incidents"]["fields"] if f.key == "incidents/gap")
        gap.widget.setValue(25)
        grow = next(f for f in win.pages["incidents"]["fields"] if f.key == "incidents/grow_alert")
        grow.widget.setChecked(True)
        win.apply()
        self.assertEqual(int(self.w.settings.value("incidents/gap")), 25)
        self.assertEqual(str(self.w.settings.value("incidents/grow_alert")), "true")
        self.assertEqual(inc.gap.value(), 25)                 # the window's own (hidden) control follows
        self.assertTrue(inc.grow_alert.isChecked())

    def test_a_field_without_its_window_still_saves(self):
        self.assertIsNone(getattr(self.w, "aircraft_window", None))
        win = self.open("aircraft")
        miles = next(f for f in win.pages["aircraft"]["fields"] if f.key == "adsb/alert_miles")
        miles.widget.setValue(40)
        with mock.patch.object(self.w.adsb, "apply_settings") as applied:
            win.apply()
        self.assertEqual(int(self.w.settings.value("adsb/alert_miles")), 40)
        applied.assert_called()

    def test_numbers_stored_as_text_pick_the_right_choice(self):
        self.w.settings.setValue("watch/window", "30")
        win = self.open("activity")
        f = next(f for f in win.pages["activity"]["fields"] if f.key == "watch/window")
        self.assertEqual(f.widget.currentData(), 30)

    def test_storage_minimums(self):
        win = self.open("storage")
        rec = next(f for f in win.pages["storage"]["fields"] if f.key == "storage/recordings_days")
        rec.widget.setValue(3)                 # under the 7-day minimum
        with mock.patch.object(mon.QTimer, "singleShot"):
            win.apply()
        self.assertEqual(int(self.w.settings.value("storage/recordings_days")), 7)

    def test_embedded_dialog_saves_with_the_window(self):
        win = self.open("phone")
        dialog = win.pages["phone"]["dialogs"][0]
        dialog.enabled.setChecked(True)
        dialog.quiet.setChecked(True)
        win.apply()
        self.assertEqual(str(self.w.settings.value("phone/enabled")), "true")
        self.assertEqual(str(self.w.settings.value("phone/quiet")), "true")

    def test_cancel_saves_nothing(self):
        win = self.open("alerts")
        f = next(f for f in win.pages["alerts"]["fields"] if f.key == "alerts/cooldown")
        f.widget.setValue(999)
        win.cancel()
        self.assertNotEqual(str(self.w.settings.value("alerts/cooldown", 60)), "999")
        self.assertIsNone(self.w.settings_window)

    def test_embedded_pages_hide_their_own_save_and_close(self):
        win = self.open("gqrx")
        for pid in ("gqrx", "sdrtrunk", "rdio"):
            win.show_page(pid)
            dialog = win.pages[pid]["dialogs"][0]
            buttons = dialog.findChildren(mon.QPushButton)
            if not buttons:
                self.skipTest("this Qt stand-in can't list child widgets; checked on real Qt")
            for b in buttons:
                if b.text() in ("SAVE", "CLOSE"):
                    self.assertTrue(b.isHidden(), f"{pid}: {b.text()} still shown")

    def test_menus_and_buttons(self):
        src = inspect.getsource(mon.Monitor)
        self.assertIn('tools_menu.addAction("Settings…"', src)
        self.assertIn('QKeySequence("Ctrl+,")', src)
        for gone in ('"Customize Scanner Information…"', 'addMenu("Theme")', '"SDRTrunk Receiver…"',
                     'addAction("GQRX…"', '"Alert Sounds…"', '"Phone Alerts…"', '"Send Calls to Rdio Scanner…"',
                     'addAction("GPS…"', 'addMenu("Plasma Widget")', 'addMenu("Macro Keypad")',
                     '"Name SAME Groups…"'):
            self.assertNotIn(gone, src, gone)
        whole = Path(mon.__file__).read_text(encoding="utf-8")
        self.assertNotIn("PhoneSettingsDialog(self.monitor, self).exec()", whole)
        self.assertNotIn("PhoneSettingsDialog(monitor, self).exec()", whole)
        self.assertNotIn("AlertSoundsDialog(self.monitor, self).exec()", whole)

    def test_tool_windows_hide_the_moved_settings(self):
        for attr, cls, hidden in (
                ("incidents_window", mon.IncidentsWindow, ("gap", "min_calls", "grow_alert", "join_places")),
                ("activity_window", mon.ActivityWatchWindow, ()),
                ("aircraft_window", mon.AircraftWindow, ()),
                ("daily_report_window", mon.DailyReportWindow, ("auto_pdf", "phone_summary")),
                ("places_window", mon.PlacesWindow, ("area", "radius", "auto"))):
            self.w.open_analysis(attr, cls)
            window = getattr(self.w, attr)
            for name in hidden:
                self.assertFalse(getattr(window, name).isVisible() and not getattr(window, name).isHidden(), name)
        for name in ("push_box", "auto_connect_box", "auto_reconnect_box", "interval", "baud"):
            self.assertTrue(getattr(self.w, name).isHidden(), name)


if __name__ == "__main__":
    unittest.main()
