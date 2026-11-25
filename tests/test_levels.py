# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.109: Tools → Audio Level Check… and normalizing before transcribing."""
import importlib.util
import math
import shutil
import subprocess
import unittest
from array import array
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from support import mon, monitor
from test_incident_fixes import write_csv

RATE = mon.LEVEL_RATE


def tone(db, seconds, freq=440.0):
    amp = 32767 * 10 ** (db / 20)
    return [int(max(-32768, min(32767, amp * math.sin(2 * math.pi * freq * i / RATE))))
            for i in range(int(seconds * RATE))]


def hiss(db, seconds):
    amp = int(32767 * 10 ** (db / 20))
    return [((i * 7919) % (2 * amp + 1)) - amp for i in range(int(seconds * RATE))]


def call(speech_db, noise_db=-60.0):
    return array("h", hiss(noise_db, 1.0) + tone(speech_db, 2.0) + hiss(noise_db, 1.0) + tone(speech_db, 1.0))


def worker():
    spec = importlib.util.spec_from_file_location("tw", mon.Path(mon.__file__).with_name("transcribe_worker.py"))
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)
    return tw


class MeasureTests(unittest.TestCase):
    def test_levels(self):
        lv = mon.pcm_levels(call(-10))
        self.assertAlmostEqual(lv["peak"], -10, delta=0.5)
        self.assertAlmostEqual(lv["speech"], -13, delta=0.7)           # a sine's RMS is 3 dB under its peak
        self.assertLess(lv["noise"], -55)
        self.assertGreater(lv["snr"], 35)
        self.assertFalse(lv["is_clipped"])

    def test_clipping(self):
        lv = mon.pcm_levels(call(6))
        self.assertTrue(lv["is_clipped"])
        self.assertGreaterEqual(lv["peak"], -0.1)

    def test_silence_and_short(self):
        self.assertIsNone(mon.pcm_levels(array("h", [0] * 50)))
        self.assertIsNone(mon.pcm_levels(array("h", [0] * 8000))["speech"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_recording_from_disk(self):
        day = datetime(2026, 10, 26, 9, 0, 0)
        folder = mon.AUDIO_DIR / "2026-10-26"
        folder.mkdir(parents=True, exist_ok=True)
        rows = []
        for i, (name, db) in enumerate((("090000_A.mp3", -9), ("090100_B.mp3", -30), ("090200_C_gqrx.mp3", -9))):
            pcm = folder / "x.raw"
            pcm.write_bytes(call(db).tobytes())
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
                            "-i", str(pcm), str(folder / name)], check=True)
            pcm.unlink()
            start = f"2026-10-26 09:0{i}:00"
            rows.append([start, start, "5.0", "Palmetto 800", "Dept", f"Ch {i}", "", "", "", "", "FM", "", "5.0",
                         name])
        write_csv(mon.audio_index_path(day), mon.AUDIO_HEADERS, rows)
        recent = mon.recent_recordings(10, days=1, today=day)
        self.assertEqual([r[-1] for r in recent], ["090100_B.mp3", "090000_A.mp3"])   # newest first, no GQRX
        lv = mon.recording_levels(folder / "090000_A.mp3")
        self.assertAlmostEqual(lv["peak"], -9, delta=1.5)
        quiet = mon.recording_levels(folder / "090100_B.mp3")
        self.assertLess(quiet["peak"], -25)


class SummaryTests(unittest.TestCase):
    def rows(self, levels):
        return [(["", "", "", "", "", f"Ch {i % 2}"], lv) for i, lv in enumerate(levels)]

    def test_good(self):
        s = mon.level_summary(self.rows([mon.pcm_levels(call(-8))] * 6))
        self.assertEqual(s["advice"], "ok")
        self.assertIn("Good", s["verdict"])
        self.assertEqual(len(s["channels"]), 2)

    def test_quiet(self):
        s = mon.level_summary(self.rows([mon.pcm_levels(call(-26))] * 6))
        self.assertEqual(s["advice"], "raise")
        self.assertAlmostEqual(s["change_db"], 20, delta=1)
        self.assertIn("Too quiet", s["verdict"])

    def test_clipped(self):
        s = mon.level_summary(self.rows([mon.pcm_levels(call(6))] + [mon.pcm_levels(call(-8))] * 5))
        self.assertEqual(s["advice"], "lower")
        self.assertIn("clipped", s["verdict"])

    def test_noisy(self):
        s = mon.level_summary(self.rows([mon.pcm_levels(call(-8, noise_db=-18))] * 4))
        self.assertIn("not the volume", s["verdict"])

    def test_nothing(self):
        self.assertEqual(mon.level_summary([])["count"], 0)


class TestSummaryTests(unittest.TestCase):
    def reply(self, logprob, text="Engine 12 copy"):
        return {"type": "result", "text": text, "logprob": logprob}

    def test_normalized_wins(self):
        results = {f"{i}.mp3": {"As recorded": self.reply(-0.6), "6 dB quieter": self.reply(-0.8),
                                "6 dB louder": self.reply(-0.45), "12 dB louder": self.reply(-0.5),
                                "Normalized": self.reply(-0.3)} for i in range(4)}
        s = mon.level_test_summary(results)
        self.assertEqual(s["best"], "Normalized")
        self.assertIn("Normalize", s["verdict"])
        self.assertEqual(next(x for x in s["scores"] if x["label"] == "Normalized")["better"], 4)

    def test_no_change(self):
        results = {"a.mp3": {"As recorded": self.reply(-0.3), "Normalized": self.reply(-0.31)}}
        self.assertEqual(mon.level_test_summary(results)["best"], "As recorded")

    def test_confidence(self):
        self.assertEqual(mon.test_confidence(self.reply(0.0)), 1.0)
        self.assertEqual(mon.test_confidence(self.reply(-0.5, "")), 0.0)
        self.assertEqual(mon.test_confidence({"type": "error"}), 0.0)
        self.assertIsNone(mon.level_test_summary({})["best"])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("needs numpy (the engine has it)")
        self.tw = worker()

    def test_normalize_and_gain(self):
        import numpy as np
        quiet = np.array(call(-30), dtype=np.float32) / 32768
        level, peak = self.tw.level_stats(quiet)
        out = self.tw.apply_level(quiet, normalize=True)
        level2, peak2 = self.tw.level_stats(out)
        self.assertAlmostEqual(20 * math.log10(level2), -20, delta=1)
        self.assertLessEqual(peak2, 0.9)
        loud = self.tw.apply_level(quiet, gain_db=40)
        self.assertLessEqual(float(np.max(np.abs(loud))), 1.0)                     # clipped like a real volume
        self.assertIs(self.tw.apply_level("path.mp3", normalize=True), "path.mp3")

    def test_stats(self):
        seg = SimpleNamespace(text=" Copy.", start=0.0, end=2.0, no_speech_prob=0.1, avg_logprob=-0.4,
                              compression_ratio=1.1, words=[])
        stats = {}
        self.tw.clean([seg], stats)
        self.assertEqual(stats["logprob"], -0.4)
        self.assertEqual(stats["speech_seconds"], 2.0)


class TranscriberTests(unittest.TestCase):
    def test_test_jobs_are_not_saved(self):
        m = monitor(fresh=True)
        t = mon.Transcriber(m)
        written = []
        t.proc = SimpleNamespace(write=lambda b: written.append(b))
        t.ready = True
        t.test_done = mock.Mock()
        row = ["2026-10-26 09:00:00", "", "5", "Palmetto 800", "Dept", "Ch 0", "", "", "", "", "", "", "5.0",
               "090000_A.mp3"]
        with mock.patch.object(t, "ensure_worker", return_value=True), \
                mock.patch.object(mon, "save_transcript") as saved:
            t.submit_test({"row": row, "gain_db": 6, "normalize": False, "label": "6 dB louder"})
            job = mon.json.loads(written[-1])
            self.assertEqual(job["gain_db"], 6.0)
            self.assertFalse(job["words"])
            t.handle({"type": "result", "id": job["id"], "text": "Copy", "logprob": -0.2})
            saved.assert_not_called()
        self.assertEqual(t.test_done.emit.call_args[0][0]["label"], "6 dB louder")
        self.assertIsNone(t.inflight)

    def test_real_calls_go_first_and_normalize(self):
        m = monitor(fresh=True)
        m.settings.setValue("transcribe/normalize", "true")
        t = mon.Transcriber(m)
        t.tests.append({"row": ["2026-10-26 09:00:00", "", "", "", "", "x", "", "", "", "", "", "", "1", "t.mp3"]})
        t.queue.append(["2026-10-26 09:00:00", "", "", "", "", "Ch 0", "", "", "", "", "", "", "1", "090000_A.mp3"])
        written = []
        t.proc = SimpleNamespace(write=lambda b: written.append(b))
        t.ready = True
        with mock.patch.object(mon.Path, "exists", return_value=True):
            t.pump()
        job = mon.json.loads(written[-1])
        self.assertTrue(job["normalize"])
        self.assertNotIn("gain_db", job)
        self.assertEqual(len(t.tests), 1)


class WindowTests(unittest.TestCase):
    def test_window(self):
        m = monitor(fresh=True)
        w = mon.LevelCheckWindow(m)
        w.isVisible = lambda: True
        w.on_capturing(True)
        w.on_level(-30.0)
        w.on_capturing(False)
        self.assertEqual(w.live_counts["quiet"], 1)
        w.on_capturing(True)
        w.on_level(0.0)
        w.on_capturing(False)
        self.assertEqual(w.live_counts["clipped"], 1)
        w.show_measured([(["", "", "", "", "", "Ch"], mon.pcm_levels(call(-26)))])
        self.assertIn("Too quiet", w.recent_verdict.text())
        w.test_run = 3
        w.test_total = 2
        w.test_results = {"a.mp3": {}}
        w.test_rows = {"a.mp3": ["2026-10-26 09:00:00", "", "", "", "", "Ch"]}
        w.on_test_done({"owner": "levels", "run": 3, "row": ["", "a.mp3"], "label": "As recorded"},
                       {"type": "result", "text": "Copy", "logprob": -0.5})
        w.on_test_done({"owner": "levels", "run": 2, "row": ["", "a.mp3"], "label": "Normalized"},
                       {"type": "result", "text": "Copy", "logprob": -0.1})            # an old run: ignored
        self.assertEqual(list(w.test_results["a.mp3"]), ["As recorded"])
        w.set_normalize(True)
        self.assertEqual(m.settings.value("transcribe/normalize"), "true")



class FromSettingsTests(unittest.TestCase):
    def test_settings_close_before_the_level_check_opens(self):
        # v1.1.110: the modal settings window blocked the level check opened from it
        m = monitor(fresh=True)
        d = mon.TranscribeSettingsDialog(m)
        d.installer = None
        with mock.patch.object(d, "save") as save, mock.patch.object(mon.QTimer, "singleShot") as later:
            d.open_levels()
        save.assert_called_once()
        self.assertEqual(later.call_args[0][1], m.open_levels)


if __name__ == "__main__":
    unittest.main()
