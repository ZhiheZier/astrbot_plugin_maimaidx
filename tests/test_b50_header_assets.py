import unittest

from ..libraries.maimai_best_50 import (
    dani_plate_asset_name,
    rating_asset_name,
    rating_star_asset_name,
)
from ..libraries.maimaidx_user import Theme


class TestB50HeaderAssets(unittest.TestCase):

    def test_circle_rating_tiers(self):
        cases = {
            999: '01',
            1000: '02',
            13999: '08',
            14000: '09',
            14500: '10',
            15000: '11',
            15999: '11',
            16000: '12',
            17000: '12',
        }
        for rating, tier in cases.items():
            with self.subTest(rating=rating):
                self.assertEqual(
                    rating_asset_name(rating, Theme.CIRCLE),
                    f'UI_CMN_DXRating_{tier}.png',
                )

    def test_non_circle_high_rating_uses_latest_available_asset(self):
        self.assertEqual(
            rating_asset_name(16000, Theme.PRISM_PLUS),
            'UI_CMN_DXRating_11.png',
        )

    def test_circle_rating_star_tiers(self):
        self.assertEqual(rating_star_asset_name(14000), 'UI_CMN_DXRating_Star_01.png')
        self.assertEqual(rating_star_asset_name(14250), 'UI_CMN_DXRating_Star_02.png')
        self.assertEqual(rating_star_asset_name(16000), 'UI_CMN_DXRating_Star_01.png')
        self.assertEqual(rating_star_asset_name(16750), 'UI_CMN_DXRating_Star_04.png')

    def test_dani_plate_mapping_matches_b50(self):
        self.assertEqual(dani_plate_asset_name(0), 'UI_DNM_DaniPlate_00.png')
        self.assertEqual(dani_plate_asset_name(10), 'UI_DNM_DaniPlate_10.png')
        self.assertEqual(dani_plate_asset_name(11), 'UI_DNM_DaniPlate_12.png')
        self.assertEqual(dani_plate_asset_name(22), 'UI_DNM_DaniPlate_23.png')


if __name__ == '__main__':
    unittest.main()
