import unittest
from unittest.mock import patch

from ..command.mai_table import (
    parse_plate_progress_request,
    parse_plate_table_args,
    parse_table_request,
)
from ..libraries.maimaidx_model import Difficulties, Notes, PlayInfoDefault, Song
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_plate_table
from ..libraries.maimaidx_plate_progress import (
    build_plate_progress,
    is_plate_qualified,
)


def make_song(song_id: int, difficulty_count: int = 4) -> Song:
    return Song(
        song_id=song_id,
        song_name=f'song-{song_id}',
        type='DX',
        difficulties=[
            Difficulties(
                level_index=index,
                level=str(index + 1),
                level_value=float(index + 1),
                notes=Notes(),
            )
            for index in range(difficulty_count)
        ],
    )


def make_play(song: Song, level_index: int, achievements: float = 100) -> PlayInfoDefault:
    difficulty = song.difficulties[level_index]
    return PlayInfoDefault(
        id=song.song_id,
        achievements=achievements,
        level=difficulty.level,
        level_index=level_index,
        title=song.song_name,
        type=song.type,
        ds=difficulty.level_value,
    )


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

    def test_plate_progress_table_suffix_is_not_a_username(self):
        self.assertEqual(
            parse_plate_progress_request('晓将进度表'),
            ('晓', '将', ''),
        )
        self.assertEqual(
            parse_plate_progress_request('晓将进度'),
            ('晓', '将', ''),
        )

    def test_plate_progress_username_remains_supported(self):
        self.assertEqual(
            parse_plate_progress_request('晓将进度 Alice'),
            ('晓', '将', 'Alice'),
        )
        self.assertEqual(
            parse_plate_progress_request('晓将进度表 Alice'),
            ('晓', '将', 'Alice'),
        )
        self.assertEqual(
            parse_plate_progress_request('晓将进度 表哥'),
            ('晓', '将', '表哥'),
        )


class PlateTableDrawingTest(unittest.IsolatedAsyncioTestCase):
    async def test_wu_version_resolves_without_single_version_lookup(self):
        with patch.object(mai, 'total_plate_id_list', {}, create=True):
            result = await draw_plate_table(1226898784, '舞', '将', 1)

        self.assertEqual(
            result,
            '「舞」牌子数据尚未更新，暂时无法查询该牌子完成表',
        )


class PlateProgressDataTest(unittest.TestCase):
    def test_remaster_uses_its_own_denominator(self):
        standard = make_song(1)
        remaster = make_song(2, 5)
        plays = [
            make_play(song, index, 100 if index < 4 else 99)
            for song in (standard, remaster)
            for index in range(len(song.difficulties))
        ]

        progress = build_plate_progress(
            [standard, remaster],
            plays,
            '将',
            remaster_ids=[2],
        )

        self.assertEqual(progress.total_count, 2)
        self.assertEqual(progress.remaster_count, 1)
        self.assertEqual(progress.slot_counts, [2, 2, 2, 2, 0])
        self.assertEqual(progress.completed_count, 1)
        self.assertEqual(len(progress.chart_results[4]), 1)

    def test_plate_criteria_match_completion_table(self):
        song = make_song(1)
        play = make_play(song, 0)

        play.achievements = 80
        self.assertTrue(is_plate_qualified(play, '者'))
        self.assertFalse(is_plate_qualified(play, '将'))
        play.fc = 'app'
        self.assertTrue(is_plate_qualified(play, '极'))
        self.assertTrue(is_plate_qualified(play, '神'))
        play.fs = 'fdxp'
        self.assertTrue(is_plate_qualified(play, '舞舞'))


if __name__ == '__main__':
    unittest.main()
