import unittest

from ..libraries.maimaidx_music_info import format_fitting, format_rating_gain


class TestMusicInfoFormat(unittest.TestCase):

    def test_fitting_uses_label_and_two_decimals(self):
        self.assertEqual(format_fitting(13.52), '擬 - 13.52')
        self.assertEqual(format_fitting(13.5), '擬 - 13.50')
        self.assertEqual(format_fitting(None), '-')

    def test_rating_gain_uses_up_arrow(self):
        self.assertEqual(format_rating_gain(315, 13), '315(↑13)')
        self.assertEqual(format_rating_gain(315, 0), 315)
        self.assertEqual(format_rating_gain(315, None), 315)


if __name__ == '__main__':
    unittest.main()
