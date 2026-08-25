import unittest
from unittest.mock import AsyncMock, patch

from ..libraries import maimaidx_merge, maimaidx_model, maimaidx_music
from ..libraries.maimaidx_analysis import fitted_level_value
from ..libraries.maimaidx_merge import merge_music_data
from ..libraries.maimaidx_model import (
    DivingFishBasicInfo,
    DivingFishChart,
    DivingFishSong,
    Stats,
)
from ..libraries.maimaidx_music import SongList, mai
from ..libraries.maimaidx_play_result import PlayedResult, lookup_meta
from ..libraries.maimaidx_source import _fitted_level_value


def make_music() -> DivingFishSong:
    return DivingFishSong(
        id='1',
        title='same-level-master-remaster',
        type='DX',
        ds=[1.0, 2.0, 3.0, 13.0, 13.0],
        level=['1', '2', '3', '13', '13'],
        charts=[
            DivingFishChart(notes=[1, 1, 1, 1], charter='') for _ in range(5)
        ],
        basic_info=DivingFishBasicInfo.model_validate(
            {
                'title': 'same-level-master-remaster',
                'artist': '',
                'genre': '',
                'bpm': 120,
                'from': '',
                'is_new': False,
            }
        ),
    )


class MusicMergeStatsTest(unittest.IsolatedAsyncioTestCase):
    def test_legacy_music_model_and_converter_are_removed(self):
        self.assertFalse(hasattr(maimaidx_model, 'Music'))
        self.assertFalse(hasattr(maimaidx_merge, 'song_to_music'))

    async def test_binds_same_level_master_and_remaster_stats_by_index(self):
        master_stats = Stats(diff='13', fit_diff=13.52)
        remaster_stats = Stats(diff='13', fit_diff=13.11)

        with patch.object(maimaidx_merge, 'writefile', new=AsyncMock()):
            songs, _ = await merge_music_data(
                diving_fish_list=[make_music()],
                lxns_list=None,
                stats_map={
                    '1': [None, None, None, master_stats, remaster_stats]
                },
            )

        song = songs[0]
        self.assertEqual(song.difficulties[3].stats.fit_diff, 13.52)
        self.assertEqual(song.difficulties[4].stats.fit_diff, 13.11)

    async def test_domain_index_keeps_master_and_remaster_separate(self):
        master_stats = Stats(diff='13', fit_diff=13.52)
        remaster_stats = Stats(diff='13', fit_diff=13.11)

        with patch.object(maimaidx_merge, 'writefile', new=AsyncMock()):
            songs, _ = await merge_music_data(
                diving_fish_list=[make_music()],
                lxns_list=None,
                stats_map={'1': [None, None, None, master_stats, remaster_stats]},
            )

        record = PlayedResult(
            song_id=1,
            song_name='same-level-master-remaster',
            level='13',
            level_index=4,
            level_value=13.0,
            type='DX',
            achievements=100.0,
        )
        with patch.object(mai, '_song_map', {1: songs[0]}):
            self.assertEqual(mai.get_difficulty(1, 3).stats.fit_diff, 13.52)
            self.assertEqual(mai.get_difficulty(1, 4).stats.fit_diff, 13.11)
            self.assertEqual(fitted_level_value(record), 13.11)
            self.assertEqual(_fitted_level_value(record), 13.11)
            self.assertEqual(
                lookup_meta(1, 4),
                (13.0, '13', 'same-level-master-remaster'),
            )

    async def test_song_list_queries_use_domain_fields(self):
        with patch.object(maimaidx_merge, 'writefile', new=AsyncMock()):
            songs, _ = await merge_music_data(
                diving_fish_list=[make_music()],
                lxns_list=None,
                stats_map={},
            )

        song_list = SongList(songs)
        self.assertIs(song_list.by_id('1'), songs[0])
        self.assertIs(song_list.by_title('same-level-master-remaster'), songs[0])
        filtered = song_list.filter(ds=(12.9, 13.1), charter_search='')
        self.assertEqual(filtered[0].selected_difficulties, [3, 4])
        plans = song_list.by_plan('13')
        self.assertEqual(set(plans['1']), {3, 4})

    async def test_music_loader_returns_song_list_without_conversion(self):
        with (
            patch.object(maimaidx_music, '_load_diving_fish', new=AsyncMock(return_value=([], {}))),
            patch.object(maimaidx_music, '_load_lxns_songs', new=AsyncMock(return_value=None)),
            patch.object(
                maimaidx_music,
                'merge_music_data',
                new=AsyncMock(return_value=([], {'1-3': 13.0})),
            ),
        ):
            songs, level_values = await maimaidx_music.get_song_list()

        self.assertIsInstance(songs, SongList)
        self.assertEqual(level_values, {'1-3': 13.0})


if __name__ == '__main__':
    unittest.main()
