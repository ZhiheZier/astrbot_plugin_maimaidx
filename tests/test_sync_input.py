import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import zxingcpp
from PIL import Image
import astrbot.api.message_components as Comp

from ..command.mai_sync import sync_command_handler, sync_credential_handler, sync_input_handler
from ..libraries import maimaidx_sync_input as inputs
from ..libraries.maimaidx_official import OfficialError
from ..libraries.maimaidx_sync import ScoreSync, snapshot_from_official
from ..main import MaimaiDXPlugin
from .test_official_sync import data, event, sgid


def qr_image(value):
    bitmap = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, value, width=400, height=400)
    return Image.fromarray(np.asarray(bitmap))


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


class QRInputTest(unittest.IsolatedAsyncioTestCase):
    def test_real_qr_roundtrip_and_official_link_not_fetched(self):
        value = sgid()
        for payload in (value, 'https://wq.wahlap.net/qrcode/req/' + value[4:] + '.html?foo=bar'):
            self.assertEqual(inputs.decode_qr(png(qr_image(payload))), value)
        for payload in ('https://example.com/' + value,
                        'https://wq.wahlap.net.evil/qrcode/req/' + value[4:] + '.html',
                        'prefix ' + value, value + ' extra'):
            self.assertIsNone(inputs.credential_text(payload))

    def test_rejects_invalid_images_multiple_players_and_non_player_qr(self):
        left = qr_image(sgid())
        right = qr_image(sgid()[:-1] + 'B')
        combined = Image.new('L', (900, 450), 255)
        combined.paste(left, (0, 0))
        combined.paste(right, (450, 0))
        with self.assertRaisesRegex(OfficialError, '多个'):
            inputs.decode_qr(png(combined))
        for content in (b'not an image', png(qr_image('https://example.com/'))):
            with self.assertRaises(OfficialError) as caught:
                inputs.decode_qr(content)
            self.assertNotIn('example.com', str(caught.exception))

    def test_pixel_limit(self):
        with patch.object(inputs, 'MAX_IMAGE_PIXELS', 100):
            with self.assertRaisesRegex(OfficialError, '像素'):
                inputs.decode_qr(png(Image.new('L', (20, 20))))

    def test_missing_decoder_has_actionable_safe_error(self):
        with patch.dict('sys.modules', {'zxingcpp': None}):
            with self.assertRaisesRegex(OfficialError, 'requirements.txt'):
                inputs.decode_qr(b'ignored')

    async def test_download_bounds_redirect_rejection_and_safe_errors(self):
        for status, length, chunks, expected in (
            (200, 2, [b'ok'], None), (302, 0, [], '下载失败'),
            (200, 20, [], '过大'), (200, None, [b'123456', b'78901'], '过大'),
        ):
            async def stream(_):
                for chunk in chunks:
                    yield chunk

            response = SimpleNamespace(status=status, content_length=length,
                                       content=SimpleNamespace(iter_chunked=stream))
            request = MagicMock()
            request.__aenter__ = AsyncMock(return_value=response)
            session = MagicMock()
            session.get.return_value = request
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=session)
            with (patch.object(inputs.aiohttp, 'ClientSession', return_value=context),
                  patch.object(inputs, 'MAX_IMAGE_BYTES', 10)):
                image = Comp.Image.fromURL('https://image.example/private-token')
                if expected:
                    with self.assertRaisesRegex(OfficialError, expected) as caught:
                        await inputs.read_credential_image(image)
                    self.assertNotIn('private-token', str(caught.exception))
                else:
                    self.assertEqual(await inputs.read_credential_image(image), b'ok')
                session.get.assert_called_once_with(image.file, allow_redirects=False)

    async def test_bounded_base64_and_local_reads(self):
        raw = png(qr_image(sgid()))
        self.assertEqual(await inputs.read_credential_image(Comp.Image.fromBytes(raw)), raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'qr.png'
            path.write_bytes(raw)
            self.assertEqual(await inputs.read_credential_image(Comp.Image.fromFileSystem(str(path))), raw)
        with patch.object(inputs, 'MAX_IMAGE_BYTES', 10):
            with self.assertRaisesRegex(OfficialError, '过大'):
                await inputs.read_credential_image(Comp.Image.fromBytes(raw))
        with self.assertRaisesRegex(OfficialError, '读取'):
            await inputs.read_credential_image(Comp.Image(file='base64://!invalid'))


class SyncInputFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = ScoreSync(Path(self.directory.name), {'official_sync_enabled': True})
        self.service.client.check_available = AsyncMock()
        self.service.sync = AsyncMock(return_value=(snapshot_from_official(data()), ['水鱼：上传成功，共上传 1 条成绩。', '落雪：上传失败']))
        self.plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        self.plugin.score_sync = self.service
        self.plugin._is_group_enabled = lambda _: True

    async def test_all_aliases_prompt_for_text_or_qr(self):
        for command in ('导', '/导', '更新b50', '更新B50', '更新乐曲数据', '同步成绩', '更新成绩'):
            request = event(command)
            messages = [m async for m in sync_command_handler(request, self.service)]
            self.assertIn('SGID', messages[0])
            self.assertIn('二维码图片', messages[0])
            self.assertIn('更新b50 SGID', messages[0])
            self.assertIn('更新b50 官方二维码链接', messages[0])
            self.assertTrue(self.service.waiting(request))
            request.bot.delete_msg.assert_not_awaited()

    async def test_inline_text_alias_only_recalled_in_group_then_result(self):
        for group in (None, '10'):
            value = sgid()
            request = event('更新B50 ' + value, group=group)
            messages = [m async for m in sync_command_handler(request, self.service)]
            if group:
                request.bot.delete_msg.assert_awaited_once_with(message_id=42)
            else:
                request.bot.delete_msg.assert_not_awaited()
                self.assertNotIn('撤回', str(messages))
            self.assertIn('正在获取', messages[0])
            self.assertNotIn('PC 合计', messages[-1])
            self.assertNotIn('张谱面', messages[-1])
            self.assertIn('共上传 1 条成绩', messages[-1])
            self.assertIn('水鱼：上传成功', messages[-1])
            self.assertIn('落雪：上传失败', messages[-1])
            self.assertNotIn(value, str(messages))
            self.service.sync.assert_awaited_with(request, value)

    async def test_pending_image_dispatch_recall_and_result(self):
        request = event(group='10')
        request.message_obj.message = [Comp.Image.fromBytes(png(qr_image(sgid())))]
        await self.service.begin(request)
        messages = [m async for m in self.plugin.official_score_input(request)]
        request.bot.delete_msg.assert_awaited_once()
        self.service.sync.assert_awaited_once()
        self.assertIn('已获取并保存', messages[-1])

    async def test_recall_failure_or_unsupported_platform_warns_but_continues(self):
        for unsupported in (False, True):
            request = event(sgid(), group='10')
            if unsupported:
                request.get_platform_name = lambda: 'other'
            else:
                request.bot.delete_msg.side_effect = RuntimeError('sensitive!')
            messages = [m async for m in sync_input_handler(request, self.service, sgid())]
            self.assertIn('手动撤回', messages[0])
            self.assertIn('已获取并保存', messages[-1])
            self.assertNotIn('sensitive!', str(messages))

    async def test_bad_image_keeps_waiting_and_does_not_sync(self):
        request = event(group='10')
        request.message_obj.message = [Comp.Image.fromBytes(b'bad')]
        await self.service.begin(request)
        messages = [m async for m in self.plugin.official_score_input(request)]
        self.assertIn('无法识别', messages[-1])
        self.assertTrue(self.service.waiting(request))
        self.service.sync.assert_not_awaited()
        request.bot.delete_msg.assert_awaited_once()

    async def test_private_images_and_direct_text_never_recall_or_warn(self):
        request = event()
        request.bot.delete_msg.side_effect = RuntimeError('must not be called')
        request.message_obj.message = [Comp.Image.fromBytes(png(qr_image(sgid())))]
        await self.service.begin(request)
        messages = [m async for m in self.plugin.official_score_input(request)]
        self.assertNotIn('撤回', str(messages))
        direct = [m async for m in sync_credential_handler(request, self.service, sgid())]
        self.assertNotIn('撤回', str(direct))
        request.bot.delete_msg.assert_not_awaited()

    async def test_official_link_text_and_inline_link_use_path_credential_only(self):
        value = sgid()
        link = ('https://wq.wahlap.net/qrcode/req/' + value[4:] + '.html'
                '?l=1794328307&t=E8889EE8908C4458&d=E68A8AE4B88B')
        for inline in (False, True):
            request = event('更新b50 ' + link if inline else link, group='10')
            await self.service.begin(request)
            route = self.plugin.sync_official_scores if inline else self.plugin.official_score_input
            messages = [m async for m in route(request)]
            self.service.sync.assert_awaited_with(request, value)
            request.bot.delete_msg.assert_awaited_once_with(message_id=42)
            self.assertIn('共上传 1 条成绩', messages[-1])
            self.assertNotIn(value, str(messages))
            self.assertNotIn(link, str(messages))

    async def test_command_with_image_is_not_double_dispatched(self):
        request = event('导')
        request.message_obj.message.append(Comp.Image.fromBytes(png(qr_image(sgid()))))
        await self.service.begin(request)
        self.assertEqual([m async for m in self.plugin.official_score_input(request)], [])
        messages = [m async for m in sync_command_handler(request, self.service)]
        self.assertIn('已获取并保存', messages[-1])
        self.service.sync.assert_awaited_once()

    async def test_other_conversations_and_unrelated_text_ignored(self):
        await self.service.begin(event(group='10'))
        for request in (event(sgid(), group='20'), event(sgid(), group='10', user='other'),
                        event('聊天内容', group='10'), event(sgid())):
            self.assertEqual([m async for m in self.plugin.official_score_input(request)], [])
            request.bot.delete_msg.assert_not_awaited()
        self.service.sync.assert_not_awaited()

    async def test_cancel_during_image_read_prevents_fetch(self):
        request = event()
        request.message_obj.message = [Comp.Image.fromBytes(b'ignored')]
        await self.service.begin(request)

        async def cancel(_):
            self.service.pending.clear()
            return b'image'

        with (patch('data.plugins.astrbot_plugin_maimaidx.command.mai_sync.read_credential_image', side_effect=cancel),
              patch('data.plugins.astrbot_plugin_maimaidx.command.mai_sync.decode_qr', return_value=sgid())):
            messages = [m async for m in self.plugin.official_score_input(request)]
        self.assertIn('取消或过期', messages[-1])
        self.service.sync.assert_not_awaited()

    async def test_multiple_images_and_busy_input_do_not_sync(self):
        request = event()
        request.message_obj.message = [Comp.Image.fromBytes(b'1'), Comp.Image.fromBytes(b'2')]
        messages = [m async for m in sync_input_handler(request, self.service)]
        self.assertIn('只发送一个', messages[-1])
        async with self.service.input_lock(self.service.identity(request)):
            messages = [m async for m in sync_input_handler(request, self.service, sgid())]
        self.assertIn('勿重复', messages[-1])
        self.service.sync.assert_not_awaited()
