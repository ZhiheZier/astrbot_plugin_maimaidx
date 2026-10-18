import unittest
import base64
import tempfile
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp
from PIL import Image, ImageFont

from .. import MessageSegment
from ..command import mai_score
from ..command.mai_score import score_handler
from ..libraries import maimaidx_tolerance as drawing
from ..libraries.maimaidx_model import Alias, Difficulties, Notes, Song
from ..libraries.maimaidx_music import AliasList, SongList, mai


class FakeEvent:
    def __init__(self, message: str):
        self.message_str = message

    @staticmethod
    def plain_result(message: str) -> str:
        return message

    @staticmethod
    def chain_result(chain):
        return chain


async def collect_results(message: str) -> list[str]:
    return [result async for result in score_handler(FakeEvent(message))]


def make_song() -> Song:
    difficulties = [
        Difficulties(
            level_index=index,
            level=str(index + 1),
            level_value=float(index + 1),
            notes=Notes(total=5, tap=1, hold=1, slide=1, touch=1, brk=1),
        )
        for index in range(4)
    ]
    return Song(song_id=799, song_name='test', type='DX', difficulties=difficulties)


class ScoreLineTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_format_returns_help_without_logging_exception(self):
        with (
            patch.object(mai, 'total_list', SongList([make_song()])),
            patch.object(mai_score.log, 'exception') as exception_log,
        ):
            results = await collect_results('分数线 13 99')

        self.assertEqual(results, ['格式错误，输入"分数线 帮助"以查看帮助信息'])
        exception_log.assert_not_called()

    async def test_valid_command_still_calculates(self):
        song = make_song()
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(mai_score, 'draw_score_line_table', AsyncMock(
                  return_value=MessageSegment.image('base64://aW1hZ2U='))) as draw):
            results = await collect_results('分数线 紫799 100')

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0][0], Comp.Image)
        draw.assert_awaited_once_with(song, song.difficulties[3], Fraction(100))

    def test_parser_supports_difficulty_spacing_and_song_names(self):
        for request, index, query, target in (
            ('分数线 紫799 100.5', 3, '799', Fraction('100.5')),
            ('分数线 紫 799 sss+', 3, '799', Fraction('100.5')),
            ('/分数线 MASTERid799 100%', 3, 'id799', Fraction(100)),
            ('分数线 白谱 歌曲别名 SS+', 4, '歌曲别名', Fraction('99.5')),
            ('分数线 Re:Master Test Song sss', 4, 'Test Song', Fraction(100)),
            ('分数线 红分数线的歌名 99.99', 2, '分数线的歌名', Fraction('99.99')),
        ):
            with self.subTest(request=request):
                self.assertEqual(mai_score.parse_score_line_request(request), (index, query, target))

    def test_all_rank_thresholds(self):
        for rank, value in (('d', 0), ('c', 50), ('b', 60), ('bb', 70), ('bbb', 75),
                            ('a', 80), ('aa', 90), ('aaa', 94), ('s', 97), ('s+', 98),
                            ('ss', 99), ('ss+', 99.5), ('sss', 100), ('sss+', 100.5), ('sssp', 100.5)):
            self.assertEqual(mai_score.parse_score_line_request(f'分数线 紫799 {rank}'),
                             (3, '799', Fraction(str(value))))

    def test_parser_rejects_missing_or_invalid_targets(self):
        for request in ('分数线', '分数线 紫799', '分数线 紫 100', '分数线 紫799 ssss',
                        '分数线 紫799 nan', '分数线 紫799 inf', '分数线 紫799 1e2',
                        '分数线 紫799 -1', '分数线 13 799 100', '分数线 紫799 100 more'):
            self.assertIsNone(mai_score.parse_score_line_request(request), request)

    async def test_help_returns_text_without_loading_or_drawing(self):
        with (patch.object(mai, 'total_list', SongList()),
              patch.object(mai_score, 'draw_score_line_table', AsyncMock()) as draw):
            results = await collect_results('/分数线 帮助')
        self.assertIn('歌曲ID/别名/名称', results[0])
        self.assertIn('sss+', results[0])
        draw.assert_not_awaited()

    async def test_exact_title_with_spaces_and_at_segments(self):
        song = make_song()
        song.song_name = 'Test Song'
        request = FakeEvent('分数线 紫Test Song sss+ @other(123)')
        request.message_obj = SimpleNamespace(message=[Comp.Plain('分数线 紫Test Song sss+'), Comp.At(qq='123')])
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(mai_score, 'draw_score_line_table', AsyncMock(
                  return_value=MessageSegment.image('base64://aW1hZ2U='))) as draw):
            results = [result async for result in score_handler(request)]
        draw.assert_awaited_once_with(song, song.difficulties[3], Fraction('100.5'))
        self.assertIsInstance(results[0][0], Comp.Image)

    async def test_missing_difficulty_or_song_and_ambiguous_names(self):
        song = make_song()
        other = song.model_copy(update={'song_id': 10799})
        with (patch.object(mai, 'total_list', SongList([song, other])),
              patch.object(mai_score, 'draw_score_line_table', AsyncMock()) as draw):
            self.assertEqual(await collect_results('分数线 白799 100'), ['该乐曲没有这个难度'])
            self.assertIn('未找到', (await collect_results('分数线 紫999999 100'))[0])
            self.assertIn('找到 2 首歌曲', (await collect_results('分数线 紫test 100'))[0])
        draw.assert_not_awaited()

    async def test_target_out_of_range_returns_text(self):
        with patch.object(mai_score, 'draw_score_line_table', AsyncMock()) as draw:
            self.assertIn('0% 至 101%', (await collect_results('分数线 紫799 101.1'))[0])
        draw.assert_not_awaited()

    async def test_alias_uses_same_song_lookup_as_tolerance(self):
        song = make_song()
        aliases = AliasList([Alias(SongID=799, Name=song.song_name, Alias=['别名'])])
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(mai, 'total_alias_list', aliases, create=True),
              patch.object(mai_score, 'draw_score_line_table', AsyncMock(
                  return_value=MessageSegment.image('base64://aW1hZ2U='))) as draw):
            await collect_results('分数线 紫 别名 SS+')
        draw.assert_awaited_once_with(song, song.difficulties[3], Fraction('99.5'))

    async def test_invalid_note_data_returns_text_without_traceback(self):
        song = make_song()
        song.difficulties[3].notes = Notes()
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(mai_score.log, 'exception') as exception_log):
            self.assertIn('暂无有效物量', (await collect_results('分数线 紫799 sss+'))[0])
        exception_log.assert_not_called()


class ScoreLineCalculationTest(unittest.TestCase):
    def setUp(self):
        self.notes = Notes(tap=1000, hold=50, slide=3, brk=101)

    def test_sss_plus_limits_include_all_break_great_tiers(self):
        data = drawing.calculate_score_line(self.notes, Fraction('100.5'))
        self.assertEqual(data.budget, Fraction(1, 2))
        self.assertEqual(data.normal_limits, {'Tap': (40, 16, 8), 'Hold': (20, 8, 4),
                                              'Slide': (3, 3, 2), 'Touch': (0, 0, 0)})
        self.assertEqual(data.break_limits, (7, 3, 3, 2, 1))

    def test_exact_boundary_uses_fractions_without_float_rounding(self):
        loss = drawing.calculate_tolerance(self.notes).normal_losses['Tap'][1]
        target = 101 - 40 * loss
        self.assertEqual(drawing.calculate_score_line(self.notes, target).normal_limits['Tap'][0], 40)
        self.assertEqual(drawing.calculate_score_line(self.notes, target + Fraction(1, 10**12)).normal_limits['Tap'][0], 39)

    def test_full_score_and_zero_target(self):
        full = drawing.calculate_score_line(self.notes, Fraction(101))
        self.assertTrue(all(limit == 0 for values in full.normal_limits.values() for limit in values))
        self.assertEqual(full.break_limits, (0, 0, 0, 0, 0))
        zero = drawing.calculate_score_line(self.notes, Fraction(0))
        for kind, limits in zero.normal_limits.items():
            self.assertEqual(limits, (zero.counts[kind],) * 3)
        self.assertEqual(zero.break_limits, (101,) * 5)

    def test_every_limit_fits_budget_and_next_note_exceeds_budget_or_count(self):
        losses = drawing.calculate_tolerance(self.notes)
        for target in ('0', '97', '99.5', '100', '100.5', '100.99999', '101'):
            data = drawing.calculate_score_line(self.notes, Fraction(target))
            pairs = [(data.normal_limits[kind], values[1:], data.counts[kind])
                     for kind, values in losses.normal_losses.items()]
            pairs.append((data.break_limits, losses.break_losses[3:], data.counts['Break']))
            for limits, values, count in pairs:
                for limit, loss in zip(limits, values):
                    self.assertLessEqual(limit * loss, data.budget)
                    if limit < count:
                        self.assertGreater((limit + 1) * loss, data.budget)

    def test_chart_without_breaks_has_100_percent_maximum(self):
        data = drawing.calculate_score_line(Notes(tap=10), Fraction(99))
        self.assertEqual(data.maximum, 100)
        self.assertEqual(data.budget, 1)
        self.assertEqual(data.break_limits, ())
        self.assertEqual(data.normal_limits['Tap'], (0, 0, 0))
        for target in ('100.5', '-1', '102'):
            with self.assertRaises(ValueError):
                drawing.calculate_score_line(Notes(tap=10), Fraction(target))


class ScoreLineImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_png_has_notes_across_and_judgements_down(self):
        font = ImageFont.load_default(size=20)
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / 'cover.png'
            Image.new('RGB', (96, 96), 'blue').save(cover)
            with (patch.object(drawing, 'music_picture', AsyncMock(return_value=cover)),
                  patch.object(drawing.ImageFont, 'truetype', return_value=font),
                  patch.object(drawing, '_text', wraps=drawing._text) as text):
                song = make_song()
                song.difficulties[3].notes = Notes(tap=1000, hold=50, slide=3, brk=101)
                result = await drawing.draw_score_line_table(song, song.difficulties[3], Fraction('100.5'))
        image = Image.open(BytesIO(base64.b64decode(result.data['file'].removeprefix('base64://'))))
        self.assertEqual(image.size, (1120, 714))
        self.assertEqual(image.getpixel((25, 275)), (236, 60, 120))
        calls = text.call_args_list
        for index, label in enumerate(('TAP', 'HOLD', 'SLIDE', 'TOUCH', 'BREAK')):
            call = next(call for call in calls if call.args[2] == label)
            self.assertEqual(call.args[1][1], 214)
            self.assertEqual(call.args[1][0], (192, 373, 554, 735, 916)[index] + 8)
        captions = [call.args[2] for call in calls]
        for value in ('GREAT', 'GOOD', 'MISS', '40', '2000: 7', '1500: 3', '1250: 3', '-'):
            self.assertIn(value, captions)

    async def test_no_break_chart_renders_placeholder(self):
        font = ImageFont.load_default(size=20)
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / 'cover.png'
            Image.new('RGB', (96, 96), 'blue').save(cover)
            with (patch.object(drawing, 'music_picture', AsyncMock(return_value=cover)),
                  patch.object(drawing.ImageFont, 'truetype', return_value=font),
                  patch.object(drawing, '_text', wraps=drawing._text) as text):
                song = make_song()
                song.difficulties[3].notes = Notes(tap=10)
                await drawing.draw_score_line_table(song, song.difficulties[3], Fraction(99))
                placeholders = [call for call in text.call_args_list if call.args[2] == '-' and call.args[1][0] == 924]
                self.assertEqual(len(placeholders), 3)


if __name__ == '__main__':
    unittest.main()
