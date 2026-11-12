import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from PIL import Image

from ..libraries import maimaidx_pc_image as image
from ..libraries import maimai_best_50 as b50
from ..libraries.maimaidx_play_result import played_to_chartinfo, chartinfo_to_played
from ..libraries.maimaidx_user import Theme
from ..libraries.maimaidx_sync import snapshot_from_official, upload_records
from ..libraries.maimaidx_official import OfficialData
from .test_help_image import command_decorators
from .test_official_sync import data, detail


class PC50ImageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        catalogue = patch.object(image.mai, 'get_song', side_effect=lambda song_id: SimpleNamespace(
            song_name=f'Song {song_id}', type='SD', isnew=False, difficulties=[],
        ))
        catalogue.start()
        self.addCleanup(catalogue.stop)

    def test_pc50_selects_old35_and_new15_independently(self):
        snapshot = snapshot_from_official(data([detail(musicId=index + 1, playCount=index + 1)
                                                for index in range(80)]))
        rows = snapshot.pc50(lambda song_id: song_id > 40)
        self.assertEqual(len(rows), 50)
        self.assertEqual([row.play_count for row in rows[:35]], list(range(40, 5, -1)))
        self.assertEqual([row.play_count for row in rows[35:]], list(range(80, 65, -1)))

    def test_empty_version_group_is_not_filled_from_other_group(self):
        snapshot = snapshot_from_official(data([detail(musicId=index + 1, playCount=1)
                                                for index in range(60)]))
        self.assertEqual(len(snapshot.pc50(lambda _: False)), 35)
        self.assertEqual(len(snapshot.pc50(lambda _: True)), 15)

    def test_skips_utage_unknown_zero_and_missing_pc_before_selection(self):
        snapshot = snapshot_from_official(data([
            detail(musicId=100001, playCount=9999), detail(musicId=1, level=5, playCount=9999),
            detail(musicId=2, playCount=9999), detail(musicId=3, playCount=None),
            detail(musicId=4, playCount=0), detail(musicId=5, playCount=10),
            detail(musicId=6, playCount=10, achievement=1004000),
            detail(musicId=7, playCount=10, deluxscoreMax=2500),
        ]))
        rows = snapshot.pc50(lambda song_id: False if song_id != 2 else None)
        self.assertEqual([row.song_id for row in rows], [7, 5, 6])

    def test_adapter_preserves_pc_order_and_counts_through_b50_models(self):
        snapshot = snapshot_from_official(data([detail(musicId=index + 1, playCount=index + 1)
                                                for index in range(50)]))
        with patch.object(image.mai, 'get_song', side_effect=lambda song_id: SimpleNamespace(
            song_name=f'Song {song_id}', type='DX', isnew=song_id > 35, difficulties=[],
        )):
            player, result = image.pc50_to_best50(snapshot)
        self.assertEqual(player.rating, sum(score.rating for score in result.sd + result.dx))
        self.assertEqual((len(result.sd), len(result.dx)), (35, 15))
        self.assertEqual([score.play_count for score in result.sd], list(range(35, 0, -1)))
        self.assertEqual([score.play_count for score in result.dx], list(range(50, 35, -1)))
        record = played_to_chartinfo(result.sd[0])
        self.assertEqual(record.play_count, 35)
        self.assertEqual(chartinfo_to_played(record).play_count, 35)

    def test_rating_only_sums_pc_selected_regular_old35_and_new15(self):
        snapshot = snapshot_from_official(data(
            [detail(musicId=index, playCount=index, achievement=1000000 + index * 10) for index in range(1, 81)]
            + [detail(musicId=100001, playCount=9999), detail(musicId=81, playCount=None),
               detail(musicId=82, playCount=0), detail(musicId=83, playCount=9999)]
        ))
        def song(song_id):
            return None if song_id == 83 else SimpleNamespace(song_name=f'Song {song_id}', type='DX', isnew=song_id > 40)

        with (patch.object(image.mai, 'get_song', side_effect=song),
              patch.object(image.mai, 'get_difficulty', return_value=SimpleNamespace(level_value=14.6, level='14+'))):
            player, result = image.pc50_to_best50(snapshot)
        expected_old, expected_new = list(range(40, 5, -1)), list(range(80, 65, -1))
        self.assertEqual([score.song_id for score in result.sd], expected_old)
        self.assertEqual([score.song_id for score in result.dx], expected_new)
        expected = sum(image.computeRa(14.6, (1000000 + song_id * 10) / 10000) for song_id in expected_old + expected_new)
        self.assertEqual(player.rating, expected)
        self.assertNotEqual(player.rating, snapshot.rating)
        self.assertEqual(snapshot.rating, 16000)

    def test_empty_pc_selection_has_zero_rating(self):
        snapshot = snapshot_from_official(data([detail(playCount=0)]))
        player, result = image.pc50_to_best50(snapshot)
        self.assertEqual((player.rating, result.sd, result.dx), (0, [], []))

    def test_partial_pc_selection_and_missing_difficulty_use_card_ratings(self):
        snapshot = snapshot_from_official(data([detail(musicId=1), detail(musicId=2)]))
        with patch.object(image.mai, 'get_difficulty', side_effect=lambda song_id, _: (
                SimpleNamespace(level_value=13.5, level='13+') if song_id == 1 else None)):
            player, result = image.pc50_to_best50(snapshot)
        self.assertEqual(len(result.sd), 2)
        self.assertEqual(result.sd[1].rating, 0)
        self.assertEqual(player.rating, result.sd[0].rating)

    async def test_pc50_calls_existing_b50_renderer_with_users_theme(self):
        snapshot = snapshot_from_official(data())
        renderer = SimpleNamespace(draw=AsyncMock(return_value=Image.new('RGBA', (1400, 1600))))
        with patch.object(b50.DrawBest, 'from_best50', return_value=renderer) as factory:
            picture = await image.draw_pc50(snapshot, qqid='123456', theme=Theme.CIRCLE)
        self.assertEqual(picture.type, 'image')
        renderer.draw.assert_awaited_once()
        self.assertTrue(factory.call_args.kwargs['pc50'])
        self.assertEqual(factory.call_args.args[0].rating,
                         sum(score.rating for score in factory.call_args.args[1].sd + factory.call_args.args[1].dx))
        self.assertEqual(factory.call_args.kwargs['theme'], Theme.CIRCLE)
        self.assertEqual(factory.call_args.kwargs['pc_total'], 7)

    async def test_pc50_uses_separate_old_and_new_rendering_sections(self):
        renderer = b50.DrawBest.__new__(b50.DrawBest)
        renderer.player = image.Player(name='Player', rating=16000)
        renderer.Rating = 16000
        renderer.addRating = 0
        renderer.qqid = None
        renderer.theme = Theme.PRISM_PLUS
        renderer.pc50 = True
        renderer.pc_total = 100
        renderer.pc_updated_at = '2026-11-11'
        renderer.service = None
        renderer.sdBest, renderer.dxBest = [Mock(ra=1)], [Mock(ra=2)]
        renderer._im = Image.new('RGBA', (1400, 1600))
        renderer._sy = renderer._tb = Mock()
        renderer.text_color = (0, 0, 0, 255)
        renderer.all_perfect = renderer.all_perfect_plus = renderer.fitted = renderer.all_songs = False
        renderer.min_dx_star = renderer.achievement_mode = renderer.difficulty_index = None
        renderer._collection_asset = AsyncMock(return_value=None)
        renderer.whiledraw = AsyncMock()
        with patch.object(b50.Image, 'open', side_effect=lambda *_: Image.new('RGBA', (10, 10))):
            await renderer.draw()
        self.assertEqual(renderer.whiledraw.await_args_list,
                         [call(renderer.sdBest, True), call(renderer.dxBest, False)])

    async def test_shared_b50_card_changes_only_pc_line_and_preserves_icons(self):
        snapshot = snapshot_from_official(data())
        _, result = image.pc50_to_best50(snapshot)
        record = played_to_chartinfo(result.sd[0])
        for pc_mode in (False, True):
            renderer = b50.DrawBest.__new__(b50.DrawBest)
            renderer.pc50 = pc_mode
            renderer.theme = Theme.PRISM_PLUS
            renderer._im = Image.new('RGBA', (1400, 1600))
            renderer._diff = [Image.new('RGBA', (270, 112)) for _ in range(5)]
            renderer._sy = Mock()
            renderer._tb = Mock()
            with (patch.object(b50, 'music_picture', AsyncMock(return_value='unused')),
                  patch.object(b50.Image, 'open', side_effect=lambda *_: Image.new('RGBA', (75, 75), 'red')) as assets):
                await renderer.whiledraw([record], True)
            labels = [call.args[3] for call in renderer._tb.draw.call_args_list]
            self.assertIn('PC: 7' if pc_mode else f'{record.ds} -> {record.ra}', labels)
            self.assertIn('100.5000%', labels)
            self.assertTrue(any('APp' in str(call.args[0]) for call in assets.call_args_list))
            self.assertTrue(any('FSDp' in str(call.args[0]) for call in assets.call_args_list))
            self.assertEqual(renderer._im.getpixel((30, 250)), (255, 0, 0, 255))

    def test_upload_conversion_uses_catalogue_and_never_sends_pc_or_credentials(self):
        snapshot = snapshot_from_official(OfficialData(123, 'Player', 16000, [
            detail(musicId=383), detail(), detail(musicId=999999), detail(level=10),
        ]))
        from ..libraries.maimaidx_music import mai

        def song(song_id):
            from types import SimpleNamespace
            return None if song_id == 999999 else SimpleNamespace(song_name='Link', type='DX' if song_id > 10000 else 'SD')

        with (patch.object(mai, 'get_song', side_effect=song),
              patch.object(mai, 'get_difficulty', return_value=object())):
            fish, lxns = upload_records(snapshot)
        self.assertEqual(len(fish), 2)
        self.assertEqual(fish[0]['title'], 'Link(CoF)')
        self.assertEqual((lxns[1]['id'], lxns[1]['type']), (1103, 'dx'))
        for record in fish + lxns:
            self.assertFalse({'playCount', 'play_count', 'token', 'userId'} & record.keys())

    def test_command_filters_are_anchored(self):
        commands = command_decorators()
        for text in ('pc50', '/PC50'):
            self.assertIsNotNone(re.fullmatch(commands['pc50'][1], text))
        for text in ('pc50 123456', 'xpc50', 'pc500', '我的pc数', '我的PC数'):
            self.assertIsNone(re.fullmatch(commands['pc50'][1], text))
        for text in ('同步成绩', '更新成绩', '/同步成绩 SGWCMAID...', '重传成绩', '取消成绩更新', '删除PC数据',
                     '导', '/导', '更新b50', '更新B50', '更新乐曲数据', '导 SGWCMAID...'):
            self.assertIsNotNone(re.fullmatch(commands['sync_official_scores'][1], text))
        for text in ('更新成绩别人的账号', '同步成绩123', 'x同步成绩', '导演', '更新b500', '更新乐曲数据abc'):
            self.assertIsNone(re.fullmatch(commands['sync_official_scores'][1], text))
