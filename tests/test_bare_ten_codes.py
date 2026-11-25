# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.107: "1081" written by the speech engine is recognised as 10-81 when nothing says it's something else."""
import unittest

from support import mon

LIST = {"name": "Test", "enabled": True, "agencies": [], "area": None,
        "codes": [{"code": "10-81", "meaning": "Breathalyzer", "details": ""},
                  {"code": "10-4", "meaning": "Acknowledged", "details": ""},
                  {"code": "10-50", "meaning": "Accident", "details": "B. Personal injury"}]}
RANKED = [(LIST, 2, "anywhere")]


def codes(text):
    return [(raw, code) for raw, code, _m, _l in mon.decode_ten_codes(text, RANKED)]


class BareTests(unittest.TestCase):
    def test_recognised(self):
        self.assertEqual(codes("Charlie 40, I'm going to be 1081 with one."), [("1081", "10-81")])
        self.assertEqual(codes("1081 at the jail"), [("1081", "10-81")])
        self.assertEqual(codes("Copy, 104."), [("104", "10-4")])
        self.assertEqual(codes("we have a 1050B on 85"), [("1050B", "10-50B")])

    def test_left_alone(self):
        for text in ("respond to 1081 Main Street", "the 1050 block of Laurens Road", "Engine 1081 en route",
                     "Station 1050 tone", "Highway 1012", "apartment 1081", "it's 104 degrees",
                     "call 864-555-1081", "$1050", "10.81 miles", "at 1081 North Pleasantburg Dr",
                     "unit 104, respond", "traffic from Greer 1081 copy", "104 units on scene", "it was 10:50"):
            self.assertEqual(codes(text), [], text)

    def test_unknown_code_is_not_reported(self):
        self.assertEqual(codes("he is 1077 now"), [])                 # no 10-77 in the list

    def test_spoken_and_bare_once(self):
        self.assertEqual(codes("10-81, yes 1081"), [("10-81", "10-81")])

    def test_prompt_has_no_code_list(self):
        # v1.1.108: a list of codes in the prompt made the engine write "10, 10, 10, …" loops
        row = ["", "", "", "Palmetto 800", "Greenville County", "Sheriff Dispatch"]
        self.assertNotIn("10-", mon.build_transcribe_prompt(row, "", []))


if __name__ == "__main__":
    unittest.main()
