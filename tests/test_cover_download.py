import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from ..libraries import image
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_music import Guess


class CoverDownloadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.coverdir = Path(directory)
        self.stack.enter_context(patch.object(image, 'coverdir', self.coverdir))
        self.stack.enter_context(patch.object(image, '_missing_covers', set()))
        self.stack.enter_context(patch.object(image, '_cover_locks', {}))
        self.stack.enter_context(patch.object(maiApi.config, 'assets_online', True))
        output = BytesIO()
        Image.new('RGB', (8, 8), 'red').save(output, 'PNG')
        self.content = output.getvalue()

        self.response = MagicMock(status=200)
        self.response.read = AsyncMock(return_value=self.content)
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=self.response)
        self.session = MagicMock()
        self.session.get.return_value = request
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=self.session)
        self.client = self.stack.enter_context(
            patch.object(image.aiohttp, 'ClientSession', return_value=client)
        )

    async def test_downloads_and_caches_shared_standard_dx_cover(self):
        result = await image.music_picture('11820')
        self.assertEqual(result, self.coverdir / '1820.png')
        self.assertEqual(result.read_bytes(), self.content)
        self.assertEqual(await image.music_picture(1820), result)
        self.client.assert_called_once()
        self.assertEqual(
            self.session.get.call_args.args[0], f'{maiApi.MaiCover}/1820.png',
        )

    async def test_exact_local_cover_takes_priority_over_shared_cover(self):
        exact = self.coverdir / '11820.png'
        exact.write_bytes(self.content)
        (self.coverdir / '1820.png').write_bytes(self.content)
        self.assertEqual(await image.music_picture(11820), exact)
        self.client.assert_not_called()

    async def test_offline_mode_uses_placeholder_without_network(self):
        with patch.object(maiApi.config, 'assets_online', False):
            path = await image.music_picture(11820)
        self.assertEqual(path, self.coverdir / '0.png')
        with Image.open(path) as placeholder:
            self.assertEqual(placeholder.size, (200, 200))
        self.client.assert_not_called()

    async def test_http_failure_uses_existing_placeholder_and_is_not_repeated(self):
        fallback = self.coverdir / '0.png'
        fallback.write_bytes(self.content)
        self.response.status = 404
        self.assertEqual(await image.music_picture(11820), fallback)
        self.assertEqual(await image.music_picture(1820), fallback)
        self.client.assert_called_once()
        self.response.read.assert_not_awaited()

    async def test_timeout_and_empty_response_fall_back_without_partial_cache(self):
        self.response.read.side_effect = asyncio.TimeoutError()
        self.assertEqual(await image.music_picture(11820), self.coverdir / '0.png')
        self.assertFalse((self.coverdir / '1820.png').exists())
        self.response.read.side_effect = None
        self.response.read.return_value = b''
        self.assertEqual(await image.music_picture(11821), self.coverdir / '0.png')
        self.assertFalse((self.coverdir / '1821.png').exists())

    async def test_waiting_for_network_does_not_block_other_tasks(self):
        reading = asyncio.Event()
        release = asyncio.Event()

        async def slow_read():
            reading.set()
            await release.wait()
            return self.content

        self.response.read.side_effect = slow_read
        download = asyncio.create_task(image.music_picture(11820))
        try:
            await asyncio.wait_for(reading.wait(), timeout=1)
            heartbeat = AsyncMock()
            await asyncio.create_task(heartbeat())
            heartbeat.assert_awaited_once()
            self.assertFalse(download.done())
        finally:
            release.set()
            await download

    async def test_concurrent_queries_download_shared_cover_once(self):
        reading = asyncio.Event()
        release = asyncio.Event()

        async def slow_read():
            reading.set()
            await release.wait()
            return self.content

        self.response.read.side_effect = slow_read
        first = asyncio.create_task(image.music_picture(11820))
        second = asyncio.create_task(image.music_picture(1820))
        try:
            await asyncio.wait_for(reading.wait(), timeout=1)
            await asyncio.sleep(0)
        finally:
            release.set()
            results = await asyncio.gather(first, second)
        self.assertEqual(results, [self.coverdir / '1820.png'] * 2)
        self.client.assert_called_once()


class GuessCoverLoadingTest(unittest.IsolatedAsyncioTestCase):
    async def test_cover_download_prevents_duplicate_guess_games(self):
        game = Guess.__new__(Guess)
        game.Group = {}
        game.starting = set()
        game.switch = SimpleNamespace(enable=['10001'])
        loading = asyncio.Event()
        release = asyncio.Event()
        data = object()

        async def load_data():
            loading.set()
            await release.wait()
            return data

        game.guessData = load_data
        task = asyncio.create_task(game.start('10001'))
        try:
            await asyncio.wait_for(loading.wait(), timeout=1)
            with self.assertRaisesRegex(ValueError, '已有正在进行'):
                await game.startpic('10001')
        finally:
            release.set()
            await task
        self.assertIs(game.Group['10001'], data)
        self.assertFalse(game.starting)

    async def test_disabling_guess_while_cover_loads_does_not_create_game(self):
        game = Guess.__new__(Guess)
        game.Group = {}
        game.starting = set()
        game.switch = SimpleNamespace(enable=['10001'])

        async def load_data():
            game.switch.enable.clear()
            return object()

        game.guesspicdata = load_data
        with self.assertRaisesRegex(ValueError, '已关闭猜歌功能'):
            await game.startpic('10001')
        self.assertFalse(game.Group)
        self.assertFalse(game.starting)
