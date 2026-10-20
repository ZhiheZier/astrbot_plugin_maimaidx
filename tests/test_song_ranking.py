import asyncio
import base64
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp
from PIL import Image, ImageFont

from .. import MessageSegment
from ..command import mai_ranking as command
from ..libraries import maimaidx_ranking as ranking
from ..libraries import maimaidx_ranking_image as drawing
from ..libraries.maimaidx_error import MusicNotPlayError
from ..libraries.maimaidx_music import SongList, mai
from ..libraries.maimaidx_play_result import PlayedResult, dx_star_from_scores, played_to_playinfodev
from ..main import MaimaiDXPlugin
from .test_help_image import command_decorators
from .test_tolerance import make_song


def event(text, *, group='200', qq='123456', bot='654321'):
    return SimpleNamespace(
        message_str=text, message_obj=SimpleNamespace(group_id=group, message=[Comp.Plain(text)]),
        get_platform_name=lambda: 'aiocqhttp', get_self_id=lambda: bot,
        get_sender_id=lambda: qq, get_sender_name=lambda: f'玩家{qq}',
        plain_result=lambda text: text, chain_result=lambda chain: chain,
    )


def score(achievement=100.5, dx=2041, **kwargs):
    return PlayedResult(song_id=799, level_index=3, type='DX', achievements=achievement,
                        dx_score=dx, rate='sssp', fc='app', fs='fsdp', **kwargs)


class RankingTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'song_ranking.json'
        self.store = ranking.SongRankingStore(self.path)
        self.scope = '654321:200'


class RankingStoreTest(RankingTestBase):
    async def test_opt_in_is_group_and_bot_scoped_and_survives_restart(self):
        self.assertEqual(await self.store.members(self.scope), {})
        self.assertTrue(await self.store.set_member(self.scope, '123', '玩家', joined=True))
        self.assertFalse(await self.store.set_member(self.scope, '123', '玩家', joined=True))
        self.assertEqual(await self.store.members('654321:300'), {})
        self.assertEqual(await self.store.members('111111:200'), {})
        restored = ranking.SongRankingStore(self.path)
        self.assertEqual(await restored.members(self.scope), {'123': '玩家'})
        snapshot = await restored.members(self.scope)
        snapshot.clear()
        self.assertEqual(await restored.members(self.scope), {'123': '玩家'})
        self.assertTrue(await restored.set_member(self.scope, '123', '', joined=False))
        self.assertFalse(await restored.set_member(self.scope, '123', '', joined=False))
        self.assertEqual(await ranking.SongRankingStore(self.path).members(self.scope), {})
        self.assertNotIn('access_token', self.path.read_text(encoding='utf-8'))

    async def test_failed_save_does_not_change_memory_or_disk(self):
        await self.store.set_member(self.scope, '123', '玩家', joined=True)
        before = self.path.read_bytes()
        with patch.object(self.store, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                await self.store.set_member(self.scope, '123', '', joined=False)
        self.assertEqual(await self.store.members(self.scope), {'123': '玩家'})
        self.assertEqual(self.path.read_bytes(), before)

    async def test_corrupt_file_is_not_overwritten(self):
        for text in ('bad json', '[]', '{}', '{"groups":{"g":[]}}', '{"groups":{"g":{"q":null}}}'):
            self.path.write_text(text, encoding='utf-8')
            store = ranking.SongRankingStore(self.path)
            with self.assertRaises(ValueError):
                await store.set_member(self.scope, '123', '玩家', joined=True)
            self.assertEqual(self.path.read_text(encoding='utf-8'), text)

    async def test_concurrent_joins_do_not_lose_members(self):
        await asyncio.gather(*(self.store.set_member(self.scope, str(i), str(i), joined=True) for i in range(8)))
        self.assertEqual(len(await self.store.members(self.scope)), 8)


class RankingCollectionTest(RankingTestBase):
    async def enroll(self, *ids):
        for qq in ids:
            await self.store.set_member(self.scope, qq, f'玩家{qq}', joined=True)

    async def test_only_opted_in_players_are_queried_and_chart_is_exact(self):
        await self.enroll('1', '2')
        song = make_song()
        data = [score(99), score(100), score(101).model_copy(update={'song_id': 800}),
                score(101).model_copy(update={'level_index': 4}), score(101).model_copy(update={'type': 'SD'})]
        with patch.object(ranking, 'get_music_record', AsyncMock(return_value=data)) as fetch:
            result = await ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3])
        self.assertEqual({call.args[0] for call in fetch.await_args_list}, {'1', '2'})
        self.assertEqual(len(result.rows), 2)
        self.assertTrue(all(row.score.achievements == 100 for row in result.rows))
        self.assertEqual((result.failed, result.unplayed), (0, 0))

    async def test_unplayed_and_failed_queries_never_become_zero_score_rows(self):
        await self.enroll('1', '2', '3')
        async def fetch(qq, song_id):
            if qq == '1':
                raise MusicNotPlayError()
            if qq == '2':
                raise RuntimeError('private-token')
            return []
        song = make_song()
        with patch.object(ranking, 'get_music_record', side_effect=fetch):
            result = await ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3])
        self.assertEqual(result.rows, ())
        self.assertEqual((result.failed, result.unplayed), (1, 2))

    async def test_opt_out_during_fetch_is_removed_before_rendering(self):
        await self.enroll('1')
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            started.set()
            await release.wait()
            return [score()]
        song = make_song()
        with patch.object(ranking, 'get_music_record', side_effect=fetch):
            task = asyncio.create_task(ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3]))
            await started.wait()
            await self.store.set_member(self.scope, '1', '', joined=False)
            release.set()
            self.assertEqual((await task).rows, ())

    async def test_concurrency_is_bounded(self):
        await self.enroll(*(str(i) for i in range(8)))
        active = maximum = 0
        async def fetch(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(.001)
            active -= 1
            return [score()]
        song = make_song()
        with patch.object(ranking, 'get_music_record', side_effect=fetch):
            result = await ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3])
        self.assertEqual(len(result.rows), 8)
        self.assertEqual(maximum, 3)

    async def test_source_compatibility_records_are_converted_without_losing_badges(self):
        await self.enroll('1')
        song = make_song()
        record = played_to_playinfodev(score())
        with patch.object(ranking, 'get_music_record', AsyncMock(return_value=[record])):
            result = await ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3])
        self.assertEqual(len(result.rows), 1)
        self.assertEqual((result.rows[0].score.fc, result.rows[0].score.fs, result.rows[0].score.dx_score),
                         ('app', 'fsdp', 2041))

    async def test_request_timeout_and_cancellation_do_not_create_fake_rows(self):
        await self.enroll('1')
        song = make_song()
        with patch.object(ranking, 'get_music_record', AsyncMock(side_effect=asyncio.TimeoutError)):
            result = await ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3])
        self.assertEqual((result.rows, result.failed), ((), 1))
        with patch.object(ranking, 'get_music_record', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await ranking.collect_ranking(self.store, self.scope, song, song.difficulties[3])

    def test_achievement_then_dx_score_sorting_and_ties(self):
        rows = ranking.rank_rows([
            ranking.RankingRow('1', 'A', score(100, 2000)),
            ranking.RankingRow('2', 'B', score(100.5, 1000)),
            ranking.RankingRow('3', 'C', score(100, 2100)),
            ranking.RankingRow('4', 'D', score(100.5, 1000)),
        ])
        self.assertEqual([row.qqid for row in rows], ['2', '4', '3', '1'])
        self.assertEqual([row.rank for row in rows], [1, 1, 3, 4])


class RankingCommandTest(RankingTestBase):
    def setUp(self):
        super().setUp()
        self.song = make_song(title='Test Song', white=True)
        self.songs = patch.object(mai, 'total_list', SongList([self.song]))
        self.songs.start()
        self.addCleanup(self.songs.stop)
        self.aliases = patch.object(mai, 'total_alias_list', SimpleNamespace(
            by_alias=lambda query: [SimpleNamespace(SongID=799)] if query == '别名' else [],
        ), create=True)
        self.aliases.start()
        self.addCleanup(self.aliases.stop)

    def test_default_highest_difficulty_and_color_english_labels(self):
        for query in ('799', 'Test Song', '别名'):
            self.assertEqual(command.resolve_ranking_chart(query), (self.song, self.song.difficulties[4]))
        for label in ('紫', '紫谱', 'Master', 'MASTER'):
            self.assertEqual(command.resolve_ranking_chart(f'Test Song {label}'),
                             (self.song, self.song.difficulties[3]))
        self.song.difficulties.pop()
        self.assertEqual(command.resolve_ranking_chart('799')[1].level_index, 3)
        self.assertEqual(command.resolve_ranking_chart('799 白'), '该乐曲没有这个难度')

    def test_exact_title_wins_before_optional_difficulty(self):
        self.song.song_name = 'My Master'
        self.assertEqual(command.resolve_ranking_chart('My Master')[1].level_index, 4)

    def test_ambiguous_title_or_alias_never_silently_selects_one_song(self):
        other = make_song(song_id=800, title='Test Song')
        with patch.object(mai, 'total_list', SongList([self.song, other])):
            result = command.resolve_ranking_chart('Test Song')
        self.assertIn('多个歌曲', result)
        self.assertIn('799', result)
        self.assertIn('800', result)

    def test_partial_name_lists_candidates_in_requested_format(self):
        songs = [make_song(song_id=11860, title='MYTH Re：LEASE'), make_song(song_id=233, title='MYTHOS')]
        with patch.object(mai, 'total_list', SongList(songs)):
            result = command.resolve_ranking_chart('myth')
            self.assertEqual(result, '❌ 匹配到多个歌曲，请使用更完整的歌曲名、别名或 ID：\n'
                             '- MYTH Re：LEASE（ID: 11860）\n- MYTHOS（ID: 233）')
            self.assertEqual(command.resolve_ranking_chart('Re：LEASE 紫'), (songs[0], songs[0].difficulties[3]))

    async def test_failed_membership_save_returns_failure_instead_of_success(self):
        with patch.object(self.store, '_write', side_effect=OSError('disk full')):
            replies = [reply async for reply in command.ranking_membership_handler(event('加入排行榜'), self.store)]
        self.assertIn('保存失败', replies[0])
        self.assertEqual(await self.store.members(self.scope), {})

    async def test_membership_only_changes_current_group_and_current_sender(self):
        for text in ('加入排行榜', '/退出排行榜'):
            replies = [reply async for reply in command.ranking_membership_handler(event(text), self.store)]
            self.assertIn('已加入' if '加入' in text else '已退出', replies[0])
            self.assertEqual(await self.store.members('654321:300'), {})
        replies = [reply async for reply in command.ranking_membership_handler(event('加入排行榜', group=None), self.store)]
        self.assertIn('仅在 QQ 群聊', replies[0])

    async def test_empty_group_and_busy_group_do_not_query_scores(self):
        with patch.object(command, 'collect_ranking', AsyncMock()) as collect:
            replies = [reply async for reply in command.song_ranking_handler(event('wmrk 799'), self.store)]
            self.assertIn('尚无人加入', replies[0])
            async with self.store.query_lock(self.scope):
                replies = [reply async for reply in command.song_ranking_handler(event('wmrk 799'), self.store)]
                self.assertIn('正在查询', replies[0])
            collect.assert_not_awaited()

    async def test_handler_passes_song_and_highest_chart_and_returns_image(self):
        await self.store.set_member(self.scope, '1', '玩家', joined=True)
        result = ranking.RankingResult(ranking.rank_rows([ranking.RankingRow('1', '玩家', score())]))
        with (patch.object(command, 'collect_ranking', AsyncMock(return_value=result)) as fetch,
              patch.object(command, 'draw_song_ranking', AsyncMock(return_value=[MessageSegment.image('base64://aW1hZ2U=')]))):
            replies = [reply async for reply in command.song_ranking_handler(event('/WMRK 799 紫'), self.store)]
        fetch.assert_awaited_once_with(self.store, self.scope, self.song, self.song.difficulties[3])
        self.assertIsInstance(replies[0][0], Comp.Image)

    async def test_handler_accepts_no_space_before_id_title_or_alias(self):
        await self.store.set_member(self.scope, '1', '玩家', joined=True)
        result = ranking.RankingResult(ranking.rank_rows([ranking.RankingRow('1', '玩家', score())]))
        for text in ('wmrk799 紫', '/WMRKTest Song 紫', 'wmrk别名 紫', 'wmrk799'):
            with self.subTest(command=text):
                with (patch.object(command, 'collect_ranking', AsyncMock(return_value=result)) as fetch,
                      patch.object(command, 'draw_song_ranking', AsyncMock(return_value=[MessageSegment.image('base64://aW1hZ2U=')]))):
                    replies = [reply async for reply in command.song_ranking_handler(event(text), self.store)]
                selected = self.song.difficulties[4 if text == 'wmrk799' else 3]
                fetch.assert_awaited_once_with(self.store, self.scope, self.song, selected)
                self.assertIsInstance(replies[0][0], Comp.Image)

    async def test_no_valid_scores_returns_text_without_rendering_empty_image(self):
        await self.store.set_member(self.scope, '1', '玩家', joined=True)
        for failed in (0, 2):
            with self.subTest(failed=failed):
                with (patch.object(command, 'collect_ranking', AsyncMock(return_value=ranking.RankingResult((), failed, 1))),
                      patch.object(command, 'draw_song_ranking', AsyncMock()) as draw):
                    replies = [reply async for reply in command.song_ranking_handler(event('wmrk799'), self.store)]
                draw.assert_not_awaited()
                if failed:
                    self.assertIn('查询失败', replies[0])
                    self.assertNotIn('无人游玩过', replies[0])
                else:
                    self.assertEqual(replies, ['本群暂无人游玩过「Test Song」！'])

    async def test_feature_gate_blocks_join_and_query_but_still_allows_opt_out(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.config = {}
        plugin.disabled_groups, plugin.enabled_groups = {'200'}, set()
        plugin.song_ranking_store = self.store
        await self.store.set_member(self.scope, '123456', '玩家', joined=True)
        self.assertEqual([reply async for reply in plugin.ranking_membership(event('加入排行榜'))], [])
        self.assertEqual([reply async for reply in plugin.song_ranking(event('wmrk 799'))], [])
        replies = [reply async for reply in plugin.ranking_membership(event('退出排行榜'))]
        self.assertIn('已退出', replies[0])
        self.assertEqual(await self.store.members(self.scope), {})

    def test_filters_accept_slash_and_case_but_not_prefix_collisions(self):
        patterns = command_decorators()
        for text in ('wmrk 799', 'wmrk799', 'wmrk别名', '/WMRKTest Song 紫', 'wmrk'):
            self.assertIsNotNone(re.fullmatch(patterns['song_ranking'][1], text))
        for text in ('xwmrk 799', '/xwmrk799'):
            self.assertIsNone(re.fullmatch(patterns['song_ranking'][1], text))


class RankingImageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cover = self.root / 'cover.png'
        Image.new('RGB', (196, 196), '#38b9ad').save(self.cover)
        self.font = patch.object(drawing, '_font', side_effect=lambda size: ImageFont.load_default(size=size))
        self.font.start()
        self.addCleanup(self.font.stop)

    def test_star_thresholds_round_up_and_match_b50_star_boundaries(self):
        difficulty = make_song().difficulties[3]
        difficulty.dx_score = 2043
        maximum, thresholds = drawing.dx_star_thresholds(difficulty)
        self.assertEqual((maximum, thresholds), (2043, (1737, 1839, 1900, 1941, 1982)))
        for star, minimum in enumerate(thresholds, 1):
            self.assertEqual(dx_star_from_scores(minimum, maximum), star)
            self.assertEqual(dx_star_from_scores(minimum - 1, maximum), star - 1)
        difficulty.dx_score = 0
        self.assertEqual(drawing.dx_star_thresholds(difficulty)[0], 3462)
        difficulty.notes = difficulty.notes.model_copy(update={'tap': 0, 'hold': 0, 'slide': 0, 'brk': 0})
        self.assertIsNone(drawing.dx_star_thresholds(difficulty))

    async def test_png_has_cover_record_fields_and_b50_icons(self):
        for name, color in (('UI_MSS_MBase_Icon_APp.png', 'red'),
                            ('UI_MSS_MBase_Icon_FSDp.png', 'blue'), ('UI_TTR_Rank_SSSp.png', 'gold')):
            Image.new('RGBA', (40, 40), color).save(self.root / name)
        song = make_song()
        result = ranking.RankingResult(ranking.rank_rows([ranking.RankingRow('1', '长昵称' * 30, score())]))
        with (patch.object(drawing, 'music_picture', AsyncMock(return_value=self.cover)),
              patch.object(drawing, 'themed_path', side_effect=lambda theme, name: self.root / name),
              patch.object(drawing, '_text', wraps=drawing._text) as text):
            pictures = await drawing.draw_song_ranking(song, song.difficulties[3], result)
        image = Image.open(BytesIO(base64.b64decode(pictures[0].data['file'].removeprefix('base64://'))))
        self.assertEqual(image.size, (1024, 464))
        self.assertEqual(image.getpixel((30, 30)), (56, 185, 173))
        self.assertEqual(image.getpixel((790, 325)), (255, 0, 0))
        self.assertEqual(image.getpixel((830, 325)), (0, 0, 255))
        self.assertEqual(image.getpixel((930, 325)), (255, 215, 0))
        captions = [call.args[1] for call in text.call_args_list]
        self.assertIn('100.5000%', captions)
        self.assertIn('2041', captions)

    def test_header_is_left_aligned_and_uses_all_five_star_assets(self):
        song = make_song()
        song.difficulties[3].dx_score = 2043
        with (patch.object(drawing, '_text', wraps=drawing._text) as text,
              patch.object(drawing, '_icon', return_value=True) as icon):
            drawing.render_ranking_page(song, song.difficulties[3], ranking.RankingResult(()), self.cover)
        title = next(call for call in text.call_args_list if call.args[1] == song.song_name)
        self.assertEqual(title.kwargs['align'], 'left')
        metadata = next(call for call in text.call_args_list if str(call.args[1]).startswith('ID:'))
        self.assertEqual(metadata.kwargs['align'], 'left')
        filenames = [call.args[1] for call in icon.call_args_list]
        for star in range(1, 6):
            self.assertIn(f'UI_GAM_Gauge_DXScoreIcon_0{star}.png', filenames)
        captions = [call.args[1] for call in text.call_args_list]
        for threshold in (1737, 1839, 1900, 1941, 1982):
            self.assertIn(f'≥ {threshold}', captions)

    async def test_pagination_and_missing_icons_keep_all_players(self):
        song = make_song()
        rows = ranking.rank_rows([ranking.RankingRow(str(i), f'玩家{i}', score()) for i in range(21)])
        with (patch.object(drawing, 'music_picture', AsyncMock(return_value=self.cover)),
              patch.object(drawing, 'themed_path', return_value=self.root / 'missing.png'),
              patch.object(drawing, '_text', wraps=drawing._text) as text):
            pictures = await drawing.draw_song_ranking(song, song.difficulties[3], ranking.RankingResult(rows))
        self.assertEqual(len(pictures), 2)
        captions = [call.args[1] for call in text.call_args_list]
        self.assertEqual(sum(bool(re.fullmatch(r'玩家\d+', str(caption))) for caption in captions), 21)
        self.assertIn('SSS+', captions)

    def test_empty_result_renders_safe_summary_without_fake_players(self):
        song = make_song()
        with patch.object(drawing, '_text', wraps=drawing._text) as text:
            image = drawing.render_ranking_page(song, song.difficulties[3], ranking.RankingResult((), 2, 3), self.cover)
        self.assertEqual(image.size, (1024, 464))
        captions = [call.args[1] for call in text.call_args_list]
        self.assertIn('本群暂无已加入且有该谱面成绩的玩家', captions)
        self.assertTrue(any('查询失败 2 人' in str(caption) for caption in captions))
