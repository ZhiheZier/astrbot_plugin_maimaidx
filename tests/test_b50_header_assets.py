import unittest

from ..libraries.maimai_best_50 import (
    collection_asset_name,
    dani_plate_asset_name,
    format_b50_footer,
    rating_asset_name,
    rating_star_asset_name,
    trophy_asset_name,
)
from ..libraries.maimaidx_lxns import LxnsBest50, LxnsPlayer, lxns_best50_to_best50
from ..libraries.maimaidx_play_result import Collection, best50_to_userinfo
from ..libraries.maimaidx_user import ServiceName, Theme


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

    def test_collection_asset_names_are_zero_padded(self):
        self.assertEqual(collection_asset_name('icon', 123), 'UI_Icon_000123.png')
        self.assertEqual(collection_asset_name('plate', 456), 'UI_Plate_000456.png')

    def test_trophy_color_falls_back_to_rainbow(self):
        self.assertEqual(trophy_asset_name('Gold'), 'UI_CMN_Shougou_Gold.png')
        self.assertEqual(trophy_asset_name('unknown'), 'UI_CMN_Shougou_Rainbow.png')
        self.assertEqual(trophy_asset_name(None), 'UI_CMN_Shougou_Rainbow.png')

    def test_lxns_collections_survive_player_conversion(self):
        lxns_player = LxnsPlayer.model_validate(
            {
                'name': 'TEST',
                'rating': 15000,
                'course_rank': 12,
                'class_rank': 5,
                'trophy': {'id': 1, 'name': '虹色称号', 'color': 'Rainbow'},
                'icon': {'id': 2, 'name': '头像'},
                'name_plate': {'id': 3, 'name': '姓名框'},
                'frame': {'id': 4, 'name': '框'},
            }
        )

        player, best50 = lxns_best50_to_best50(lxns_player, LxnsBest50())

        self.assertEqual(player.trophy.name, '虹色称号')
        self.assertEqual(player.icon.id, 2)
        self.assertEqual(player.name_plate.id, 3)
        self.assertEqual(player.frame.id, 4)
        self.assertEqual(player.class_rank, 5)
        # 绘图兼容桥接不应把 Collection 塞进只接受字符串的旧 plate 字段。
        self.assertIsNone(best50_to_userinfo(player, best50).plate)

    def test_collection_model_accepts_minimal_payload(self):
        collection = Collection.model_validate({'id': 7, 'name': '收藏品'})
        self.assertEqual(collection.id, 7)
        self.assertIsNone(collection.color)

    def test_footer_identifies_score_source(self):
        lxns_footer = format_b50_footer(ServiceName.LXNS)
        divingfish_footer = format_b50_footer(ServiceName.DIVINGFISH)

        self.assertIn('Data from Lxns-Network.', lxns_footer)
        self.assertIn('Data from Diving-Fish.', divingfish_footer)
        self.assertTrue(lxns_footer.endswith(' BOT'))


if __name__ == '__main__':
    unittest.main()
