import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp

from ..command import mai_base, mai_score, mai_search, mai_table
from ..libraries import maimaidx_analysis, maimaidx_source
from ..libraries.maimaidx_error import TokenNotFoundError
from ..libraries.maimaidx_model import Alias, Difficulties, Notes, Song
from ..libraries.maimaidx_music import AliasList, SongList, mai
from ..libraries.maimaidx_music_info import draw_music_play_data, draw_plate_table, draw_rating_table


def event(text='', at=None):
    components = [Comp.Plain(text)]
    if at:
        components.append(Comp.At(qq=at))
    return SimpleNamespace(
        message_str=text,
        message_obj=SimpleNamespace(message=components, message_id='1', group_id=None),
        get_sender_id=lambda: '654321',
        chain_result=lambda chain: chain,
        plain_result=lambda text: text,
    )


def song(song_id=799, title='Test Song'):
    return Song(song_id=song_id, song_name=title, type='DX', difficulties=[
        Difficulties(level_index=index, level='14', level_value=14.0, notes=Notes(tap=10, brk=1))
        for index in range(4)
    ])


async def run(handler, text, at=None):
    return [result async for result in handler(event(text, at))]


class PlayerParsingTest(unittest.TestCase):
    def test_player_types_and_at_precedence(self):
        for argument, expected in (('', ('654321', '')), ('123456', (123456, '')),
                                   ('Alice', (None, 'Alice')), ('Alice Smith', (None, 'Alice Smith')),
                                   ('[MSG_ID:abc] Alice', (None, 'Alice'))):
            self.assertEqual(mai_base.parse_player_target(event(), argument), expected)
            self.assertEqual(mai_base.parse_player_target(event(at='333333'), argument), ('333333', ''))

    def test_page_and_player_are_distinguished(self):
        for text, expected in (('', (1, '')), ('2', (2, '')), ('123456', (1, '123456')),
                               ('2 123456', (2, '123456')), ('Alice', (1, 'Alice')),
                               ('2 Alice Smith', (2, 'Alice Smith'))):
            self.assertEqual(mai_base.parse_page_player_args(text), expected)

    def test_completion_table_player_and_page_syntax(self):
        for text, expected in (('舞神完成表 1', ('舞神', 1, '')),
                               ('舞神完成表1 Alice', ('舞神', 1, 'Alice')),
                               ('舞神完成表 2 123456', ('舞神', 2, '123456')),
                               ('晓将完成表 123456', ('晓将', None, '123456')),
                               ('14完成表 Alice Smith', ('14', None, 'Alice Smith'))):
            self.assertEqual(mai_table.parse_table_player_request(text), expected)


class PlayerCommandTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.songs = patch.object(mai, 'total_list', SongList([song()]))
        self.songs.start()
        self.addCleanup(self.songs.stop)
        for module in (mai_score, mai_table):
            reply = patch.object(module, 'is_reply_enabled', return_value=False)
            reply.start()
            self.addCleanup(reply.stop)

    async def test_b50_player_forms(self):
        for text, at, expected in (('b50', None, ('654321', '')),
                                    ('b50 123456', None, (123456, '')),
                                    ('b50 Alice', None, (None, 'Alice')),
                                    ('b50 Alice', '333333', ('333333', ''))):
            with patch.object(mai_score, 'generate', AsyncMock(return_value='ok')) as generate:
                await run(mai_score.best50_handler, text, at)
                self.assertEqual(generate.await_args.args[:2], expected)

    async def test_analysis_queries_accept_all_player_forms(self):
        for handler, command, function in ((mai_score.gold_analysis_handler, '含金量', 'generate_gold_analysis'),
                                           (mai_score.water_analysis_handler, '含水量', 'generate_water_analysis')):
            for suffix, at, expected in (('', None, ('654321', '')), ('123456', None, (123456, '')),
                                         ('Alice', None, (None, 'Alice')), ('Alice', '333333', ('333333', ''))):
                with patch.object(maimaidx_analysis, function, AsyncMock(return_value='ok')) as draw:
                    await run(handler, f'{command} {suffix}', at)
                    draw.assert_awaited_once_with(qqid=expected[0], username=expected[1])

    async def test_minfo_passes_player_to_drawing_and_preserves_case(self):
        aliases = AliasList([Alias(SongID=799, Name='Test Song', Alias=['别名'])])
        with patch.object(mai, 'total_alias_list', aliases, create=True):
            for request, at, expected in (
                ('minfo 799', None, ('654321', None)), ('minfo Test Song', None, ('654321', None)),
                ('minfo 799 123456', None, (123456, None)), ('minfo Test Song Alice', None, (None, 'Alice')),
                ('minfo Test Song | Alice Smith', None, (None, 'Alice Smith')),
                ('/INFO 别名 Alice', None, (None, 'Alice')), ('minfo 799 Alice', '333333', ('333333', None)),
            ):
                with patch.object(mai_score, 'draw_music_play_data', AsyncMock(return_value='ok')) as draw:
                    await run(mai_score.minfo_handler, request, at)
                    draw.assert_awaited_once_with(expected[0], '799', username=expected[1])

    async def test_minfo_prefers_whole_title_and_reports_ambiguous_aliases(self):
        with patch.object(mai, 'total_list', SongList([song(), song(800, 'Test Song Alice')])):
            with patch.object(mai_score, 'draw_music_play_data', AsyncMock(return_value='ok')) as draw:
                await run(mai_score.minfo_handler, 'minfo Test Song Alice')
                draw.assert_awaited_once_with('654321', '800', username=None)
            aliases = AliasList([Alias(SongID=799, Name='Test Song', Alias=['重复']),
                                 Alias(SongID=800, Name='Test Song Alice', Alias=['重复'])])
            with (patch.object(mai, 'total_alias_list', aliases, create=True),
                  patch.object(mai_score, 'draw_music_play_data', AsyncMock()) as draw):
                result = await run(mai_score.minfo_handler, 'minfo 重复 Alice')
                self.assertIn('多个相同名称或别名', result[0])
                draw.assert_not_awaited()

    async def test_minfo_keeps_literal_pipe_in_complete_song_title(self):
        with patch.object(mai, 'total_list', SongList([song(800, 'Test | Song')])):
            with patch.object(mai_score, 'draw_music_play_data', AsyncMock(return_value='ok')) as draw:
                await run(mai_score.minfo_handler, 'minfo Test | Song')
                draw.assert_awaited_once_with('654321', '800', username=None)

    async def test_plate_progress_and_rise_queries_accept_player_forms(self):
        for handler, command, function in ((mai_table.plate_process_handler, '晓将进度表', 'player_plate_data'),
                                           (mai_table.rise_score_handler, '我要在14上10分', 'rise_score_data')):
            for suffix, at, expected in (('', None, ('654321', '')), ('123456', None, (123456, '')),
                                         ('Alice', None, (None, 'Alice')), ('Alice', '333333', ('333333', ''))):
                with patch.object(mai_table, function, AsyncMock(return_value='ok')) as draw:
                    await run(handler, f'{command} {suffix}', at)
                    self.assertEqual(draw.await_args.args[:2], expected)

    async def test_paged_queries_accept_qq_without_treating_it_as_page(self):
        for handler, command, function in ((mai_table.level_process_handler, '14sss未完成进度', 'level_process_data'),
                                           (mai_table.level_achievement_list_handler, '14分数列表', 'level_achievement_list_data')):
            for suffix, at, expected, page in (
                ('', None, ('654321', ''), 1), ('123456', None, (123456, ''), 1),
                ('2 123456', None, (123456, ''), 2), ('Alice', None, (None, 'Alice'), 1),
                ('2 Alice Smith', None, (None, 'Alice Smith'), 2), ('2 Alice', '333333', ('333333', ''), 2),
            ):
                with patch.object(mai_table, function, AsyncMock(return_value='ok')) as draw:
                    await run(handler, f'{command} {suffix}', at)
                    self.assertEqual(draw.await_args.args[:2], expected)
                    self.assertEqual(draw.await_args.args[-1], page)

    async def test_completion_table_accepts_player_and_preserves_pagination(self):
        for request, at, function, args, username in (
            ('14完成表 Alice', None, 'draw_rating_table', (None, '14', False), 'Alice'),
            ('14fc完成表 123456', None, 'draw_rating_table', (123456, '14', True), None),
            ('晓将完成表 Alice', None, 'draw_plate_table', (None, '暁', '将', 1), 'Alice'),
            ('舞神完成表 2 123456', None, 'draw_plate_table', (123456, '舞', '神', 2), None),
            ('舞神完成表 2 Alice', '333333', 'draw_plate_table', ('333333', '舞', '神', 2), None),
        ):
            with patch.object(mai_table, function, AsyncMock(return_value='ok')) as draw:
                await run(mai_table.table_pfm_handler, request, at)
                draw.assert_awaited_once_with(*args, username=username)

    async def test_username_is_forwarded_by_single_song_renderer(self):
        with patch.object(maimaidx_source, 'get_music_record', AsyncMock(return_value=[])) as query:
            await draw_music_play_data(None, '799', username='Alice')
        query.assert_awaited_once_with(None, '799', username='Alice')

    async def test_username_is_forwarded_by_completion_renderers(self):
        with patch.object(maimaidx_source, 'get_plate', AsyncMock(side_effect=TokenNotFoundError())) as query:
            await draw_rating_table(None, '14', username='Alice')
            query.assert_awaited_once_with(qqid=None, username='Alice')
        with (patch.object(mai, 'total_plate_id_list', {'舞': [799]}, create=True),
              patch.object(maimaidx_source, 'get_plate', AsyncMock(side_effect=TokenNotFoundError())) as query):
            await draw_plate_table(None, '舞', '神', 1, username='Alice')
            self.assertEqual(query.await_args.kwargs['username'], 'Alice')
            self.assertIsNone(query.await_args.kwargs['qqid'])


class SearchRangeTest(unittest.IsolatedAsyncioTestCase):
    async def test_constant_range_includes_both_boundaries(self):
        songs = SongList([song(799), song(800), song(801)])
        for item, value in zip(songs, (13.6, 13.9, 14.0)):
            for difficulty in item.difficulties:
                difficulty.level_value = value
        with (patch.object(mai, 'total_list', songs),
              patch.object(mai_search, 'song_list_chain', AsyncMock(return_value=[])) as draw):
            await run(mai_search.search_base_handler, '定数查歌 13.6 13.9 1')
        self.assertEqual([item.song_id for item in draw.await_args.args[1]], [799, 800])
        self.assertEqual(draw.await_args.args[2], 1)

    async def test_bpm_range_includes_both_boundaries_with_optional_page(self):
        songs = SongList([song(799), song(800), song(801)])
        for item, value in zip(songs, (180, 220, 240)):
            item.bpm = value
        for request in ('bpm查歌 180 220', 'bpm查歌 180 220 1'):
            with (patch.object(mai, 'total_list', songs),
                  patch.object(mai_search, 'song_list_chain', AsyncMock(return_value=[])) as draw):
                await run(mai_search.search_bpm_handler, request)
            self.assertEqual([item.song_id for item in draw.await_args.args[1]], [799, 800])
            self.assertEqual(draw.await_args.args[2], 1)
