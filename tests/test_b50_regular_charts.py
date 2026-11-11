import unittest
from unittest.mock import AsyncMock, patch

from ..libraries import maimaidx_source as source
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_play_result import Best50, Player, best50_to_userinfo
from .test_divingfish_ap50 import make_record


class RegularB50SelectionTest(unittest.TestCase):
    def test_all_selectors_exclude_utage_before_limits_and_totals(self):
        records = [make_record(index, index, fc='app', achievements=96.9)
                   for index in range(1, 81)]
        records.extend([
            make_record(100000, 99999, fc='app', achievements=96.9),
            make_record(100001, 99999, fc='app', achievements=96.9),
            make_record(999, 99999, fc='app', achievements=96.9).model_copy(update={'level_index': 5}),
        ])
        is_new = lambda song_id: song_id > 40
        selectors = {
            'all': lambda: source.select_all_songs_b50_records(records),
            'ap': lambda: source.select_ap50_records(records, is_new),
            'ap+': lambda: source.select_ap_plus50_records(records, is_new),
            'difficulty': lambda: source.select_difficulty_b50_records(records, 3, is_new),
            'star': lambda: source.select_star_b50_records(records, 1, is_new, lambda _: 5),
            'fitted': lambda: source.select_fitted_b50_records(
                records, is_new, lambda record: record.rating, lambda value, _: int(value)),
            'under_s': lambda: source.select_achievement_b50_records(records, 'under_s', is_new),
        }
        for name, select in selectors.items():
            with self.subTest(name=name):
                result = select()
                self.assertEqual((len(result.sd), len(result.dx)), (35, 15))
                self.assertTrue(all(record.song_id < 100000 and record.level_index <= 4
                                    for record in result.sd + result.dx))
                self.assertEqual(result.sd_total, sum(record.rating for record in result.sd))
                self.assertEqual(result.dx_total, sum(record.rating for record in result.dx))
                expected = list(range(80, 30, -1)) if name == 'all' else (
                    list(range(40, 5, -1)) + list(range(80, 65, -1)))
                self.assertEqual([record.song_id for record in result.sd + result.dx], expected)

    def test_near_and_lock_also_exclude_utage(self):
        for mode, achievement in (('near', 99.99), ('lock', 100.5)):
            with self.subTest(mode=mode):
                result = source.select_achievement_b50_records([
                    make_record(100001, 99999, achievements=achievement),
                    make_record(1, 100, achievements=achievement),
                ], mode, lambda _: False)
                self.assertEqual([record.song_id for record in result.sd], [1])


class RegularB50RoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_waterfish_and_lxns_best_lists_are_filtered(self):
        player = Player(name='Player', rating=16000)
        result = Best50(sd=[make_record(100001, 99999), make_record(1, 100)],
                        dx=[make_record(100002, 99999), make_record(2, 200)])
        for lxns, ap in ((False, False), (True, False), (True, True)):
            with self.subTest(lxns=lxns, ap=ap):
                with (patch.object(source, 'is_lxns', return_value=lxns),
                      patch.object(source, '_lxns_best50_raw', AsyncMock(return_value=(player, result))),
                      patch.object(maiApi, 'query_user_b50', AsyncMock(
                          return_value=best50_to_userinfo(player, result)))):
                    actual_player, actual = await source.get_best50('123', all_perfect=ap)
                self.assertEqual([record.song_id for record in actual.sd], [1])
                self.assertEqual([record.song_id for record in actual.dx], [2])
                self.assertEqual((actual.sd_total, actual.dx_total), (100, 200))
                self.assertEqual(actual_player.rating, 300 if ap else 16000)

    async def test_legacy_lxns_entry_also_filters_utage(self):
        result = Best50(sd=[make_record(100001, 99999), make_record(1, 100)])
        with patch.object(source, '_lxns_best50_raw', AsyncMock(return_value=(Player(), result))):
            user = await source.lxns_b50('123')
        self.assertEqual([record.song_id for record in source.userinfo_to_best50(user).sd], [1])
