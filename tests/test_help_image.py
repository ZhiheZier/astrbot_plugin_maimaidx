import ast
import asyncio
import base64
import re
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp
from PIL import Image, ImageDraw, ImageFont

from .. import MessageSegment, Root, __version__
from ..command import mai_base
from ..libraries import maimaidx_help as help_image


def entries():
    return [entry for column in help_image.HELP_COLUMNS for section in column for entry in section.entries]


def command_decorators():
    tree = ast.parse((Root / 'main.py').read_text(encoding='utf-8'))
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MaimaiDXPlugin')
    commands = {}
    for node in plugin.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in ('regex', 'command')):
                commands[node.name] = (decorator.func.attr, ast.literal_eval(decorator.args[0]))
    return commands


class HelpCatalogueTest(unittest.TestCase):
    def test_every_command_handler_is_documented(self):
        documented = {handler for entry in entries() for handler in entry.handlers}
        self.assertEqual(documented, set(command_decorators()))

    def test_catalogue_contains_no_empty_or_duplicate_entries(self):
        commands = [entry.command for entry in entries()]
        self.assertEqual(len(commands), len(set(commands)))
        for entry in entries():
            self.assertTrue(entry.handlers)
            self.assertTrue(entry.command)
            self.assertTrue(entry.description)

    def test_help_routes_are_separate_and_accept_any_english_case(self):
        for handler, aliases in (('maimaidxhelp', ('帮助maimaidx', 'helpmaimaidx')),
                                 ('maihelp', ('maihelp',))):
            entry = next(entry for entry in entries() if handler in entry.handlers)
            kind, pattern = command_decorators()[handler]
            self.assertEqual(kind, 'regex')
            for alias in aliases:
                self.assertIn(alias, entry.command)
                for spelling in (alias, alias.upper(), alias.replace('dx', 'DX')):
                    for prefix in ('', '/'):
                        self.assertIsNotNone(re.fullmatch(pattern, prefix + spelling))
            for invalid in ('帮助maimai', 'helpmaimaidx排卡', 'helpmaimaidx 1', 'xhelpmaimai', 'maihelp 1', 'xmaihelp'):
                self.assertIsNone(re.fullmatch(pattern, invalid))
        self.assertIsNone(re.fullmatch(command_decorators()['maimaidxhelp'][1], 'maihelp'))
        self.assertIsNone(re.fullmatch(command_decorators()['maihelp'][1], '帮助maimaidx'))
        for handler in ('maimaidxhelp', 'maihelp'):
            for alias in ('helpmaimai', 'HELPMAIMAI', '/helpmaimai', '/HELPMAIMAI'):
                self.assertIsNone(re.fullmatch(command_decorators()[handler][1], alias))
        entry = next(entry for entry in entries() if 'maimaidxhelp' in entry.handlers)
        self.assertNotIn('helpmaimai', entry.command.split(' / '))
        pattern = command_decorators()['dx_arcade_help'][1]
        for prefix in ('', '/'):
            self.assertIsNotNone(re.fullmatch(pattern, prefix + '排卡帮助'))
        self.assertIsNone(re.fullmatch(pattern, '帮助maimaiDX排卡'))
        readme = (Root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('以上入口均不区分英文大小写', readme)
        self.assertIn('开头可加 `/`', readme)

    def test_prefix_and_admin_markers_match_documented_permissions(self):
        by_handler = {handler: entry for entry in entries() for handler in entry.handlers}
        for handler in ('search_alias_song', 'alias_song', 'rating_table', 'plate_process', 'alias_switch'):
            self.assertTrue(by_handler[handler].prefix, handler)
            self.assertIn('※', help_image.help_command_label(by_handler[handler]))
        for handler in ('update_data', 'update_alias', 'update_table', 'update_plate', 'alias_switch_on_off',
                        'toggle_maimai_feature', 'add_arcade', 'delete_arcade'):
            entry = by_handler[handler]
            self.assertTrue(entry.admin, handler)
            self.assertIn('#', help_image.help_command_label(entry))
        for handler in ('reset_guess', 'guess_on_off', 'subscribe_arcade', 'modify_arcade', 'toggle_arcade'):
            entry = by_handler[handler]
            self.assertTrue(entry.group_admin, handler)
            self.assertFalse(entry.admin, handler)
            self.assertIn('☆', help_image.help_command_label(entry))
            self.assertNotIn('#', help_image.help_command_label(entry))
        self.assertTrue(all('#/☆' not in help_image.help_command_label(entry) for entry in entries()))
        self.assertFalse(by_handler['alias_switch'].admin)
        self.assertFalse(by_handler['best50'].admin)

    def test_player_arguments_and_english_difficulties_are_explained(self):
        notes = '\n'.join(help_image.HELP_NOTES)
        self.assertIn('[玩家]可填QQ号、@群成员或水鱼用户名', notes)
        self.assertNotIn('用户名不是QQ昵称', notes)
        self.assertNotIn('不要求被查询者', notes)
        self.assertIn('管理员也可使用所有☆指令', notes)
        self.assertNotIn('BOT管理员', notes)
        for color, name in (('绿', 'Basic'), ('黄', 'Advanced'), ('红', 'Expert'),
                            ('紫', 'Master'), ('白', 'Re:Master')):
            self.assertIn(f'{color}={name}', notes)
        entry = next(entry for entry in entries() if 'minfo' in entry.handlers)
        self.assertIn('[玩家]', entry.command)
        for entry in entries():
            self.assertNotIn('[用户名]', entry.command)
            self.assertNotIn('[@群成员]', entry.command)
            self.assertNotIn('BOT管理员', entry.description)

    def test_single_and_range_search_share_one_entry(self):
        for handler, parameter in (('search_base', '定数'), ('search_bpm', 'BPM')):
            matches = [entry for entry in entries() if handler in entry.handlers]
            self.assertEqual(len(matches), 1)
            self.assertIn(f'<{parameter}> / <下限> <上限> [页码]', matches[0].command)
        add = next(entry for entry in entries() if 'add_arcade' in entry.handlers)
        self.assertIn('[别名]', add.command)
        self.assertNotIn('...', add.command)

    def test_representative_examples_match_actual_command_filters(self):
        commands = command_decorators()
        examples = (
            ('maimaidxhelp', '帮助maimaiDX'), ('maihelp', 'maihelp'),
            ('dx_arcade_help', '排卡帮助'), ('maimaidxrepo', '项目地址maimaiDX'),
            ('search_alias_song', '潘是什么歌'), ('random_song', '来个dx紫13+'),
            ('reset_guess', '重置猜歌'), ('guess_pic', '猜曲绘'), ('guess_on_off', '开启mai猜歌'),
            ('tolerance', '容错计算 紫799 0+'), ('score', '分数线 紫 799 sss+'),
            ('calculate_score', '13.2的100.5是多少分'), ('plate_process', '晓将进度表'),
            ('table_pfm', '舞神完成表 1'), ('table_pfm', '14fc完成表'),
            ('table_pfm', '舞神完成表 2 Alice'), ('table_pfm', '晓将完成表 123456'),
            ('rating_table', '13+定数表'), ('level_process', '14sss未完成进度 1'),
            ('level_achievement_list', '14分数列表 1'), ('rise_score', '我要在14上10分'),
            ('rise_score', '我要上10分'), ('auth_code', '水鱼授权码 XXXX-XXXX-XXXX'),
            ('auth_code', '落雪授权码 XXXX-XXXX-XXXX'), ('bind_divingfish', '绑定水鱼'),
            ('bind_lxns', '绑定落雪'), ('alias_song', 'id799有什么别名'),
            ('arcade_person', '机厅=-1'), ('arcade_person', '机厅+1'),
            ('arcade_query_person', '机厅几人'), ('toggle_arcade', '开启排卡'),
            ('all_songs_best50', '全曲b50'), ('dx_star_best50', '五星b50'),
            ('ap_plus50', '理论b50'), ('fitted_best50', '拟合b50'),
        )
        for handler, example in examples:
            with self.subTest(handler=handler, example=example):
                kind, pattern = commands[handler]
                self.assertEqual(kind, 'regex')
                self.assertIsNotNone(re.match(pattern, example))


class HelpRenderingTest(unittest.TestCase):
    def setUp(self):
        self.font = ImageFont.load_default(size=23)

    def test_wrap_keeps_all_characters_and_respects_width(self):
        text = '容错计算 Master Test Song 0+，难度后可以空格。'
        lines = help_image.wrap_help_text(text, self.font, 170)
        self.assertGreater(len(lines), 1)
        self.assertEqual(''.join(lines), text)
        self.assertTrue(all(self.font.getlength(line) <= 170 for line in lines))
        self.assertEqual(help_image.wrap_help_text('one\ntwo', self.font, 100), ('one', 'two'))
        with self.assertRaises(ValueError):
            help_image.wrap_help_text('W', self.font, 1)

    def test_wrapping_keeps_ascii_words_and_closing_punctuation_together(self):
        lines = help_image.wrap_help_text('测试GREAT结果。', self.font, 100)
        self.assertEqual(''.join(lines), '测试GREAT结果。')
        self.assertTrue(any('GREAT' in line for line in lines))
        self.assertTrue(all(not line.startswith('。') for line in lines))

    def test_bracketed_parameters_do_not_break_across_lines(self):
        text = '添加机厅 <店名> <地址> <机台数量> [别名...]；删除时填写完整店名。'
        lines = help_image.wrap_help_text(text, self.font, 180)
        self.assertEqual(''.join(lines), text)
        self.assertTrue(any('[别名...]' in line for line in lines))
        self.assertTrue(all(not line.startswith((']', '>', '）', '。', '；')) for line in lines))
        for entry in entries():
            for text in (help_image.help_command_label(entry), entry.description):
                lines = help_image.wrap_help_text(text, self.font, 626)
                self.assertTrue(all(not line.startswith((']', '>')) for line in lines), text)

    def test_single_image_contains_all_text_within_bounds(self):
        original_text = ImageDraw.ImageDraw.text
        drawn = []

        def record(draw, xy, text, *args, **kwargs):
            box = draw.textbbox(xy, text, font=kwargs['font'], anchor=kwargs.get('anchor'))
            drawn.append((text, box))
            return original_text(draw, xy, text, *args, **kwargs)

        with (patch.object(help_image.ImageFont, 'truetype', return_value=self.font),
              patch.object(ImageDraw.ImageDraw, 'text', new=record)):
            image = help_image.render_help_image('测试名称' * 80, 'font.ttf')
        self.assertEqual(image.width, 2160)
        for text, (left, top, right, bottom) in drawn:
            self.assertGreaterEqual(left, 0, text)
            self.assertGreaterEqual(top, 0, text)
            self.assertLessEqual(right, image.width, text)
            self.assertLessEqual(bottom, image.height, text)
        texts = [text for text, _ in drawn]
        for column in help_image.HELP_COLUMNS:
            for section in column:
                self.assertIn(section.title, texts)
                for entry in section.entries:
                    for part in (help_image.help_command_label(entry), entry.description):
                        width = 626 - help_image.NEW_BADGE_SPACE if part == help_image.help_command_label(entry) and entry.new_in == __version__ else 626
                        for line in help_image.wrap_help_text(part, self.font, width):
                            self.assertIn(line, texts)
        self.assertIn('maimaiDX 使用说明', texts)
        self.assertNotIn('舞萌 DX', texts)
        self.assertNotEqual(image.getpixel((0, 0)), image.getpixel((image.width - 1, image.height - 1)))

    def test_version_label_and_new_badges_follow_release_version(self):
        expected = sum(entry.new_in == __version__ for entry in entries())
        self.assertGreater(expected, 0)
        for version, count in ((__version__, expected), ('9.0.0', 0)):
            with (patch.object(help_image.ImageFont, 'truetype', return_value=self.font),
                  patch.object(ImageDraw.ImageDraw, 'text', autospec=True) as text):
                help_image.render_help_image('Bot', 'font.ttf', version=version)
                drawn = [call.args[2] for call in text.call_args_list]
                self.assertIn(f'v{version}', drawn)
                self.assertEqual(drawn.count('新'), count)

    def test_cached_image_changes_with_bot_name_font_and_catalogue(self):
        help_image._cached_help_image.cache_clear()
        try:
            with patch.object(help_image, 'render_help_image', return_value=Image.new('RGB', (20, 20))) as render:
                for _ in range(2):
                    help_image._cached_help_image('A', 'font.ttf', 1, 20, help_image.HELP_COLUMNS)
                render.assert_called_once()
                help_image._cached_help_image('B', 'font.ttf', 1, 20, help_image.HELP_COLUMNS)
                help_image._cached_help_image('B', 'font.ttf', 2, 20, help_image.HELP_COLUMNS)
                help_image._cached_help_image('B', 'font.ttf', 2, 20, help_image.HELP_COLUMNS[:1])
                help_image._cached_help_image('B', 'font.ttf', 2, 20, help_image.HELP_COLUMNS[:1], '9.0.0')
                self.assertEqual(render.call_count, 5)
        finally:
            help_image._cached_help_image.cache_clear()

    def test_new_badge_follows_last_command_line(self):
        entry = help_image.HelpEntry(('test',), '容错计算 <难度><歌曲> [模式]', '测试说明', new_in=__version__)
        columns = ((help_image.HelpSection('测试', '#168681', (entry,)),),)
        calls = []
        original = ImageDraw.ImageDraw.text

        def record(draw, xy, text, *args, **kwargs):
            calls.append((xy, text))
            return original(draw, xy, text, *args, **kwargs)

        with (patch.object(help_image.ImageFont, 'truetype', return_value=self.font),
              patch.object(ImageDraw.ImageDraw, 'text', new=record)):
            help_image.render_help_image('Bot', 'font.ttf', columns=columns)
        command = next((xy, text) for xy, text in calls if text == entry.command)
        badge = next(xy for xy, text in calls if text == '新')
        self.assertEqual(command[0][0], help_image.MARGIN + help_image.COLUMN_PADDING)
        self.assertGreater(badge[0] - help_image.NEW_BADGE_WIDTH / 2,
                           command[0][0] + self.font.getlength(command[1]))


class HelpCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_handler_sends_static_image_without_rendering(self):
        event = SimpleNamespace(chain_result=lambda chain: chain, plain_result=lambda text: text)
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(help_image, 'draw_help_image', AsyncMock()) as render):
            root = Path(directory)
            Image.new('RGB', (20, 20)).save(root / 'maimaidxhelp.png')
            with patch.object(mai_base, 'Root', root):
                results = [result async for result in mai_base.legacy_help_handler(event)]
            self.assertIsInstance(results[0][0], Comp.Image)
            render.assert_not_awaited()

    async def test_handler_sends_exactly_one_generated_image(self):
        event = SimpleNamespace(chain_result=lambda chain: chain, plain_result=lambda text: text)
        picture = MessageSegment.image('base64://aW1hZ2U=')
        with patch.object(help_image, 'draw_help_image', AsyncMock(return_value=picture)) as draw:
            results = [result async for result in mai_base.maimaidxhelp_handler(event)]
        draw.assert_awaited_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 1)
        self.assertIsInstance(results[0][0], Comp.Image)

    async def test_render_runs_in_worker_thread_and_concurrent_requests_share_cache(self):
        help_image._cached_help_image.cache_clear()
        main_thread = threading.get_ident()
        worker_threads = []

        def render(*args):
            worker_threads.append(threading.get_ident())
            return Image.new('RGB', (20, 20))

        try:
            with tempfile.TemporaryDirectory() as directory:
                font = Path(directory) / 'font.ttf'
                font.touch()
                with (patch.object(help_image, 'SIYUAN', font),
                      patch.object(help_image, 'render_help_image', side_effect=render)):
                    pictures = await asyncio.gather(*(help_image.draw_help_image() for _ in range(3)))
            self.assertEqual(len(worker_threads), 1)
            self.assertNotEqual(worker_threads[0], main_thread)
            self.assertTrue(all(picture.data == pictures[0].data for picture in pictures))
            png = base64.b64decode(pictures[0].data['file'].removeprefix('base64://'))
            self.assertEqual(Image.open(BytesIO(png)).size, (20, 20))
        finally:
            help_image._cached_help_image.cache_clear()

    async def test_missing_font_uses_existing_image_or_clear_error(self):
        event = SimpleNamespace(chain_result=lambda chain: chain, plain_result=lambda text: text)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(mai_base, 'Root', root),
                  patch.object(help_image, 'draw_help_image', AsyncMock(side_effect=OSError('missing font'))),
                  patch.object(mai_base.log, 'warning')):
                results = [result async for result in mai_base.maimaidxhelp_handler(event)]
                self.assertIn('字体或资源缺失', results[0])
                Image.new('RGB', (20, 20)).save(root / 'maimaidxhelp.png')
                results = [result async for result in mai_base.maimaidxhelp_handler(event)]
                self.assertEqual(len(results[0]), 1)
                self.assertIsInstance(results[0][0], Comp.Image)
