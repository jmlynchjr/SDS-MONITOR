# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.105: transcripts split by who was talking, from word times and the radios' keyups."""
import importlib.util
import unittest
from datetime import datetime
from types import SimpleNamespace

from support import mon
from test_incident_fixes import write_csv

DAY = "2026-10-24"
DISPATCH = [[0.6, 1.0, " Engine"], [1.0, 1.3, " 12,"], [1.4, 1.9, " respond"], [1.9, 2.2, " to"],
            [2.2, 2.6, " 100"], [2.6, 3.0, " Main."]]
ENGINE = [[3.9, 4.3, " Engine"], [4.3, 4.6, " 12"], [4.7, 5.0, " copy,"], [5.0, 5.5, " en"], [5.5, 5.9, " route."]]
WORDS = DISPATCH + ENGINE


class SplitTests(unittest.TestCase):
    def test_two_radios(self):
        parts = mon.split_speakers(WORDS, [(0.5, "3231467"), (4.0, "3237894")])
        self.assertEqual([p["unit"] for p in parts], ["3231467", "3237894"])
        self.assertEqual(parts[0]["text"], "Engine 12, respond to 100 Main.")
        self.assertEqual(parts[1]["text"], "Engine 12 copy, en route.")
        self.assertEqual(parts[1]["at"], 3.9)

    def test_late_keyup_snaps_to_the_pause(self):
        # The scanner reported the engine a third of a second late: the split still falls in the pause.
        parts = mon.split_speakers(WORDS, [(0.5, "A"), (4.3, "B")])
        self.assertEqual(parts[1]["text"], "Engine 12 copy, en route.")
        early = mon.split_speakers(WORDS, [(0.5, "A"), (3.5, "B")])
        self.assertEqual(early[1]["text"], "Engine 12 copy, en route.")

    def test_same_radio_again_joins(self):
        parts = mon.split_speakers(WORDS, [(0.5, "A"), (3.2, "B"), (3.4, "A")])
        self.assertEqual([p["unit"] for p in parts], ["A"])

    def test_no_units_splits_at_long_pauses(self):
        words = DISPATCH + [[w[0] + 2, w[1] + 2, w[2]] for w in ENGINE]
        parts = mon.split_speakers(words, [])
        self.assertEqual(len(parts), 2)
        self.assertIsNone(parts[0]["unit"])
        self.assertEqual(len(mon.split_speakers(WORDS, [])), 1)          # 0.9 s isn't long enough

    def test_text(self):
        parts = mon.split_speakers(WORDS, [(0.5, "3231467"), (4.0, "3237894")])
        text = mon.parts_text(parts, lambda u: {"3231467": "Dispatch"}.get(u, u))
        self.assertEqual(text, "Dispatch: Engine 12, respond to 100 Main.\n3237894: Engine 12 copy, en route.")
        self.assertEqual(mon.parts_text(parts[:1]), "")
        self.assertEqual(mon.said_text({"text": "x", "parts": None}), "x")
        self.assertEqual(mon.split_speakers([], [(0, "A")]), [])
        self.assertEqual(mon.split_speakers([["bad"]], []), [])


class CaptureTests(unittest.TestCase):
    def test_lead_and_marks(self):
        c = mon.AudioCapture()
        c.source, c.frame, c.bytes_per_sec = object(), 2, 16000
        c.preroll, c.stream_url, c.stream_delay = 1.5, None, 1.0
        c.ring, c.ring_start, c.total, c.last_end = bytearray(64000), 0, 64000, 0
        c.begin()
        self.assertEqual(c.current["lead"], 1.5)
        c.total += 16000 * 2
        c.mark_unit("3231467")
        c.mark_unit("3231467")
        c.total += 8000
        c.mark_unit("3237894")
        self.assertEqual(c.current["marks"], [[3.5, "3231467"], [4.0, "3237894"]])
        c.current["meta"] = {"row": []}
        c.mark_unit("x")                                  # after the reception ended: not marked
        self.assertEqual(len(c.current["marks"]), 2)

    def test_network_stream_is_later(self):
        c = mon.AudioCapture()
        c.source, c.frame, c.bytes_per_sec = object(), 2, 16000
        c.preroll, c.stream_url, c.stream_delay = 1.0, "rtsp://x/y", 1.0
        c.ring, c.ring_start, c.total, c.last_end = bytearray(32000), 0, 32000, 0
        c.begin()
        self.assertEqual(c.current["lead"], 2.0)


def write_files(marks=True, model="small.en"):
    when = datetime.strptime(DAY, "%Y-%m-%d")
    start = f"{DAY} 10:00:00"
    write_csv(mon.audio_index_path(when), mon.AUDIO_HEADERS, [
        [start, f"{DAY} 10:00:05", "5.0", "Palmetto 800", "Greenville County", "Sheriff Dispatch",
         "859.262500MHz", "TGID:44002", "", "UID:3231467; UID:3237894", "P25", "-50", "7.5", "100000.mp3"]])
    write_csv(mon.unit_log_path(when), mon.UNIT_HEADERS, [
        [start, "UID:3231467", "", "Palmetto 800", "Simulcast", "Greenville County", "Sheriff Dispatch",
         "TGID:44002", "859.2625"],
        [f"{DAY} 10:00:02", "UID:3237894", "", "Palmetto 800", "Simulcast", "Greenville County",
         "Sheriff Dispatch", "TGID:44002", "859.2625"]])
    tr = [start, "Palmetto 800", "Greenville County", "Sheriff Dispatch", "TGID:44002",
          "UID:3231467; UID:3237894", "7.5", "100000.mp3", mon._join_words(WORDS), model, start]
    write_csv(mon.transcript_path(DAY), mon.TRANSCRIPT_HEADERS, [tr])
    for path in (mon.transcript_words_path(DAY), mon.recording_marks_path(DAY)):
        path.unlink(missing_ok=True)
    mon.save_transcript_words(DAY, "100000.mp3", WORDS)
    if marks:
        mon.save_recording_marks(when, "100000.mp3", 0.5, [[0.5, "3231467"], [4.1, "3237894"]])
    return tr


class FileTests(unittest.TestCase):
    def test_with_marks(self):
        tr = write_files()
        parts = mon.transcript_parts(tr)
        self.assertEqual([p["unit"] for p in parts], ["3231467", "3237894"])
        recs = mon.receptions_with_units(DAY) if mon.read_reception_day(DAY) else []
        self.assertIsInstance(recs, list)

    def test_from_the_unit_log(self):
        tr = write_files(marks=False)
        parts = mon.transcript_parts(tr)                  # lead estimated 1.5, radio at +2 s → 3.5 s
        self.assertEqual([p["unit"] for p in parts], ["3231467", "3237894"])
        self.assertEqual(parts[1]["text"], "Engine 12 copy, en route.")

    def test_corrected_and_old(self):
        tr = write_files(model="small.en (corrected)")
        self.assertIsNone(mon.transcript_parts(tr))
        tr = write_files()
        mon.transcript_words_path(DAY).unlink()
        self.assertIsNone(mon.transcript_parts(tr))

    def test_later_line_wins(self):
        tr = write_files()
        mon.save_transcript_words(DAY, "100000.mp3", [])
        self.assertIsNone(mon.transcript_parts(tr))

    def test_name_evidence_uses_split_calls(self):
        tr = write_files()
        ev = mon.NameEvidence(mon.callsign_words(mon.CALLSIGN_WORDS_DEFAULT))
        self.assertEqual(ev.add(tr), "3237894")
        self.assertEqual(ev.votes["3237894"]["Engine 12"], 1)
        self.assertEqual(ev.votes["3231467"]["Engine 12"], 1)            # dispatch named the engine too


class StoryTests(unittest.TestCase):
    def test_step_goes_to_the_speaker(self):
        t = datetime(2026, 10, 24, 10, 0, 0)
        parts = [{"unit": "3231467", "at": 0.6, "text": "Engine 12 respond to 100 Main."},
                 {"unit": "3237894", "at": 3.9, "text": "Engine 12 copy, en route."}]
        r = {"start": t, "end": t, "label": "Sheriff Dispatch", "system": "Palmetto 800",
             "units": ["3231467", "3237894", "3239999"], "text": "x en route", "parts": parts}
        inc = {"members": [r], "start": t, "end": t, "units": r["units"]}
        story = mon.incident_story(inc, [[]])
        self.assertEqual(list(story["units"]), ["3237894"])
        r["parts"] = None
        story = mon.incident_story(inc, [[]])
        self.assertEqual(len(story["units"]), 3)                        # unsplit: every radio, as before


class WorkerTests(unittest.TestCase):
    def test_clean_returns_words(self):
        spec = importlib.util.spec_from_file_location("tw", mon.Path(mon.__file__).with_name("transcribe_worker.py"))
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        w = lambda a, b, t: SimpleNamespace(start=a, end=b, word=t)
        segs = [SimpleNamespace(text=" Engine 12 copy.", start=0.5, end=1.5, no_speech_prob=0.1, avg_logprob=-0.2,
                                words=[w(0.5, 0.9, " Engine"), w(0.9, 1.1, " 12"), w(1.1, 1.5, " copy.")]),
                SimpleNamespace(text=" noise", start=2, end=3, no_speech_prob=0.9, avg_logprob=-2, words=[]),
                SimpleNamespace(text=" Ten four.", start=4, end=5, no_speech_prob=0.1, avg_logprob=-0.2, words=None)]
        text, words = tw.clean(segs)
        self.assertEqual(text, "Engine 12 copy. Ten four.")
        self.assertEqual(words[0], [0.5, 0.9, " Engine"])
        self.assertEqual(words[-1], [4, 5, " Ten four."])
        self.assertEqual(tw.clean([SimpleNamespace(text="Thank you.", start=0, end=1, words=[])]), ("", []))

    def test_loops_are_dropped(self):
        spec = importlib.util.spec_from_file_location("tw", mon.Path(mon.__file__).with_name("transcribe_worker.py"))
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        w = lambda a, t: SimpleNamespace(start=a, end=a + 0.3, word=t)
        loop = SimpleNamespace(text=" x", start=0, end=6, no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=1.5,
                               words=[w(0, " Copy,")] + [w(i, " 10,") for i in range(1, 9)] + [w(9, " okay.")])
        text, words = tw.clean([loop])
        self.assertEqual(text, "Copy, okay.")
        self.assertEqual(len(words), 2)
        ok = SimpleNamespace(text=" 104, 104.", start=0, end=2, no_speech_prob=0.1, avg_logprob=-0.3,
                             compression_ratio=1.2, words=[w(0, " 104,"), w(1, " 104.")])
        self.assertEqual(tw.clean([ok])[0], "104, 104.")             # a couple of repeats are speech
        stuck = SimpleNamespace(text=" ten ten ten ten ten ten", start=0, end=3, no_speech_prob=0.1,
                                avg_logprob=-0.3, compression_ratio=3.1, words=[])
        self.assertEqual(tw.clean([stuck]), ("", []))
        self.assertEqual(tw.cut_text_loops("10, 10, 10, 10, 10, demo."), "demo.")


if __name__ == "__main__":
    unittest.main()
