import unittest
from unittest.mock import AsyncMock, patch

from ..libraries.maimai_best_50 import format_best50_summary
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_model import Data, UserInfo, UserInfoDev
from ..libraries.maimaidx_play_result import (
    Best50,
    PlayedResult,
    Player,
    dx_star_from_percentage,
    dx_star_from_scores,
    played_to_playinfodev,
)
from ..libraries.maimaidx_source import (
    get_best50,
    select_ap50_records,
    select_ap_plus50_records,
    select_fitted_b50_records,
    select_star_b50_records,
)


def make_record(
    song_id: int,
    rating: int,
    *,
    fc: str = 'ap',
    achievements: float = 100.0,
) -> PlayedResult:
    return PlayedResult(
        song_id=song_id,
        song_name=f'song-{song_id}',
        level='13',
        level_index=3,
        level_value=13.0,
        type='DX',
        rating=rating,
        achievements=achievements,
        rate='sss',
        fc=fc,
    )


class DivingFishAp50SelectionTest(unittest.TestCase):
    def test_selects_highest_ap35_and_ap15(self):
        records = []
        is_new = {}
        for song_id in range(1, 41):
            records.append(make_record(song_id, 200 + song_id))
            is_new[song_id] = False
        for song_id in range(101, 121):
            records.append(make_record(song_id, 300 + song_id, fc='app'))
            is_new[song_id] = True

        best50 = select_ap50_records(records, is_new.get)

        self.assertEqual(len(best50.sd), 35)
        self.assertEqual(len(best50.dx), 15)
        self.assertEqual([record.song_id for record in best50.sd[:3]], [40, 39, 38])
        self.assertEqual([record.song_id for record in best50.dx[:3]], [120, 119, 118])
        self.assertEqual(best50.sd_total, sum(record.rating for record in best50.sd))
        self.assertEqual(best50.dx_total, sum(record.rating for record in best50.dx))

    def test_includes_ap_and_app_only(self):
        records = [
            make_record(1, 100, fc='ap'),
            make_record(2, 200, fc='APP'),
            make_record(3, 999, fc='fcp'),
            make_record(4, 999, fc='fc'),
            make_record(5, 999, fc=''),
        ]
        best50 = select_ap50_records(records, lambda _song_id: False)

        self.assertEqual([record.song_id for record in best50.sd], [2, 1])
        self.assertEqual(best50.sd_total, 300)
        self.assertEqual(best50.dx, [])
        self.assertEqual(best50.dx_total, 0)

    def test_skips_records_without_music_metadata(self):
        records = [make_record(1, 100), make_record(2, 200)]
        best50 = select_ap50_records(
            records,
            lambda song_id: False if song_id == 1 else None,
        )

        self.assertEqual([record.song_id for record in best50.sd], [1])


class DxStarBest50SelectionTest(unittest.TestCase):
    def test_dx_score_star_boundaries(self):
        self.assertEqual(dx_star_from_percentage(84.999), 0)
        self.assertEqual(dx_star_from_percentage(85), 1)
        self.assertEqual(dx_star_from_percentage(90), 2)
        self.assertEqual(dx_star_from_percentage(93), 3)
        self.assertEqual(dx_star_from_percentage(95), 4)
        self.assertEqual(dx_star_from_percentage(97), 5)
        self.assertEqual(dx_star_from_scores(930, 1000), 3)
        self.assertIsNone(dx_star_from_scores(100, 0))

    def test_selects_at_least_requested_star_and_applies_35_15_caps(self):
        records = []
        stars = {}
        is_new = {}
        for song_id in range(1, 41):
            records.append(make_record(song_id, 200 + song_id))
            stars[song_id] = 3 if song_id > 2 else 2
            is_new[song_id] = False
        for song_id in range(101, 121):
            records.append(make_record(song_id, 300 + song_id))
            stars[song_id] = 4
            is_new[song_id] = True

        best50 = select_star_b50_records(
            records,
            3,
            is_new.get,
            lambda record: stars.get(record.song_id),
        )

        self.assertEqual(len(best50.sd), 35)
        self.assertEqual(len(best50.dx), 15)
        self.assertNotIn(1, [record.song_id for record in best50.sd])
        self.assertNotIn(2, [record.song_id for record in best50.sd])
        self.assertEqual([record.song_id for record in best50.sd[:3]], [40, 39, 38])
        self.assertEqual([record.song_id for record in best50.dx[:3]], [120, 119, 118])

    def test_five_star_only_accepts_five_star_records(self):
        records = [make_record(song_id, song_id * 100) for song_id in range(1, 6)]
        best50 = select_star_b50_records(
            records,
            5,
            lambda _song_id: False,
            lambda record: record.song_id,
        )

        self.assertEqual([record.song_id for record in best50.sd], [5])

    def test_rejects_invalid_star(self):
        with self.assertRaises(ValueError):
            select_star_b50_records([], 0, lambda _song_id: False, lambda _: 0)


class FittedBest50SelectionTest(unittest.TestCase):
    def test_recalculates_rating_with_fitted_level_value_and_resorts(self):
        records = [
            make_record(1, 999, achievements=100.0),
            make_record(2, 100, achievements=100.0),
            make_record(3, 500, achievements=100.0),
        ]
        fitted_values = {1: 13.5, 2: 14.8, 3: None}

        best50 = select_fitted_b50_records(
            records,
            lambda song_id: song_id == 2,
            lambda record: fitted_values[record.song_id],
            lambda level_value, _achievements: int(level_value * 100),
        )

        self.assertEqual([record.song_id for record in best50.sd], [1])
        self.assertEqual([record.song_id for record in best50.dx], [2])
        self.assertEqual(best50.sd[0].level_value, 13.5)
        self.assertEqual(best50.sd[0].rating, 1350)
        self.assertEqual(best50.dx[0].level_value, 14.8)
        self.assertEqual(best50.dx[0].rating, 1480)
        self.assertEqual(records[0].level_value, 13.0)
        self.assertEqual(records[0].rating, 999)


class ApPlusBest50SelectionTest(unittest.TestCase):
    def test_selects_player_app_records_only_with_35_and_15_caps(self):
        records = []
        is_new = {}
        for song_id in range(1, 41):
            records.append(make_record(song_id, 200 + song_id, fc='app'))
            is_new[song_id] = False
        for song_id in range(101, 121):
            records.append(make_record(song_id, 300 + song_id, fc='APP'))
            is_new[song_id] = True
        records.extend([
            make_record(1001, 9999, fc='ap'),
            make_record(1002, 9999, fc='fcp'),
            make_record(1003, 9999, fc='fc'),
        ])
        is_new.update({1001: False, 1002: False, 1003: False})

        best50 = select_ap_plus50_records(records, is_new.get)

        self.assertEqual(len(best50.sd), 35)
        self.assertEqual(len(best50.dx), 15)
        self.assertEqual([record.song_id for record in best50.sd[:3]], [40, 39, 38])
        self.assertEqual([record.song_id for record in best50.dx[:3]], [120, 119, 118])
        self.assertTrue(
            all((record.fc or '').lower() == 'app' for record in best50.sd + best50.dx)
        )


class Best50SummaryTest(unittest.TestCase):
    def test_formats_regular_b50_summary(self):
        self.assertEqual(
            format_best50_summary(100, 50, 150),
            'B35: 100 + B15: 50 = 150',
        )

    def test_formats_ap50_summary(self):
        self.assertEqual(
            format_best50_summary(100, 50, 150, all_perfect=True),
            '(ap)B35: 100 + B15: 50 = 150',
        )

    def test_formats_dx_star_b50_summary(self):
        self.assertEqual(
            format_best50_summary(100, 50, 150, min_dx_star=3),
            '(3星)B35: 100 + B15: 50 = 150',
        )

    def test_formats_fitted_b50_summary(self):
        self.assertEqual(
            format_best50_summary(100, 50, 150, fitted=True),
            '(拟合)B35: 100 + B15: 50 = 150',
        )

    def test_formats_ap_plus50_summary(self):
        self.assertEqual(
            format_best50_summary(100, 50, 150, all_perfect_plus=True),
            '(ap+)B35: 100 + B15: 50 = 150',
        )


class Best50RoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_divingfish_ap_plus50_uses_player_dev_records(self):
        old_app = make_record(1, 100, fc='app', achievements=100.75)
        new_app = make_record(2, 200, fc='app', achievements=101.0)
        ignored_ap = make_record(3, 999, fc='ap', achievements=100.5)
        user = UserInfoDev(
            additional_rating=0,
            nickname='player',
            plate=None,
            rating=15000,
            username='player',
            records=[
                played_to_playinfodev(old_app),
                played_to_playinfodev(new_app),
                played_to_playinfodev(ignored_ap),
            ],
        )
        query_b50 = AsyncMock()
        query_dev = AsyncMock(return_value=user)

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._is_new_song',
                side_effect=lambda song_id: song_id == 2,
            ),
            patch.object(maiApi, 'token', 'developer-token'),
            patch.object(maiApi, 'query_user_b50', query_b50),
            patch.object(maiApi, 'query_user_get_dev', query_dev),
        ):
            player, best50 = await get_best50(
                qqid=123456,
                all_perfect_plus=True,
            )

        query_dev.assert_awaited_once_with(qqid=123456, username=None)
        query_b50.assert_not_awaited()
        self.assertEqual([record.song_id for record in best50.sd], [1])
        self.assertEqual([record.song_id for record in best50.dx], [2])
        self.assertEqual(best50.sd[0].achievements, 100.75)
        self.assertEqual(best50.dx[0].achievements, 101.0)
        self.assertEqual(player.rating, 300)

    async def test_regular_divingfish_b50_keeps_public_query_path(self):
        user = UserInfo(
            additional_rating=0,
            nickname='player',
            plate=None,
            rating=12345,
            username='player',
            charts=Data(sd=[], dx=[]),
        )
        query_b50 = AsyncMock(return_value=user)
        query_dev = AsyncMock()

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch.object(maiApi, 'query_user_b50', query_b50),
            patch.object(maiApi, 'query_user_get_dev', query_dev),
        ):
            player, best50 = await get_best50(qqid=123456)

        query_b50.assert_awaited_once_with(qqid=123456, username=None)
        query_dev.assert_not_awaited()
        self.assertEqual(player.rating, 12345)
        self.assertEqual(best50.sd, [])
        self.assertEqual(best50.dx, [])

    async def test_divingfish_ap50_uses_dev_records_and_ap_total(self):
        old_ap = make_record(1, 100, fc='ap')
        new_app = make_record(2, 200, fc='app')
        ignored_fc = make_record(3, 999, fc='fc')
        user = UserInfoDev(
            additional_rating=0,
            nickname='player',
            plate=None,
            rating=15000,
            username='player',
            records=[
                played_to_playinfodev(old_ap),
                played_to_playinfodev(new_app),
                played_to_playinfodev(ignored_fc),
            ],
        )
        query_b50 = AsyncMock()
        query_dev = AsyncMock(return_value=user)

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._is_new_song',
                side_effect=lambda song_id: song_id == 2,
            ),
            patch.object(maiApi, 'token', 'developer-token'),
            patch.object(maiApi, 'query_user_b50', query_b50),
            patch.object(maiApi, 'query_user_get_dev', query_dev),
        ):
            player, best50 = await get_best50(
                qqid=123456,
                all_perfect=True,
            )

        query_dev.assert_awaited_once_with(qqid=123456, username=None)
        query_b50.assert_not_awaited()
        self.assertEqual([record.song_id for record in best50.sd], [1])
        self.assertEqual([record.song_id for record in best50.dx], [2])
        self.assertEqual(player.rating, 300)

    async def test_divingfish_star_b50_uses_all_dev_records(self):
        old_record = make_record(1, 100)
        new_record = make_record(2, 200)
        ignored_record = make_record(3, 999)
        user = UserInfoDev(
            additional_rating=0,
            nickname='player',
            plate=None,
            rating=15000,
            username='player',
            records=[
                played_to_playinfodev(old_record),
                played_to_playinfodev(new_record),
                played_to_playinfodev(ignored_record),
            ],
        )
        query_b50 = AsyncMock()
        query_dev = AsyncMock(return_value=user)

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._is_new_song',
                side_effect=lambda song_id: song_id == 2,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._dx_star_for_record',
                side_effect=lambda record: 2 if record.song_id == 3 else 3,
            ),
            patch.object(maiApi, 'token', 'developer-token'),
            patch.object(maiApi, 'query_user_b50', query_b50),
            patch.object(maiApi, 'query_user_get_dev', query_dev),
        ):
            player, best50 = await get_best50(
                qqid=123456,
                min_dx_star=3,
            )

        query_dev.assert_awaited_once_with(qqid=123456, username=None)
        query_b50.assert_not_awaited()
        self.assertEqual([record.song_id for record in best50.sd], [1])
        self.assertEqual([record.song_id for record in best50.dx], [2])
        self.assertEqual(player.rating, 300)

    async def test_divingfish_fitted_b50_uses_all_dev_records(self):
        old_record = make_record(1, 100)
        new_record = make_record(2, 200)
        user = UserInfoDev(
            additional_rating=0,
            nickname='player',
            plate=None,
            rating=15000,
            username='player',
            records=[
                played_to_playinfodev(old_record),
                played_to_playinfodev(new_record),
            ],
        )
        query_b50 = AsyncMock()
        query_dev = AsyncMock(return_value=user)

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._is_new_song',
                side_effect=lambda song_id: song_id == 2,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._fitted_level_value',
                side_effect=lambda record: 13.5 if record.song_id == 1 else 14.8,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._rating_for_level_value',
                side_effect=lambda level_value, _achievements: int(level_value * 100),
            ),
            patch.object(maiApi, 'token', 'developer-token'),
            patch.object(maiApi, 'query_user_b50', query_b50),
            patch.object(maiApi, 'query_user_get_dev', query_dev),
        ):
            player, best50 = await get_best50(qqid=123456, fitted=True)

        query_dev.assert_awaited_once_with(qqid=123456, username=None)
        query_b50.assert_not_awaited()
        self.assertEqual(best50.sd[0].rating, 1350)
        self.assertEqual(best50.dx[0].rating, 1480)
        self.assertEqual(player.rating, 2830)

    async def test_regular_lxns_b50_keeps_player_rating(self):
        player = Player(name='player', rating=15000)
        best50 = Best50(sd_total=100, dx_total=200)
        lxns_b50 = AsyncMock(return_value=(player, best50))

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=True,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._lxns_best50_raw',
                lxns_b50,
            ),
        ):
            result_player, _ = await get_best50(qqid=123456)

        self.assertEqual(result_player.rating, 15000)

    async def test_lxns_ap50_uses_selected_score_total(self):
        player = Player(name='player', rating=15000)
        best50 = Best50(sd_total=100, dx_total=200)
        lxns_b50 = AsyncMock(return_value=(player, best50))

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=True,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._lxns_best50_raw',
                lxns_b50,
            ),
        ):
            result_player, _ = await get_best50(
                qqid=123456,
                all_perfect=True,
            )

        self.assertEqual(result_player.rating, 300)


if __name__ == '__main__':
    unittest.main()
