# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""1.1.112: tables and lists keep room for their rows; tab pages scroll instead of squeezing."""
import unittest
from unittest import mock

from support import mon, monitor


class RuleTests(unittest.TestCase):
    def test_keep_rows(self):
        table = mock.Mock()
        table.verticalHeader.return_value.defaultSectionSize.return_value = 30
        table.horizontalHeader.return_value.sizeHint.return_value.height.return_value = 32
        mon.keep_rows(table, 6)
        table.setMinimumHeight.assert_called_once_with(32 + 180 + 6)
        broken = mock.Mock()
        broken.verticalHeader.side_effect = AttributeError
        mon.keep_rows(broken, 5)
        broken.setMinimumHeight.assert_called_once_with(186)

    def test_keep_items(self):
        listing = mock.Mock()
        listing.count.return_value = 0
        mon.keep_items(listing, 4)
        listing.setMinimumHeight.assert_called_once_with(104)

    def test_fit_columns(self):
        table = mock.Mock()
        table.columnCount.return_value = 3
        mon.fit_columns(table)
        modes = [c.args[1] for c in table.horizontalHeader.return_value.setSectionResizeMode.call_args_list]
        self.assertEqual(modes[:2], [mon.QHeaderView.ResizeMode.ResizeToContents] * 2)
        self.assertEqual(modes[-1], mon.QHeaderView.ResizeMode.Stretch)

    def test_level_window_pages_scroll(self):
        with mock.patch.object(mon, "scroll_page", wraps=mon.scroll_page) as pages:
            mon.LevelCheckWindow(monitor(fresh=True))
        self.assertEqual(pages.call_count, 4)


if __name__ == "__main__":
    unittest.main()
