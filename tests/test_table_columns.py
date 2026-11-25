# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""v1.1.100: reception history and statistics columns can be moved, hidden and shown again."""
import json
import unittest

from support import mon, monitor


class FakeSignal:
    def connect(self, *_):
        pass


class FakeHeader:
    """Enough of QHeaderView: visual order, hidden sections and widths."""

    def __init__(self, n):
        self.visual = list(range(n))              # visual position -> logical column
        self.hidden = set()
        self.sizes = [100 + c for c in range(n)]
        self.customContextMenuRequested = self.sectionMoved = self.sectionResized = FakeSignal()

    def setSectionsMovable(self, _on): pass
    def setContextMenuPolicy(self, _p): pass
    def setToolTip(self, _t): pass
    def logicalIndex(self, v): return self.visual[v]
    def visualIndex(self, c): return self.visual.index(c)
    def isSectionHidden(self, c): return c in self.hidden
    def sectionSize(self, c): return 0 if c in self.hidden else self.sizes[c]
    def resizeSection(self, c, w): self.sizes[c] = w

    def moveSection(self, frm, to):
        self.visual.insert(to, self.visual.pop(frm))

    def setSectionHidden(self, c, on):
        (self.hidden.add if on else self.hidden.discard)(c)


class Settings(dict):
    def value(self, key, default=None): return self.get(key, default)
    def setValue(self, key, value): self[key] = value
    def remove(self, key): self.pop(key, None)


NAMES = ["Start", "End", "Channel", "Source"]


class ColumnTests(unittest.TestCase):
    def make(self, settings, names=NAMES):
        header = FakeHeader(len(names))
        return header, mon.TableColumns(header, names, settings, "columns/test")

    def test_move_hide_and_remember(self):
        settings = Settings()
        header, cols = self.make(settings)
        header.moveSection(2, 0)                  # Channel first
        cols.set_shown(1, False)                  # hide End
        header.resizeSection(0, 222)
        cols.save()
        saved = json.loads(settings["columns/test"])
        self.assertEqual(saved["order"], ["Channel", "Start", "End", "Source"])
        self.assertEqual(saved["hidden"], ["End"])
        self.assertEqual(saved["widths"]["Start"], 222)
        # Next start: the same columns come back the same way.
        header2, _cols2 = self.make(settings)
        self.assertEqual([NAMES[header2.logicalIndex(v)] for v in range(4)],
                         ["Channel", "Start", "End", "Source"])
        self.assertTrue(header2.isSectionHidden(1))
        self.assertEqual(header2.sectionSize(0), 222)

    def test_new_column_and_last_visible(self):
        settings = Settings({"columns/test": json.dumps({"order": ["Channel", "Start", "End"],
                                                         "hidden": ["Start", "End", "Channel"]})})
        header, cols = self.make(settings)       # saved before "Source" existed, and everything hidden
        self.assertEqual([NAMES[header.logicalIndex(v)] for v in range(4)],
                         ["Channel", "Start", "End", "Source"])
        self.assertEqual(header.hidden, {0, 1, 2})          # the new column shows
        for c in (0, 1, 2):
            cols.set_shown(c, False)
        cols.set_shown(3, False)                  # the last one stays
        self.assertEqual(cols.visible_count(), 1)
        cols.show_all()
        self.assertEqual(cols.visible_count(), 4)

    def test_reset(self):
        settings = Settings()
        header, cols = self.make(settings)
        header.moveSection(3, 0)
        cols.set_shown(2, False)
        header.resizeSection(1, 400)
        cols.reset()
        self.assertEqual(header.visual, [0, 1, 2, 3])
        self.assertEqual(header.hidden, set())
        self.assertEqual(header.sizes[1], 101)
        self.assertNotIn("columns/test", settings)

    def test_never_all_hidden(self):
        header, _cols = self.make(Settings({"columns/test": json.dumps({"hidden": NAMES})}))
        self.assertEqual(header.hidden, set())

    def test_bad_settings_are_ignored(self):
        header, _cols = self.make(Settings({"columns/test": "not json"}))
        self.assertEqual(header.visual, [0, 1, 2, 3])

    def test_used_by_both_tables(self):
        monitor(fresh=True)
        with open(mon.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('"columns/history"', source)
        self.assertIn('"columns/statistics"', source)


if __name__ == "__main__":
    unittest.main()
