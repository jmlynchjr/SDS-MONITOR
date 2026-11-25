# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""v1.1.99: the Scanner Control panel gets narrower with its contents instead of scrolling sideways."""
import unittest

from support import mon, monitor


class FakeButton:
    def __init__(self, width):
        self.w = width

    def sizeHint(self):
        return type("Size", (), {"width": lambda _s: self.w})()


class ReflowTests(unittest.TestCase):
    def test_columns_follow_the_width(self):
        buttons = [FakeButton(w) for w in (120, 150, 130, 140)]
        box = mon.ReflowButtons(buttons, 2)
        box.grid.horizontalSpacing = lambda: 6
        self.assertEqual(box.current, 2)
        self.assertEqual(box.needed(2), 306)
        self.assertEqual(box.fit(400), 2)
        self.assertEqual(box.fit(306), 2)
        self.assertEqual(box.fit(305), 1)                  # too narrow for two side by side
        box.arrange(1)
        self.assertEqual(box.current, 1)
        self.assertEqual(mon.ReflowButtons(buttons, 1).fit(50), 1)

    def test_panel_uses_it(self):
        monitor(fresh=True)                                 # builds the panel with let_panel_shrink
        with open(mon.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("let_panel_shrink(control_content)", source)
        self.assertIn("ScrollBarAlwaysOff", source)
        self.assertEqual(source.count("ReflowButtons(["), 2)   # clock and PC audio buttons
        self.assertIn("ReflowButtons(self.control_buttons, 2)", source)
        mon.let_panel_shrink(mon.QWidget())                 # harmless on the stand-in


if __name__ == "__main__":
    unittest.main()
