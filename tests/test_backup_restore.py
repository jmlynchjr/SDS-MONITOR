# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.86: Tools → Back Up Settings and Data… and Restore Settings and Data…

Every test works in its own temporary folders; nothing here reads or writes the real data folder or settings.
"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from support import Settings, mon, monitor


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


class Place:
    """A computer: a data folder, a settings file and a folder of sounds."""

    def __init__(self, base, name):
        self.root = Path(base) / name
        self.data = self.root / "SDS100Monitor"
        self.conf = self.root / ".config/SDS100Monitor/Monitor110.conf"
        self.sounds = self.root / "Music"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = Place(self.tmp.name, "old")
        self.new = Place(self.tmp.name, "new")
        o = self.old
        write(o.conf, "[General]\nport=/dev/ttyACM0\n")
        write(o.data / "unit_names.csv", "Unit,Name\n1234,Engine 1\n")
        write(o.data / "ten_codes.json", "{}")
        write(o.data / "history/2026-11-14.csv", "a,b\n")
        write(o.data / "unit_ids/2026-11-14.csv", "u\n")
        write(o.data / "audio/2026-11-14/call.mp3", b"\x00" * 100)
        write(o.data / "transcribe-engine/model.bin", b"x" * 50)
        write(o.data / "backups/older.zip", b"old backup")
        write(o.data / "widget.json", "{}")
        self.bell = write(o.sounds / "bell.wav", b"RIFFbell")
        self.settings = Settings()
        self.settings.setValue("alertsounds/tone_out", json.dumps({"source": "file", "sound": str(self.bell),
                                                                   "volume": 80}))
        self.settings.setValue("alertsounds/activity", json.dumps({"source": "system"}))
        self.settings.setValue("reports/folder", str(o.data / "reports"))
        self.zip = Path(self.tmp.name) / "backup.zip"

    def backup(self, parts=("settings", "history")):
        return mon.make_backup(self.zip, parts, self.old.conf, self.settings, self.old.data)


class PartTests(unittest.TestCase):
    def test_parts(self):
        self.assertEqual(mon.backup_part_of("unit_names.csv"), "settings")
        self.assertEqual(mon.backup_part_of("incident_edits.json"), "settings")       # 1.1.87
        self.assertEqual(mon.backup_part_of("coverage/coverage_2026-11-15.csv"), "history")
        self.assertEqual(mon.backup_part_of("history/2026-11-14.csv"), "history")
        self.assertEqual(mon.backup_part_of("reception_history_v6.csv"), "history")
        self.assertEqual(mon.backup_part_of("audio/x/y.mp3"), "recordings")
        for never in ("transcribe-engine/m.bin", "backups/a.zip", "widget.json", "rdio_queue.json",
                      "restore-pending.zip"):
            self.assertIsNone(mon.backup_part_of(never), never)

    def test_sizes(self):
        self.assertEqual(mon.backup_size_text(512), "512 bytes")
        self.assertEqual(mon.backup_size_text(1536), "1.5 KB")
        self.assertEqual(mon.backup_size_text(3 * 1024 ** 3), "3.0 GB")

    def test_settings_file_follows_xdg(self):
        with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": "/x/conf"}):
            self.assertEqual(mon.settings_file_path(), Path("/x/conf/SDS100Monitor/Monitor110.conf"))


class BackupTests(Base):
    def test_what_goes_in(self):
        manifest = self.backup()
        with zipfile.ZipFile(self.zip) as z:
            names = set(z.namelist())
        self.assertIn("settings/Monitor110.conf", names)
        self.assertIn("data/unit_names.csv", names)
        self.assertIn("data/history/2026-11-14.csv", names)
        self.assertIn("sounds/tone_out-bell.wav", names)
        for left_out in ("data/audio/2026-11-14/call.mp3", "data/transcribe-engine/model.bin",
                         "data/backups/older.zip", "data/widget.json"):
            self.assertNotIn(left_out, names)
        self.assertEqual(manifest["parts"], ["history", "settings"])
        self.assertEqual(manifest["files"]["data/unit_names.csv"]["part"], "settings")
        self.assertEqual(manifest["sounds"]["tone_out"]["path"], str(self.bell))
        self.assertEqual(mon.read_backup(self.zip)["format"], mon.BACKUP_FORMAT)
        self.assertFalse(Path(str(self.zip) + ".part").exists())

    def test_recordings_only_when_asked(self):
        manifest = self.backup(["recordings"])
        self.assertEqual(set(manifest["files"]), {"data/audio/2026-11-14/call.mp3"})
        with zipfile.ZipFile(self.zip) as z:
            self.assertEqual(z.getinfo("data/audio/2026-11-14/call.mp3").compress_type, zipfile.ZIP_STORED)

    def test_nothing_chosen(self):
        with self.assertRaises(ValueError):
            self.backup([])

    def test_a_backup_saved_in_the_data_folder_is_not_in_itself(self):
        target = self.old.data / "mine.zip"
        manifest = mon.make_backup(target, ["history"], self.old.conf, self.settings, self.old.data)
        self.assertNotIn("data/mine.zip", manifest["files"])
        self.assertNotIn("data/mine.zip.part", manifest["files"])


class ReadTests(Base):
    def bad(self, files, manifest=None):
        with zipfile.ZipFile(self.zip, "w") as z:
            for name, data in files.items():
                z.writestr(name, data)
            if manifest is not None:
                z.writestr("manifest.json", json.dumps(manifest))
        with self.assertRaises(ValueError) as caught:
            mon.read_backup(self.zip)
        return str(caught.exception)

    def test_refuses(self):
        write(self.zip, "not a zip")
        with self.assertRaises(ValueError):
            mon.read_backup(self.zip)
        self.assertIn("no manifest", self.bad({"a.txt": "x"}))
        self.assertIn("newer version", self.bad({}, {"format": "something-else", "files": {}}))
        good = {"format": mon.BACKUP_FORMAT}
        self.assertIn("unsafe", self.bad({"data/../../evil": "x"}, dict(good, files={"data/../../evil": {}})))
        self.assertIn("unsafe", self.bad({"/etc/passwd": "x"}, dict(good, files={"/etc/passwd": {}})))
        self.assertIn("unsafe", self.bad({"other/x": "x"}, dict(good, files={"other/x": {}})))
        self.assertIn("missing", self.bad({}, dict(good, files={"data/a.csv": {}})))


class RestoreTests(Base):
    def restored_settings(self):
        """What the settings file holds after the restore (the old computer's settings)."""
        s = Settings()
        for key in self.settings.allKeys():
            s.setValue(key, self.settings.value(key))
        return s

    def test_new_computer(self):
        self.backup()
        conf = self.restored_settings()
        result = mon.apply_restore(self.zip, ["settings", "history"], self.new.conf, self.new.data,
                                   make_settings=lambda f: conf)
        self.assertEqual(self.new.conf.read_text(), self.old.conf.read_text())
        self.assertEqual((self.new.data / "unit_names.csv").read_text(), "Unit,Name\n1234,Engine 1\n")
        self.assertTrue((self.new.data / "history/2026-11-14.csv").is_file())
        self.assertFalse((self.new.data / "audio").exists())
        self.assertEqual(result["safety"], "")                 # nothing was replaced
        # The old computer's own sound file isn't here: it is put in the data folder and the setting follows it.
        self.bell.unlink()
        conf = self.restored_settings()
        mon.apply_restore(self.zip, ["settings"], self.new.conf, self.new.data, make_settings=lambda f: conf)
        moved = self.new.data / "sounds/tone_out-bell.wav"
        self.assertEqual(moved.read_bytes(), b"RIFFbell")
        choice = mon.clean_sound_choice(conf.value("alertsounds/tone_out"))
        self.assertEqual((choice["source"], choice["sound"], choice["volume"]), ("file", str(moved), 80))
        self.assertEqual(conf.value("reports/folder"), str(self.new.data / "reports"))

    def test_sound_that_is_still_there_is_left_alone(self):
        self.backup()
        conf = self.restored_settings()
        result = mon.apply_restore(self.zip, ["settings"], self.new.conf, self.new.data, make_settings=lambda f: conf)
        self.assertEqual(result["sounds"], [])
        self.assertEqual(mon.clean_sound_choice(conf.value("alertsounds/tone_out"))["sound"], str(self.bell))

    def test_replaced_files_are_saved_first_and_can_be_put_back(self):
        self.backup()
        write(self.old.data / "unit_names.csv", "Unit,Name\n1234,Changed\n")
        write(self.old.data / "history/2026-11-15.csv", "today\n")
        plan = {name: action for name, _, action in
                mon.plan_restore(mon.read_backup(self.zip), {"settings", "history"}, self.old.conf, self.old.data)}
        self.assertEqual(plan["data/unit_names.csv"], "replace")
        self.assertEqual(plan["data/history/2026-11-14.csv"], "same")
        result = mon.apply_restore(self.zip, ["settings", "history"], self.old.conf, self.old.data)
        self.assertEqual((self.old.data / "unit_names.csv").read_text(), "Unit,Name\n1234,Engine 1\n")
        self.assertEqual((self.old.data / "history/2026-11-15.csv").read_text(), "today\n")   # not in the backup
        self.assertEqual(result["replaced"], 1)
        safety = Path(result["safety"])
        self.assertTrue(safety.is_file())
        self.assertEqual(safety.parent, self.old.data / "backups")
        mon.apply_restore(safety, mon.read_backup(safety)["parts"], self.old.conf, self.old.data)
        self.assertEqual((self.old.data / "unit_names.csv").read_text(), "Unit,Name\n1234,Changed\n")

    def test_only_the_chosen_parts(self):
        self.backup(["settings", "history", "recordings"])
        result = mon.apply_restore(self.zip, ["recordings"], self.new.conf, self.new.data)
        self.assertTrue((self.new.data / "audio/2026-11-14/call.mp3").is_file())
        self.assertFalse(self.new.conf.exists())
        self.assertFalse((self.new.data / "unit_names.csv").exists())
        self.assertEqual(result["parts"], ["recordings"])
        with self.assertRaises(ValueError):
            mon.apply_restore(self.zip, [], self.new.conf, self.new.data)

    def test_damaged_file_is_refused(self):
        self.backup(["history"])
        with zipfile.ZipFile(self.zip) as z:
            items = {n: z.read(n) for n in z.namelist()}
        items["data/history/2026-11-14.csv"] = b"tampered\n"
        with zipfile.ZipFile(self.zip, "w") as z:
            for n, d in items.items():
                z.writestr(n, d)
        with self.assertRaisesRegex(ValueError, "damaged"):
            mon.apply_restore(self.zip, ["history"], self.new.conf, self.new.data)
        self.assertFalse((self.new.data / "history/2026-11-14.csv").exists())


class PendingTests(Base):
    def setUp(self):
        super().setUp()
        here = self.new.data
        patches = [mock.patch.object(mon, "PENDING_RESTORE", here / "restore-pending.zip"),
                   mock.patch.object(mon, "PENDING_CHOICES", here / "restore-pending.json"),
                   mock.patch.object(mon, "RESTORE_RESULT", here / "restore-result.json"),
                   mock.patch.object(mon, "DATA_DIR", here)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_nothing_pending(self):
        self.assertIsNone(mon.run_pending_restore(self.new.conf))

    def test_applied_once_and_reported(self):
        self.backup()
        write(mon.PENDING_RESTORE, self.zip.read_bytes())
        write(mon.PENDING_CHOICES, json.dumps({"parts": ["settings"], "source": "/media/usb/backup.zip"}))
        result = mon.run_pending_restore(self.new.conf)
        self.assertEqual(self.new.conf.read_text(), self.old.conf.read_text())
        self.assertFalse(mon.PENDING_RESTORE.exists())
        self.assertFalse(mon.PENDING_CHOICES.exists())
        self.assertEqual(json.loads(mon.RESTORE_RESULT.read_text())["backup"], "/media/usb/backup.zip")
        title, text = mon.restore_report(result)
        self.assertEqual(title, "Restore finished")
        self.assertIn("backup.zip", text)

    def test_a_failure_is_reported_not_repeated(self):
        write(mon.PENDING_RESTORE, "not a zip")
        write(mon.PENDING_CHOICES, json.dumps({"parts": ["settings"]}))
        result = mon.run_pending_restore(self.new.conf)
        self.assertIn("error", result)
        self.assertFalse(mon.PENDING_RESTORE.exists())
        self.assertEqual(mon.restore_report(result)[0], "Restore failed")

    def test_menu_starts_a_restore(self):
        self.backup()
        win = monitor()
        win.close = mock.Mock()

        class Accepting:
            def __init__(self, *args):
                pass

            def exec(self):
                return mon.QDialog.DialogCode.Accepted

            def parts(self):
                return ["settings"]

        with mock.patch.object(mon.QFileDialog, "getOpenFileName", return_value=(str(self.zip), "")), \
                mock.patch.object(mon, "RestoreDialog", Accepting):
            win.restore_settings_and_data()
        self.assertTrue(mon.PENDING_RESTORE.is_file())
        self.assertEqual(json.loads(mon.PENDING_CHOICES.read_text())["parts"], ["settings"])
        self.assertTrue(win.restart_after_restore)
        win.close.assert_called_once()
        self.assertFalse(self.new.conf.exists())            # nothing is restored until the Monitor closes
        win.restart_after_restore = False


class WindowTests(Base):
    def test_dialogs_build(self):
        win = monitor()
        mon.BackupDialog(win)
        self.backup()
        mon.RestoreDialog(win, str(self.zip), mon.read_backup(self.zip))

    def test_in_the_tools_menu(self):
        source = Path(mon.PROGRAM_FILE).read_text()
        self.assertIn('tools_menu.addAction("Back Up Settings and Data…", self.back_up_settings_and_data)', source)
        self.assertIn('tools_menu.addAction("Restore Settings and Data…", self.restore_settings_and_data)', source)


if __name__ == "__main__":
    unittest.main()
