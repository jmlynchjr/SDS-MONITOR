# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.115: Tools → Storage…: what the data takes, and cleanup by rules."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from support import mon, monitor
from test_incident_fixes import write_csv

TODAY = date(2026, 11, 25)
GB = 1_000_000_000


def rec(days_ago, name="a.mp3", size=1_000_000):
    day = TODAY - timedelta(days=days_ago)
    return (Path(f"/data/audio/{day}/{name}"), day, size)


def rules(**over):
    r = {"recordings": {"days": 90, "gb": 0}, "activity_log": {"days": 30}, "backups": {"keep": 5},
         "packages": {"days": 0}, "reports": {"days": 0}, "logs": {"days": 0}, "low_free_gb": 0,
         "low_min_days": 7, "protect": True}
    r.update(over)
    return r


class PlanTests(unittest.TestCase):
    def plan(self, r, files, free=None, protect=lambda day: set()):
        return mon.storage_plan(r, today=TODAY, free_bytes=free, protect=protect, files=files)

    def test_days(self):
        files = {"recordings": [rec(100), rec(91, "b.mp3"), rec(90, "c.mp3"), rec(3, "d.mp3")],
                 "activity_log": [(Path("/data/activity_log/activity_log_2026-10-01.csv"), date(2026, 10, 1), 5)],
                 "logs": [(Path("/data/history/reception_history_2020-01-01.csv"), date(2020, 1, 1), 5)]}
        plan = self.plan(rules(), files)
        self.assertEqual(sorted(p.name for p, *_ in plan),
                         ["a.mp3", "activity_log_2026-10-01.csv", "b.mp3"])    # forever logs stay
        self.assertTrue(all(why == "older than 90 days" for p, k, s, why in plan if k == "recordings"))

    def test_protected(self):
        files = {"recordings": [rec(100), rec(100, "keep.mp3")]}
        keep = lambda day: {Path(f"/data/audio/{day}/keep.mp3")}
        self.assertEqual([p.name for p, *_ in self.plan(rules(), files, protect=keep)], ["a.mp3"])
        self.assertEqual(len(self.plan(rules(protect=False), files, protect=keep)), 2)

    def test_gigabyte_cap_oldest_first(self):
        files = {"recordings": [rec(i, f"{i}.mp3", GB) for i in range(0, 6)]}       # 6 GB
        plan = self.plan(rules(recordings={"days": 0, "gb": 3.5}), files)
        self.assertEqual([p.name for p, *_ in plan], ["5.mp3", "4.mp3", "3.mp3"])
        self.assertIn("over 3.5 GB", plan[0][3])

    def test_backups_keep_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(7):
                p = Path(tmp) / f"b{i}.zip"
                p.write_bytes(b"x")
                mon.os.utime(p, (1000 + i, 1000 + i))
                paths.append((p, None, 1))
            plan = self.plan(rules(), {"backups": paths})
        self.assertEqual(sorted(p.name for p, *_ in plan), ["b0.zip", "b1.zip"])

    def test_low_disk(self):
        files = {"recordings": [rec(i, f"{i}.mp3", GB) for i in range(0, 12)]}
        plan = self.plan(rules(recordings={"days": 0, "gb": 0}, low_free_gb=5), files, free=2 * GB)
        names = [p.name for p, *_ in plan]
        self.assertEqual(names, ["11.mp3", "10.mp3", "9.mp3", "8.mp3"])       # up to 5 GB + 1 GB free
        self.assertNotIn("3.mp3", names)                                       # younger than 7 days stay
        self.assertEqual(self.plan(rules(low_free_gb=5), files, free=20 * GB), [])

    def test_overview(self):
        files = {"recordings": [rec(1, "a.mp3", 700), rec(10, "b.mp3", 300)]}
        over = mon.storage_overview(files, today=TODAY)
        self.assertEqual(over["recordings"]["bytes"], 1000)
        self.assertEqual(over["recordings"]["per_day"], 100)
        self.assertEqual(mon.human_bytes(1_500_000), "1.5 MB")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, folder in (("DATA_DIR", root), ("AUDIO_DIR", root / "audio"), ("DAILY_LOG_DIR", root / "history"),
                             ("STORAGE_LOG", root / "storage_log.csv"), ("ALERT_LOG_DIR", root / "alerts"),
                             ("INCIDENT_EDITS_FILE", root / "incident_edits.json")):
            p = mock.patch.object(mon, name, folder)
            p.start()
            self.addCleanup(p.stop)
        self.root = root
        p = mock.patch.object(mon, "STORAGE_LOG_DIRS", ("DAILY_LOG_DIR",))     # the other shared test folders
        p.start()
        self.addCleanup(p.stop)

    def test_files_and_run(self):
        day = self.root / "audio" / "2026-08-01"
        day.mkdir(parents=True)
        (day / "080000_A.mp3").write_bytes(b"x" * 10)
        (day / "recordings_2026-08-01.csv").write_text("x")
        (day / "transcripts_2026-08-01.csv").write_text("x")
        (self.root / "history").mkdir()
        (self.root / "history" / "reception_history_2026-08-01.csv").write_text("x")
        rec_files = mon.storage_files("recordings")
        self.assertEqual([(p.name, d) for p, d, _s in rec_files], [("080000_A.mp3", date(2026, 8, 1))])
        logs = sorted(p.name for p, _d, _s in mon.storage_files("logs"))
        self.assertEqual(logs, ["reception_history_2026-08-01.csv", "transcripts_2026-08-01.csv"])
        plan = mon.storage_plan(rules(), today=TODAY, free_bytes=None, protect=lambda d: set())
        removed, freed = mon.run_storage_plan(plan)
        self.assertEqual((removed, freed), (1, 10))
        self.assertFalse((day / "080000_A.mp3").exists())
        self.assertTrue((day / "recordings_2026-08-01.csv").exists())
        self.assertIn("audio/2026-08-01/080000_A.mp3", mon.STORAGE_LOG.read_text())

    def test_protected_recordings(self):
        dayname = "2026-08-02"
        folder = self.root / "audio" / dayname
        write_csv(mon.audio_index_path(datetime(2026, 8, 2)), mon.AUDIO_HEADERS, [
            [f"{dayname} 09:00:00", f"{dayname} 09:00:05", "5", "Palmetto 800", "D", "Fire Dispatch", "", "", "", "",
             "", "", "5", "090000_Fire.mp3"],
            [f"{dayname} 09:30:00", f"{dayname} 09:30:05", "5", "Palmetto 800", "D", "EMS 1", "", "", "", "", "", "",
             "5", "093000_EMS.mp3"],
            [f"{dayname} 10:00:00", f"{dayname} 10:00:05", "5", "Palmetto 800", "D", "Ops", "", "", "", "", "", "",
             "5", "100000_Ops.mp3"]])
        write_csv(mon.transcript_path(dayname), mon.TRANSCRIPT_HEADERS, [
            [f"{dayname} 10:00:00", "Palmetto 800", "D", "Ops", "", "", "5", "100000_Ops.mp3", "fixed", "small.en "
             "(corrected)", ""]])
        write_csv(mon.alert_log_path(dayname), mon.ALERT_LOG_HEADERS, [
            [f"{dayname} 09:00:20", "keyword", "Keyword: fire", 'Heard "fire" on Fire Dispatch']])
        keep = mon.protected_recordings(dayname)
        self.assertEqual(sorted(p.name for p in keep), ["090000_Fire.mp3", "100000_Ops.mp3"])


class ManagerTests(unittest.TestCase):
    def test_off_by_default_and_daily(self):
        m = monitor(fresh=True)
        st = m.storage
        with mock.patch.object(st, "run") as run:
            st.check()
            run.assert_not_called()                                           # off until turned on
            m.settings.setValue("storage/enabled", "true")
            with mock.patch.object(mon, "storage_free_bytes", return_value=100 * GB):
                st.check()
                st.check()
            self.assertEqual(run.call_count, 1)                               # once a day
            with mock.patch.object(mon, "storage_free_bytes", return_value=1 * GB):
                st.check()
            self.assertEqual(run.call_count, 2)                               # and when the disk is low

    def test_rules_from_settings(self):
        m = monitor(fresh=True)
        r = mon.storage_rules(m.settings)
        self.assertEqual((r["recordings"]["days"], r["recordings"]["gb"], r["backups"]["keep"], r["logs"]["days"]),
                         (90, 20.0, 5, 0))
        m.settings.setValue("storage/logs_days", 365)
        self.assertEqual(mon.storage_rules(m.settings)["logs"]["days"], 365)

    def test_window(self):
        m = monitor(fresh=True)
        w = mon.StorageWindow(m)
        w.show_measured({"overview": {"recordings": {"bytes": 3 * GB, "files": 10, "per_day": GB / 2}},
                         "free": 50 * GB})
        self.assertIn("about 100 days", w.disk.text())
        w.show_plan([(Path("/x/audio/2026-08-01/a.mp3"), "recordings", 1000, "older than 90 days")])
        self.assertIn("would remove 1 files", w.plan_note.text())


if __name__ == "__main__":
    unittest.main()
