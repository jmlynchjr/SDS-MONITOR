# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.85: a sound for each kind of alert (Tools → Alerts → Alert Sounds…)."""
import json
import tempfile
import unittest
from pathlib import Path

from support import Settings, mon, monitor


def make_theme(base, theme, names, sub="stereo"):
    folder = Path(base) / theme / sub
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"x")
    return folder


class Recorder:
    def __init__(self, players=("paplay", "pw-play", "ffplay")):
        self.players = set(players)
        self.run = []
        self.beeps = 0

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.players else None

    def popen(self, cmd):
        self.run.append(cmd)

    def beep(self):
        self.beeps += 1


class SystemSoundTests(unittest.TestCase):
    def test_finds_themes_and_your_own_wins(self):
        with tempfile.TemporaryDirectory() as mine, tempfile.TemporaryDirectory() as system:
            make_theme(system, "freedesktop", ["message-new-instant.oga", "bell.oga", "readme.txt"])
            make_theme(system, "ocean", ["alarm-clock-elapsed.oga"])
            make_theme(mine, "freedesktop", ["bell.wav"])
            found = mon.system_sounds([mine, system])
        self.assertEqual(set(found), {"freedesktop/message-new-instant", "freedesktop/bell",
                                      "ocean/alarm-clock-elapsed"})
        self.assertTrue(found["freedesktop/bell"].startswith(mine))

    def test_missing_folders_are_fine(self):
        self.assertEqual(mon.system_sounds(["/nonexistent/sounds"]), {})

    def test_label(self):
        self.assertEqual(mon.system_sound_label("ocean/message-new-instant"), "Message new instant (Ocean)")


class ChoiceTests(unittest.TestCase):
    def test_default_is_the_old_sound_at_full_volume(self):
        self.assertEqual(mon.clean_sound_choice(""), {"source": "system", "sound": mon.ALERT_SOUND_DEFAULT,
                                                      "volume": 100})

    def test_junk_is_cleaned(self):
        c = mon.clean_sound_choice(json.dumps({"source": "laser", "volume": "loud"}))
        self.assertEqual((c["source"], c["volume"]), ("system", 100))
        self.assertEqual(mon.clean_sound_choice({"source": "file", "sound": " a.wav ", "volume": 250})["volume"], 100)
        self.assertEqual(mon.clean_sound_choice({"source": "none", "volume": -5})["volume"], 0)


class PlayerTests(unittest.TestCase):
    def test_paplay_first_with_its_volume_scale(self):
        r = Recorder()
        self.assertEqual(mon.sound_player_command("/s/a.oga", 50, r.which),
                         ["paplay", "--volume=32768", "/s/a.oga"])

    def test_mp3_goes_to_ffplay(self):
        r = Recorder()
        cmd = mon.sound_player_command("/s/a.MP3", 30, r.which)
        self.assertEqual(cmd[0], "ffplay")
        self.assertIn("30", cmd)

    def test_falls_back_to_what_is_installed(self):
        self.assertEqual(mon.sound_player_command("/s/a.wav", 100, Recorder(["pw-play"]).which)[:2],
                         ["pw-play", "--volume=1.00"])
        self.assertIsNone(mon.sound_player_command("/s/a.wav", 100, Recorder([]).which))


class AlertSoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        make_theme(self.tmp.name, "freedesktop", ["message-new-instant.oga", "bell.oga"])
        self.sounds_found = mon.system_sounds([self.tmp.name])
        self.r = Recorder()
        self.a = mon.AlertSounds(Settings(), popen=self.r.popen, which=self.r.which, beep=self.r.beep,
                                 sounds=self.sounds_found)

    def test_default_plays_the_message_sound(self):
        cmd = self.a.play("tone_out", now=1)
        self.assertEqual(cmd[-1], self.sounds_found["freedesktop/message-new-instant"])
        self.assertEqual(self.a.last_problem, "")

    def test_each_kind_has_its_own_sound_and_volume(self):
        self.a.set_choice("tone_out", "system", "freedesktop/bell", 40)
        self.a.play("tone_out", now=1)
        self.a.play("wx_alert", now=1)
        self.assertEqual(self.r.run[0], ["paplay", "--volume=26214", self.sounds_found["freedesktop/bell"]])
        self.assertEqual(self.r.run[1][-1], self.sounds_found["freedesktop/message-new-instant"])

    def test_sound_file(self):
        f = Path(self.tmp.name) / "siren.mp3"
        f.write_bytes(b"x")
        self.a.set_choice("aircraft", "file", str(f), 70)
        self.assertEqual(self.a.play("aircraft", now=1)[0], "ffplay")

    def test_missing_file_falls_back_and_says_so(self):
        self.a.set_choice("close_call", "file", "/nowhere/gone.wav", 100)
        cmd = self.a.play("close_call", now=1)
        self.assertEqual(cmd[-1], self.sounds_found["freedesktop/message-new-instant"])
        self.assertIn("not found", self.a.last_problem)

    def test_none_and_zero_volume_are_silent(self):
        self.a.set_choice("keyword", "none", "", 100)
        self.a.set_choice("activity", "system", "freedesktop/bell", 0)
        self.assertEqual(self.a.play("keyword", now=1), "none")
        self.assertEqual(self.a.play("activity", now=1), "none")
        self.assertEqual((self.r.run, self.r.beeps), ([], 0))

    def test_same_kind_not_restarted_on_itself(self):
        self.a.play("new_unit", now=10)
        self.assertEqual(self.a.play("new_unit", now=10.2), "skipped")
        self.assertNotEqual(self.a.play("new_unit", now=10.2, force=True), "skipped")
        self.assertNotEqual(self.a.play("new_talkgroup", now=10.2), "skipped")

    def test_test_and_unknown_kinds_use_other_alerts(self):
        self.a.set_choice("alert", "system", "freedesktop/bell", 100)
        self.assertEqual(self.a.play("test", now=1)[-1], self.sounds_found["freedesktop/bell"])
        self.assertEqual(self.a.choice("something_new")["sound"], "freedesktop/bell")

    def test_no_player_beeps(self):
        a = mon.AlertSounds(Settings(), popen=self.r.popen, which=Recorder([]).which, beep=self.r.beep,
                            sounds=self.sounds_found)
        self.assertEqual(a.play("tone_out", now=1), "beep")
        self.assertEqual(self.r.beeps, 1)

    def test_reset(self):
        self.a.set_choice("tone_out", "none", "", 10)
        self.a.reset()
        self.assertEqual(self.a.choice("tone_out")["source"], "system")


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.w = monitor(fresh=True)
        self.w.settings.setValue("alerts/notify", "false")
        self.played = []
        self.w.alert_sounds.play = lambda kind, force=False, now=None: self.played.append((kind, force)) or "none"
        self.w.alert_sounds.last_problem = ""
        self.w.phone = None

    def test_alert_plays_its_kinds_sound(self):
        self.w.notify("Tone-Out: Station 1", "Tones 1 / 2", "tone_out")
        self.assertEqual(self.played, [("tone_out", False)])

    def test_sound_switch_off_is_silent(self):
        self.w.settings.setValue("alerts/sound", False)
        self.w.notify("Weather alert", "Tornado warning", "wx_alert")
        self.assertEqual(self.played, [])

    def test_test_button_always_plays(self):
        self.w.notify("SDS100 alert test", "Alerts are working.", "test")
        self.assertEqual(self.played, [("test", True)])


class DialogTests(unittest.TestCase):
    def test_window_opens_and_lists_every_kind(self):
        w = monitor(fresh=True)
        d = mon.AlertSoundsDialog(w)
        self.assertEqual(list(d.rows), [k for k, _ in mon.ALERT_SOUND_KINDS])

    def test_menu_entry(self):
        source = Path(mon.__file__).read_text(encoding="utf-8")
        self.assertIn('alerts_sub.addAction("Alert Sounds…"', source)


if __name__ == "__main__":
    unittest.main()
