import unittest

from ..libraries.maimaidx_rating_table_layout import (
    level_15_height,
    level_15_position,
    level_15_rows,
)


class Level15RatingTableLayoutTest(unittest.TestCase):
    def test_positions_use_three_column_upstream_layout(self):
        self.assertEqual(level_15_position(0), (100, 500))
        self.assertEqual(level_15_position(2), (950, 500))
        self.assertEqual(level_15_position(3), (100, 950))

    def test_height_reserves_one_large_row_per_three_charts(self):
        self.assertEqual(level_15_rows(0), 0)
        self.assertEqual(level_15_rows(1), 1)
        self.assertEqual(level_15_rows(3), 1)
        self.assertEqual(level_15_rows(4), 2)
        self.assertEqual(level_15_height(4), 1550)
