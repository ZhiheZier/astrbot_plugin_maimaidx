import unittest
from unittest.mock import AsyncMock, patch

from ..libraries import maimaidx_merge
from ..libraries.maimaidx_merge import merge_music_data, song_to_music
from ..libraries.maimaidx_model import BasicInfo, Chart, Music, Stats


def make_music() -> Music:
    return Music(
        id='1',
        title='same-level-master-remaster',
        type='DX',
        ds=[1.0, 2.0, 3.0, 13.0, 13.0],
        level=['1', '2', '3', '13', '13'],
        charts=[
            Chart(notes=[1, 1, 1, 1], charter='') for _ in range(5)
        ],
        basic_info=BasicInfo.model_validate(
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

        music = song_to_music(songs[0])
        self.assertEqual(music.stats[3].fit_diff, 13.52)
        self.assertEqual(music.stats[4].fit_diff, 13.11)


if __name__ == '__main__':
    unittest.main()
