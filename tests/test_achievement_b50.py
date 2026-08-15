import re
import unittest
from unittest.mock import AsyncMock, patch

from ..command.mai_score import (
    ACHIEVEMENT_COMMAND_PATTERNS,
    ALL_SONGS_B50_COMMAND_PATTERN,
    DIFFICULTY_B50_COMMAND_PATTERN,
)
from ..libraries.maimai_best_50 import format_best50_summary
from ..libraries.maimaidx_play_result import Best50, PlayedResult, Player
from ..libraries.maimaidx_source import (
    achievement_matches_mode,
    get_best50,
    select_all_songs_b50_records,
    select_achievement_b50_records,
    select_difficulty_b50_records,
)


def make_record(
    song_id: int,
    rating: int,
    achievements: float,
    *,
    level_index: int = 3,
) -> PlayedResult:
    return PlayedResult(
        song_id=song_id,
        song_name=f'song-{song_id}',
        level='13',
        level_index=level_index,
        level_value=13.0,
        type='DX',
        rating=rating,
        achievements=achievements,
        rate='sss',
    )


class AchievementModeBoundaryTest(unittest.TestCase):
    def test_under_s_boundaries(self):
        self.assertTrue(achievement_matches_mode(96.9999, 'under_s'))
        self.assertFalse(achievement_matches_mode(97.0, 'under_s'))

    def test_near_boundaries(self):
        for value in (99.95, 99.9999, 100.45, 100.4999):
            with self.subTest(value=value):
                self.assertTrue(achievement_matches_mode(value, 'near'))
        for value in (99.9499, 100.0, 100.4499, 100.5):
            with self.subTest(value=value):
                self.assertFalse(achievement_matches_mode(value, 'near'))

    def test_lock_boundaries(self):
        for value in (100.0, 100.05, 100.5, 100.55):
            with self.subTest(value=value):
                self.assertTrue(achievement_matches_mode(value, 'lock'))
        for value in (99.9999, 100.0501, 100.4999, 100.5501):
            with self.subTest(value=value):
                self.assertFalse(achievement_matches_mode(value, 'lock'))

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            achievement_matches_mode(100.0, 'unknown')


class AchievementBest50SelectionTest(unittest.TestCase):
    def test_applies_35_15_caps_and_sorts_by_rating(self):
        records = []
        is_new = {}
        for song_id in range(1, 41):
            records.append(make_record(song_id, 200 + song_id, 100.49))
            is_new[song_id] = False
        for song_id in range(101, 121):
            records.append(make_record(song_id, 300 + song_id, 100.48))
            is_new[song_id] = True

        best50 = select_achievement_b50_records(records, 'near', is_new.get)

        self.assertEqual(len(best50.sd), 35)
        self.assertEqual(len(best50.dx), 15)
        self.assertEqual([record.song_id for record in best50.sd[:3]], [40, 39, 38])
        self.assertEqual([record.song_id for record in best50.dx[:3]], [120, 119, 118])
        self.assertEqual(best50.sd_total, sum(record.rating for record in best50.sd))
        self.assertEqual(best50.dx_total, sum(record.rating for record in best50.dx))

    def test_filters_records_before_splitting_old_and_new(self):
        records = [
            make_record(1, 100, 96.9999),
            make_record(2, 999, 97.0),
            make_record(3, 300, 80.0),
        ]
        best50 = select_achievement_b50_records(
            records,
            'under_s',
            lambda song_id: song_id == 3,
        )

        self.assertEqual([record.song_id for record in best50.sd], [1])
        self.assertEqual([record.song_id for record in best50.dx], [3])


class AchievementBest50CommandTest(unittest.TestCase):
    def test_command_aliases_strip_to_username(self):
        cases = {
            'under_s': ('越级50 Alice', '越级b50 Alice', '/A50 Alice'),
            'near': ('寸50 Alice', '寸b50 Alice', '/C50 Alice'),
            'lock': ('锁血50 Alice', '锁血b50 Alice', '/S50 Alice'),
        }
        for mode, commands in cases.items():
            for command in commands:
                with self.subTest(mode=mode, command=command):
                    self.assertEqual(
                        re.sub(ACHIEVEMENT_COMMAND_PATTERNS[mode], '', command).strip(),
                        'Alice',
                    )

    def test_summary_labels(self):
        labels = {
            'under_s': '(越级)',
            'near': '(寸)',
            'lock': '(锁血)',
        }
        for mode, label in labels.items():
            with self.subTest(mode=mode):
                self.assertEqual(
                    format_best50_summary(
                        100,
                        50,
                        150,
                        achievement_mode=mode,
                    ),
                    f'{label}B35: 100 + B15: 50 = 150',
                )


class AchievementBest50SourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_divingfish_uses_full_records_and_filtered_rating(self):
        player = Player(name='player', rating=15000)
        records = [
            make_record(1, 100, 96.0),
            make_record(2, 200, 97.0),
            make_record(3, 300, 80.0),
        ]
        query_records = AsyncMock(return_value=(player, records))

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._divingfish_dev_records_raw',
                query_records,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._is_new_song',
                side_effect=lambda song_id: song_id == 3,
            ),
        ):
            result_player, best50 = await get_best50(
                qqid=123456,
                achievement_mode='under_s',
            )

        query_records.assert_awaited_once_with(qqid=123456, username=None)
        self.assertEqual([record.song_id for record in best50.sd], [1])
        self.assertEqual([record.song_id for record in best50.dx], [3])
        self.assertEqual(result_player.rating, 400)

    async def test_lxns_requests_exact_achievements(self):
        player = Player(name='player', rating=15000)
        query_b50 = AsyncMock(return_value=(player, Best50()))
        query_records = AsyncMock(
            return_value=[make_record(1, 100, 100.4999)]
        )

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=True,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._lxns_best50_raw',
                query_b50,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._lxns_records_raw',
                query_records,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._is_new_song',
                return_value=False,
            ),
        ):
            await get_best50(qqid=123456, achievement_mode='near')

        query_records.assert_awaited_once_with(123456, exact=True)


class DifficultyBest50Test(unittest.TestCase):
    def test_selects_each_chart_difficulty(self):
        records = [
            make_record(
                song_id=index + 1,
                rating=100 + index,
                achievements=100.0,
                level_index=index,
            )
            for index in range(5)
        ]

        for difficulty_index in range(5):
            with self.subTest(difficulty_index=difficulty_index):
                best50 = select_difficulty_b50_records(
                    records,
                    difficulty_index,
                    lambda _song_id: False,
                )
                self.assertEqual(
                    [record.level_index for record in best50.sd],
                    [difficulty_index],
                )

    def test_applies_35_15_caps_and_sorts_by_rating(self):
        records = []
        is_new = {}
        for song_id in range(1, 41):
            records.append(
                make_record(song_id, 200 + song_id, 100.0, level_index=2)
            )
            is_new[song_id] = False
        for song_id in range(101, 121):
            records.append(
                make_record(song_id, 300 + song_id, 100.0, level_index=2)
            )
            is_new[song_id] = True

        best50 = select_difficulty_b50_records(records, 2, is_new.get)

        self.assertEqual(len(best50.sd), 35)
        self.assertEqual(len(best50.dx), 15)
        self.assertEqual([record.song_id for record in best50.sd[:3]], [40, 39, 38])
        self.assertEqual([record.song_id for record in best50.dx[:3]], [120, 119, 118])

    def test_rejects_invalid_difficulty_index(self):
        with self.assertRaises(ValueError):
            select_difficulty_b50_records([], 5, lambda _song_id: False)

    def test_command_aliases_strip_to_username(self):
        for color in ('绿', '黄', '红', '紫', '白'):
            for command in (f'{color}谱b50 Alice', f'/{color}谱B50 Alice'):
                with self.subTest(command=command):
                    self.assertEqual(
                        re.sub(DIFFICULTY_B50_COMMAND_PATTERN, '', command).strip(),
                        'Alice',
                    )

    def test_summary_labels(self):
        labels = ('绿谱', '黄谱', '红谱', '紫谱', '白谱')
        for difficulty_index, label in enumerate(labels):
            with self.subTest(difficulty_index=difficulty_index):
                self.assertEqual(
                    format_best50_summary(
                        100,
                        50,
                        150,
                        difficulty_index=difficulty_index,
                    ),
                    f'({label})B35: 100 + B15: 50 = 150',
                )


class AllSongsBest50Test(unittest.TestCase):
    def test_selects_top_50_without_old_new_caps(self):
        records = [
            make_record(song_id, 1000 - song_id, 100.0)
            for song_id in range(1, 61)
        ]
        # 这些“新曲”的 Rating 全部低于前 50 个旧曲；全曲模式不应强制留出 15 项。
        records.extend(
            make_record(song_id, 100, 100.0)
            for song_id in range(101, 121)
        )

        best50 = select_all_songs_b50_records(records)
        selected = best50.sd + best50.dx

        self.assertEqual(len(best50.sd), 35)
        self.assertEqual(len(best50.dx), 15)
        self.assertEqual(len(selected), 50)
        self.assertEqual([record.song_id for record in selected[:3]], [1, 2, 3])
        self.assertEqual([record.song_id for record in selected[-3:]], [48, 49, 50])
        self.assertEqual(
            best50.sd_total + best50.dx_total,
            sum(record.rating for record in selected),
        )

    def test_uses_stable_tie_breakers(self):
        records = [
            make_record(1, 300, 100.0, level_index=2),
            make_record(2, 300, 100.0, level_index=3),
            make_record(3, 300, 100.1, level_index=1),
        ]

        selected = select_all_songs_b50_records(records).sd

        self.assertEqual([record.song_id for record in selected], [3, 2, 1])

    def test_command_aliases_strip_to_username(self):
        for command in (
            '全曲b50 Alice',
            '/全曲B50 Alice',
            'allb50 Alice',
            '/AB50 Alice',
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    re.sub(ALL_SONGS_B50_COMMAND_PATTERN, '', command).strip(),
                    'Alice',
                )

    def test_summary_uses_single_b50_total(self):
        self.assertEqual(
            format_best50_summary(100, 50, 150, all_songs=True),
            '(全曲)B50: 150',
        )


class AllSongsBest50SourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_divingfish_uses_full_records_and_selected_total(self):
        player = Player(name='player', rating=15000)
        records = [
            make_record(song_id, song_id, 100.0)
            for song_id in range(1, 61)
        ]
        query_records = AsyncMock(return_value=(player, records))

        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.is_lxns',
                return_value=False,
            ),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source._divingfish_dev_records_raw',
                query_records,
            ),
        ):
            result_player, best50 = await get_best50(
                qqid=123456,
                all_songs=True,
            )

        query_records.assert_awaited_once_with(qqid=123456, username=None)
        self.assertEqual(
            [record.song_id for record in best50.sd + best50.dx],
            list(range(60, 10, -1)),
        )
        self.assertEqual(
            result_player.rating,
            sum(range(11, 61)),
        )


if __name__ == '__main__':
    unittest.main()
