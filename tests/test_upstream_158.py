import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from .. import CB_SYNC_MAP, DX_VERSION, PLATE_CN, normalize_plate_filename, resolve_plate_asset
from ..command import mai_table
from ..command.mai_search import parse_ds_search_args
from ..libraries import maimaidx_merge as merge, maimaidx_music_info as info
from ..libraries import maimaidx_player_score as player_score
from ..libraries.maimaidx_api_data import MaimaiAPI
from ..libraries.maimaidx_error import DivingFishOAuthConfigError, MusicNotPlayError
from ..libraries.maimaidx_model import ChartInfo, ChartRef, Difficulties, Notes, PlayInfoDefault, RecordsQueryRequest, Song
from ..libraries.maimaidx_music import SongList, mai
from .test_player_arguments import event


def song(song_id=1, ds=14, indexes=(3,), *, newest=False):
    return Song(song_id=song_id, song_name=f'Song {song_id}', type='DX',
                version_str=list(DX_VERSION.values())[-1] if newest else DX_VERSION['镜'],
                difficulties=[Difficulties(level_index=index, level='14', level_value=ds, notes=Notes(tap=1))
                              for index in range(max(indexes) + 1)])


def chart(song_id, ra=200, *, achievement=99, index=3):
    return ChartInfo(song_id=song_id, ra=ra, achievements=achievement, level='14', level_index=index,
                     ds=14, title='Song', type='DX', level_label='Master')


class UpstreamRecordsTest(unittest.IsolatedAsyncioTestCase):
    async def test_query_serializes_filters_and_uses_only_records_endpoint(self):
        api = MaimaiAPI()
        request = AsyncMock(return_value={'nickname': 'Player', 'username': 'Player', 'rating': 1,
                                         'additional_rating': 0, 'records': []})
        with patch.object(api, '_requestmai_oauth', request):
            await api.query_user_records(qqid=123, query=RecordsQueryRequest(
                song_id=11103, level_value=14.5, fullcombo=['ap', 'app'], fullsync=['fsdp'], level='14+',
            ))
        request.assert_awaited_once_with('GET', '/player/records', qqid=123, username=None,
                                        params={'song_id': 11103, 'ds': 14.5, 'fc': ['ap', 'app'],
                                                'fs': ['fsdp'], 'level': '14+'})

    async def test_single_song_query_filters_records_and_converts_legacy_shape(self):
        api = MaimaiAPI()
        response = SimpleNamespace(records=[chart(1), chart(2)])
        with patch.object(api, 'query_user_records', AsyncMock(return_value=response)) as request:
            rows = await api.query_user_post_dev(qqid=123, music_id=['1', 1])
            self.assertEqual([row.song_id for row in rows], [1])
            self.assertEqual(request.await_count, 1)
            self.assertEqual(request.await_args.kwargs['query'].song_id, 1)
            rows = await api.query_user_plate(qqid=123, version=['maimai'])
            self.assertEqual([row.song_id for row in rows], [1, 2])
        with patch.object(api, 'query_user_records', AsyncMock(return_value=SimpleNamespace(records=[]))):
            with self.assertRaises(MusicNotPlayError):
                await api.query_user_post_dev(qqid=123, music_id=1)

    async def test_no_oauth_never_falls_back_to_legacy_network_request(self):
        api = MaimaiAPI()
        api.token = 'unused-legacy-token'
        api.load_token_proxy()
        self.assertIsNone(api.headers)
        self.assertIsNone(api.token)
        with patch.object(api, '_requestmai', AsyncMock()) as request:
            for method, args in ((api.query_user_get_dev, {}), (api.query_user_plate, {}),
                                 (api.query_user_post_dev, {'music_id': 1})):
                with self.assertRaises(DivingFishOAuthConfigError):
                    await method(qqid=123, **args)
        request.assert_not_awaited()


class UpstreamAliasTest(unittest.IsolatedAsyncioTestCase):
    async def test_aliases_only_attach_to_existing_standard_and_dx_yuzu_ids(self):
        yuzu = [{'SongID': sid, 'Name': str(sid), 'Alias': [str(sid)]} for sid in (1, 1001, 11001, 12000)]
        lxns = merge.LXAliases(aliases=[merge.LXAliasItem(song_id=sid, aliases=[name])
                                      for sid, name in ((1001, 'shared'), (2000, 'dx-only'), (9999, 'invalid'))])
        with patch.object(merge, 'writefile', AsyncMock()):
            result = await merge.merge_alias_data(yuzu, lxns, {'77': ['local-only']})
        by_id = {row['SongID']: row['Alias'] for row in result}
        self.assertEqual(set(by_id), {1, 1001, 11001, 12000, 77})
        self.assertIn('shared', by_id[1001])
        self.assertIn('shared', by_id[11001])
        self.assertIn('dx-only', by_id[12000])


class UpstreamRiseTest(unittest.TestCase):
    def recommend(self, songs, best, old=None, target=1, kind='SD'):
        with patch.object(mai, 'total_list', SongList(songs)):
            return player_score.get_rise_score_list(old or defaultdict(dict), kind, best, '14', target)

    def test_empty_best_slots_allow_unplayed_charts_and_use_lowercase_d(self):
        rows, threshold = self.recommend([song()], [])
        self.assertTrue(rows)
        self.assertEqual(threshold, 0)
        self.assertTrue(all(row.oldrate == 'd' for row in rows))

    def test_unsorted_full_best_uses_actual_minimum_and_checks_requested_gain(self):
        best = [chart(999, 190)] + [chart(1000 + n, 250) for n in range(34)]
        rows, threshold = self.recommend([song(ds=10)], best, target=30)
        self.assertEqual(threshold, 190)
        self.assertTrue(rows)
        self.assertTrue(all(row.ra - threshold >= 30 for row in rows))

    def test_existing_best_chart_gain_is_relative_to_its_own_rating(self):
        old_rating = player_score.computeRa(14, 99)
        best = [chart(1, old_rating)] + [chart(1000 + n, 200) for n in range(34)]
        rows, _ = self.recommend([song()], best, target=20)
        master = next(row for row in rows if row.level_index == 3)
        self.assertGreaterEqual(master.ra - old_rating, 20)

    def test_sss_plus_difficulty_does_not_exclude_other_difficulties_of_song(self):
        old = defaultdict(dict, {1: {3: 100.5}})
        rows, _ = self.recommend([song()], [chart(1, achievement=100.5)], old=old)
        self.assertTrue(rows)
        self.assertNotIn(3, [row.level_index for row in rows])

    def test_only_current_version_goes_into_new_pool_and_previous_version_is_not_dropped(self):
        songs = [song(1), song(2, newest=True)]
        old, _ = self.recommend(songs, [], kind='SD')
        new, _ = self.recommend(songs, [], kind='DX')
        self.assertEqual({row.song_id for row in old}, {1})
        self.assertEqual({row.song_id for row in new}, {2})


class UpstreamCompletionTest(unittest.IsolatedAsyncioTestCase):
    async def test_fs_completion_renders_with_legacy_assets_and_missing_allclear_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGBA', (1400, 700), 'white').save(root / '14.png')
            for name in ('unfinished_1.png', 'complete_1.png', 'UI_MSS_MBase_Icon_FSp.png', 'UI_CHR_PlayBonus_FSp.png'):
                Image.new('RGBA', (80, 80), (40, 180, 240, 255)).save(root / name)
            record = PlayInfoDefault(id=1, achievements=100, fc='fc', fs='fsp', level='14',
                                     level_index=3, title='Song', type='DX', ds=14)
            ref = ChartRef(id='1', ds=14, lv='3', lvp='14', type='DX')
            captured = []

            def encode(image):
                captured.append(image.copy())
                return 'test-image'

            with (patch.object(info, 'maimaidir', root), patch.object(info, 'ratingdir', root),
                  patch.object(mai, 'total_level_data', {'14': {'14.0': [ref]}}, create=True),
                  patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_source.get_plate', AsyncMock(return_value=[record])),
                  patch.object(info, 'DrawText'), patch.object(info, 'image_to_base64', side_effect=encode)):
                result = await info.draw_rating_table(123, '14', plan=CB_SYNC_MAP['fs+'])
            self.assertNotIsInstance(result, str)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].size, (1120, 560))
            self.assertEqual(captured[0].getpixel((60, 60))[:3], (40, 180, 240))

    async def test_target_aliases_are_passed_to_renderer(self):
        for target, key in (('fc', 'fc'), ('fc+', 'fc+'), ('fcp', 'fc+'), ('ap', 'ap'), ('app', 'ap+'),
                            ('fs', 'fs'), ('fs+', 'fs+'), ('fdx', 'fdx'), ('fdx+', 'fdx+'), ('fsdp', 'fdx+')):
            with patch.object(mai_table, 'draw_rating_table', AsyncMock(return_value='ok')) as draw:
                messages = [row async for row in mai_table.table_pfm_handler(event(f'14{target}完成表'))]
            self.assertTrue(messages)
            self.assertEqual(draw.await_args.kwargs['plan'], CB_SYNC_MAP[key])

    def test_plan_uses_correct_status_and_threshold(self):
        row = {'fc': 'fc', 'fs': 'fsp'}
        self.assertIsNone(info.completion_plan_status(row, CB_SYNC_MAP['ap']))
        self.assertEqual(info.completion_plan_status(row, CB_SYNC_MAP['fs+']), 'fsp')
        self.assertIsNone(info.completion_plan_status(row, CB_SYNC_MAP['fdx']))
        self.assertEqual(info.calc_achievements_fc([0, 0], 2, True, CB_SYNC_MAP['ap+']), 0)
        self.assertEqual(info.calc_achievements_fc([0, 1], 2, True, CB_SYNC_MAP['fs+']), 0)

    def test_mirror_assets_prefer_traditional_and_fall_back_to_legacy(self):
        self.assertEqual(PLATE_CN['镜'], '鏡')
        self.assertEqual(normalize_plate_filename('镜极'), '鏡極')
        for request in ('镜極', '鏡極'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                traditional, legacy = root / '鏡極.png', root / '镜極.png'
                legacy.touch()
                with patch('data.plugins.astrbot_plugin_maimaidx.plate_version_dir', root):
                    self.assertEqual(resolve_plate_asset(request), legacy)
                    traditional.touch()
                    self.assertEqual(resolve_plate_asset(request), traditional)
        self.assertEqual(mai_table.parse_plate_table_args('鏡极'), ('鏡', '极', 1))

    def test_constant_range_and_page_disambiguation(self):
        for args, expected in ((['13'], (13, 13, 1)), (['13', '14'], (13, 14, 1)),
                               (['14.5', '2'], (14.5, 14.5, 2)), (['14.5', '2.0'], (14.5, 14.5, 2)),
                               (['13+', '2'], (13.6, 13.9, 2)), (['13', '14', '0'], (13, 14, 1))):
            self.assertEqual(parse_ds_search_args(args), expected)
        for args in (['nan'], ['14', 'bad'], ['13', '14', 'bad']):
            with self.assertRaises(ValueError):
                parse_ds_search_args(args)
