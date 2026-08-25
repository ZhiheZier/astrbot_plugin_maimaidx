import unittest

from ..command.mai_table import parse_plate_table_args, parse_table_request


class PlateTableArgumentTest(unittest.TestCase):
    def test_wu_table_supports_two_pages(self):
        self.assertEqual(parse_table_request('舞神完成表1'), ('舞神', 1))
        self.assertEqual(parse_table_request('舞神完成表 2'), ('舞神', 2))
        self.assertEqual(parse_plate_table_args('舞神', 1), ('舞', '神', 1))
        self.assertEqual(parse_plate_table_args('舞神', 2), ('舞', '神', 2))

    def test_bazhe_table_supports_two_pages(self):
        self.assertEqual(parse_plate_table_args('霸者', 2), ('霸', '者', 2))

    def test_normal_plate_does_not_accept_page(self):
        self.assertIsNone(parse_plate_table_args('彩将', 2))

    def test_page_before_table_suffix_is_not_supported(self):
        self.assertIsNone(parse_plate_table_args('舞神1'))


if __name__ == '__main__':
    unittest.main()
