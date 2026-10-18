import base64
import tempfile
import unittest
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp
from PIL import Image, ImageFont

from .. import MessageSegment
from ..command import mai_tolerance as command
from ..libraries import maimaidx_tolerance as drawing
from ..libraries.maimaidx_model import Alias, Difficulties, Notes, Song
from ..libraries.maimaidx_music import AliasList, SongList, mai


def make_song(song_id=799, title='Test Song', white=False):
    return Song(song_id=song_id, song_name=title, type='DX', difficulties=[
        Difficulties(level_index=index, level='13+', level_value=13.8,
                     notes=Notes(tap=1000, hold=50, slide=3, brk=101))
        for index in range(5 if white else 4)
    ])


def event(text):
    return SimpleNamespace(
        message_str=text,
        plain_result=lambda result: result,
        chain_result=lambda result: result,
    )


class ToleranceCalculationTest(unittest.TestCase):
    def setUp(self):
        self.data = drawing.calculate_tolerance(make_song().difficulties[3].notes)

    def test_base_score_uses_weighted_notes_without_break_bonus(self):
        self.assertEqual(self.data.base_score, 807000)
        self.assertEqual(self.data.break_count, 101)

    def test_normal_note_losses(self):
        for kind, weight in (('Tap', 500), ('Hold', 1000), ('Slide', 1500), ('Touch', 500)):
            with self.subTest(kind=kind):
                self.assertEqual(self.data.normal_losses[kind], (
                    Fraction(0), Fraction(weight * 20, 807000),
                    Fraction(weight * 50, 807000), Fraction(weight * 100, 807000),
                ))

    def test_all_eight_break_judgements_include_base_and_bonus_loss(self):
        base = (0, 0, 0, 500, 1000, 1250, 1500, 2500)
        bonus = (Fraction(0), Fraction(1, 4), Fraction(1, 2),
                 Fraction(3, 5), Fraction(3, 5), Fraction(3, 5), Fraction(7, 10), Fraction(1))
        expected = tuple(Fraction(loss * 100, 807000) + extra / 101
                         for loss, extra in zip(base, bonus))
        self.assertEqual(self.data.break_losses, expected)
        self.assertEqual(drawing.format_change(-expected[1]), '-0.002475%')
        self.assertEqual(drawing.format_change(-expected[3]), '-0.067898%')
        self.assertEqual(drawing.format_change(-expected[7]), '-0.319690%')

    def test_perfect_breaks_only_lose_bonus(self):
        self.assertEqual(self.data.break_losses[:3], (Fraction(0), Fraction(1, 404), Fraction(1, 202)))

    def test_charts_without_breaks_still_compute_normal_losses(self):
        data = drawing.calculate_tolerance(Notes(tap=10))
        self.assertEqual(data.break_losses, ())
        self.assertEqual(data.normal_losses['Tap'][1], Fraction(2))

    def test_empty_or_negative_note_counts_are_rejected(self):
        for notes in (Notes(), Notes(tap=-1, brk=5)):
            with self.subTest(notes=notes):
                with self.assertRaises(ValueError):
                    drawing.calculate_tolerance(notes)

    def test_modes_transform_all_judgements(self):
        for mode in ('101-', '100-', '0+'):
            normal, breaks = drawing.tolerance_values(self.data, mode)
            for kind, losses in self.data.normal_losses.items():
                start = Fraction(drawing.NOTE_WEIGHTS[kind] * 100, self.data.base_score) if mode == '0+' else 0
                self.assertEqual(normal[kind], tuple(start - loss for loss in losses))
            start = Fraction(0) if mode == '101-' else Fraction(1, 101)
            if mode == '0+':
                start += Fraction(250000, self.data.base_score)
            self.assertEqual(breaks, tuple(start - loss for loss in self.data.break_losses))

    def test_modes_sum_to_correct_full_chart_totals(self):
        for mode, perfect_total, miss_total in (('101-', 0, -101), ('100-', 1, -100), ('0+', 101, 0)):
            normal, breaks = drawing.tolerance_values(self.data, mode)
            for index, expected in ((0, perfect_total), (-1, miss_total)):
                actual = sum(values[index] * self.data.counts[kind] for kind, values in normal.items())
                actual += breaks[index] * self.data.break_count
                self.assertEqual(actual, expected, (mode, index))

    def test_modes_without_breaks_and_invalid_mode(self):
        data = drawing.calculate_tolerance(Notes(tap=10))
        for mode in ('101-', '100-', '0+'):
            normal, breaks = drawing.tolerance_values(data, mode)
            self.assertEqual(breaks, ())
            self.assertEqual(normal['Tap'][0], 10 if mode == '0+' else 0)
        with self.assertRaises(ValueError):
            drawing.tolerance_values(data, '99-')

    def test_signed_format(self):
        self.assertEqual(drawing.format_change(Fraction(1, 20)), '+0.050000%')
        self.assertEqual(drawing.format_change(Fraction(0)), '0.000000%')


class ToleranceRequestTest(unittest.IsolatedAsyncioTestCase):
    def test_difficulty_and_song_can_be_separated_or_adjacent(self):
        for request in ('容错计算 紫799', '容错计算 紫 799', '/容错计算紫谱799',
                        '容错计算 MASTER799', '容错计算 master 799'):
            with self.subTest(request=request):
                self.assertEqual(command.parse_tolerance_request(request), (3, '799', '101-'))
        self.assertEqual(command.parse_tolerance_request('容错计算 Re:Master 歌名'), (4, '歌名', '101-'))
        self.assertEqual(command.parse_tolerance_request('容错计算 红Test Song'), (2, 'Test Song', '101-'))

    def test_optional_mode_only_at_end_and_space_separated(self):
        for mode in ('0+', '100-', '101-'):
            for query in ('799', '别名', 'Test Song'):
                request = f'容错计算 紫{query}  {mode} '
                self.assertEqual(command.parse_tolerance_request(request), (3, query, mode))
        self.assertEqual(command.parse_tolerance_request('容错计算 紫Song 0+ Mix'), (3, 'Song 0+ Mix', '101-'))
        self.assertEqual(command.parse_tolerance_request('容错计算 紫Song100-'), (3, 'Song100-', '101-'))

    def test_missing_or_invalid_parameters(self):
        for text in ('容错计算', '容错计算 紫', '容错计算 13 799', '容错计算 紫 0+', '容错计算 紫 100-'):
            self.assertIsNone(command.parse_tolerance_request(text))

    async def test_resolves_ids_aliases_exact_and_partial_titles(self):
        song = make_song()
        aliases = AliasList([Alias(SongID=799, Name=song.song_name, Alias=['别名'])])
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(mai, 'total_alias_list', aliases, create=True),
              patch.object(command.maiApi, 'get_songs', AsyncMock()) as online):
            for query in ('799', 'id799', 'ID 799', '别名', 'test song', 'test'):
                with self.subTest(query=query):
                    matches = await command.find_tolerance_songs(query)
                    self.assertEqual([item.song_id for item in matches], [song.song_id])
            online.assert_not_awaited()

    async def test_online_alias_fallback_and_network_failure(self):
        song = make_song()
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(mai, 'total_alias_list', AliasList(), create=True),
              patch.object(command.maiApi, 'get_songs', AsyncMock(return_value=[
                  Alias(SongID=799, Name=song.song_name, Alias=['remote']),
              ])) as online):
            self.assertEqual(await command.find_tolerance_songs('remote'), [song])
            online.side_effect = RuntimeError('offline')
            self.assertEqual(await command.find_tolerance_songs('not-found'), [])

    async def test_missing_remaster_returns_text_without_drawing(self):
        with (patch.object(mai, 'total_list', SongList([make_song()])),
              patch.object(command, 'draw_tolerance_table', AsyncMock()) as draw):
            results = [result async for result in command.tolerance_handler(event('容错计算 白799'))]
        self.assertEqual(results, ['该乐曲没有这个难度'])
        draw.assert_not_awaited()

    async def test_duplicate_titles_return_candidates_instead_of_random_song(self):
        with (patch.object(mai, 'total_list', SongList([make_song(), make_song(10799)])),
              patch.object(mai, 'total_alias_list', AliasList(), create=True),
              patch.object(command, 'draw_tolerance_table', AsyncMock()) as draw):
            results = [result async for result in command.tolerance_handler(event('容错计算 紫Test Song'))]
        self.assertIn('799：Test Song', results[0])
        self.assertIn('10799：Test Song', results[0])
        draw.assert_not_awaited()

    async def test_at_segments_are_excluded_and_selected_difficulty_is_used(self):
        request = event('容错计算 紫799 @other(123)')
        request.message_obj = SimpleNamespace(message=[
            Comp.Plain('容错计算 紫799'), Comp.At(qq='123'),
        ])
        song = make_song()
        picture = MessageSegment.image('base64://aW1hZ2U=')
        with (patch.object(mai, 'total_list', SongList([song])),
              patch.object(command, 'draw_tolerance_table', AsyncMock(return_value=picture)) as draw):
            results = [result async for result in command.tolerance_handler(request)]
        draw.assert_awaited_once_with(song, song.difficulties[3], '101-')
        self.assertIsInstance(results[0][0], Comp.Image)

    async def test_handler_passes_selected_mode_without_changing_song_query(self):
        song = make_song()
        for mode in ('0+', '100-'):
            with (patch.object(mai, 'total_list', SongList([song])),
                  patch.object(command, 'draw_tolerance_table', AsyncMock(return_value=MessageSegment.image('base64://aW1hZ2U='))) as draw):
                results = [result async for result in command.tolerance_handler(event(f'容错计算 紫799 {mode}'))]
                draw.assert_awaited_once_with(song, song.difficulties[3], mode)
                self.assertIsInstance(results[0][0], Comp.Image)


class ToleranceImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_renders_png_with_all_break_loss_tiers(self):
        font = ImageFont.load_default(size=20)
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / 'cover.png'
            Image.new('RGB', (96, 96), 'blue').save(cover)
            with (patch.object(drawing, 'music_picture', AsyncMock(return_value=cover)),
                  patch.object(drawing.ImageFont, 'truetype', return_value=font),
                  patch.object(drawing, '_text', wraps=drawing._text) as text):
                song = make_song()
                result = await drawing.draw_tolerance_table(song, song.difficulties[3])
        image = Image.open(BytesIO(base64.b64decode(result.data['file'].removeprefix('base64://'))))
        self.assertEqual(image.size, (1120, 798))
        self.assertEqual(image.getpixel((220, 500)), (255, 190, 0))
        captions = [call.args[2] for call in text.call_args_list]
        for caption in ('CP / 2600', 'P / 2550', 'P / 2500', 'G / 2000',
                        'G / 1500', 'G / 1250', 'GOOD / 1000', 'MISS'):
            self.assertIn(caption, captions)

    async def test_image_mode_labels_and_signed_break_values(self):
        font = ImageFont.load_default(size=20)
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / 'cover.png'
            Image.new('RGB', (96, 96), 'blue').save(cover)
            for mode in ('0+', '100-'):
                with (patch.object(drawing, 'music_picture', AsyncMock(return_value=cover)),
                      patch.object(drawing.ImageFont, 'truetype', return_value=font),
                      patch.object(drawing, '_text', wraps=drawing._text) as text):
                    song = make_song()
                    await drawing.draw_tolerance_table(song, song.difficulties[3], mode)
                    captions = [call.args[2] for call in text.call_args_list]
                    self.assertIn(mode[:-1] + '%' + mode[-1], captions)
                    self.assertTrue(any(value.startswith('+') and value.endswith('%') for value in captions))
                    if mode == '100-':
                        self.assertIn('+0.009901%', captions)
                        self.assertIn('-0.061958%', captions)
