import asyncio
import io
import json
import os
import stat
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import py7zr
from PIL import Image, ImageFont

from ..libraries import maimaidx_resource_archive as archive
from ..libraries import maimaidx_resource_manager as manager
from ..libraries import maimaidx_resources as resources
from ..main import MaimaiDXPlugin


def png():
    output = io.BytesIO()
    Image.new('RGB', (12, 12), '#137b83').save(output, 'PNG')
    return output.getvalue()


def zip_bytes(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as bundle:
        for name, data in entries or [('static/mai/pic/test.png', png())]:
            bundle.writestr(name, data)
    return output.getvalue()


class Upload:
    def __init__(self, data=None, filename='resources.zip'):
        self.filename = filename
        self.stream = io.BytesIO(zip_bytes() if data is None else data)
        self.closed = False

    async def read(self, size):
        return self.stream.read(size)

    async def close(self):
        self.closed = True
        self.stream.close()


def response(status=200, content=b'archive', headers=None, size=None):
    async def chunks(_):
        yield content

    result = MagicMock()
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    result.status = status
    result.headers = headers or {}
    result.content_length = len(content) if size is None else size
    result.content.iter_chunked = chunks
    return result


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.bundle = self.root / 'pack.bin'
        self.stop = threading.Event()

    def extract(self, data):
        self.bundle.write_bytes(data)
        return archive.extract_assets(self.bundle, self.stage, self.stop, Mock())

    def test_path_mapping_and_ignored_user_files(self):
        for name, expected in (
            ('wrapper/static/mai/pic/PRiSM/title.png', 'mai/pic/PRiSM/title.png'),
            ('wrapper/mai/cover/123.png', 'mai/cover/123.png'),
            ('pic/test.png', 'mai/pic/test.png'), ('test.png', 'mai/pic/test.png'),
            ('wrapper/pic/test.png', 'mai/pic/test.png'),
            ('Resource CN1.56 UPDATE/UI_NUM_Drating_0.png', 'mai/pic/UI_NUM_Drating_0.png'),
            ('font/test.otf', 'font/test.otf'), ('test.ttf', 'font/test.ttf'),
            ('static/data/user_data.json', None), ('main.py', None), ('index.html', None),
        ):
            actual = archive.asset_path(name)
            self.assertEqual(actual.as_posix() if actual else None, expected)

    def test_unsafe_paths_rejected(self):
        for name in ('../x.png', '/tmp/x.png', 'C:\\x.png', 'mai/pic/../../x.png',
                     'mai\\pic\\..\\x.png', 'mai/pic/x.png:stream', 'mai/pic/CON.png',
                     'mai/pic/x. /x.png', 'mai//pic/x.png', '\\\\server\\x.png'):
            with self.subTest(name=name), self.assertRaises(archive.ResourceError):
                archive.asset_path(name)

    def test_zip_validates_images_and_skips_configuration(self):
        files = self.extract(zip_bytes([('static/mai/pic/test.png', png()),
                                        ('static/data/user_data.json', b'{}'), ('main.py', b'bad')]))
        self.assertEqual(files, [Path('mai/pic/test.png')])
        self.assertFalse((self.stage / 'data').exists())

    def test_7z_extracts_nested_images(self):
        source = self.root / 'source.png'
        source.write_bytes(png())
        with py7zr.SevenZipFile(self.bundle, 'w') as bundle:
            bundle.write(source, arcname='wrapper/static/mai/pic/test.png')
            bundle.writestr('{}', 'static/data/user_data.json')
        files = archive.extract_assets(self.bundle, self.stage, self.stop, Mock())
        self.assertEqual(files, [Path('mai/pic/test.png')])
        self.assertEqual((self.stage / files[0]).read_bytes(), png())

    def test_duplicate_case_insensitive_paths_rejected(self):
        with self.assertRaises(archive.ResourceError):
            self.extract(zip_bytes([('mai/pic/test.png', png()), ('pic/TEST.png', png())]))

    def test_invalid_image_or_no_assets_rejected(self):
        for data in (zip_bytes([('test.png', b'invalid')]), zip_bytes([('config.json', b'{}')]), b'html'):
            with self.assertRaises(archive.ResourceError):
                self.extract(data)

    def test_symlink_zip_rejected(self):
        output = io.BytesIO()
        entry = zipfile.ZipInfo('mai/pic/test.png')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(output, 'w') as bundle:
            bundle.writestr(entry, '../target')
        with self.assertRaises(archive.ResourceError):
            self.extract(output.getvalue())

    def test_size_count_and_cancellation_limits(self):
        for field, limit in (('MAX_FILE', 1), ('MAX_EXTRACTED', 1), ('MAX_FILES', 0)):
            with patch.object(archive, field, limit), self.assertRaises(archive.ResourceError):
                self.extract(zip_bytes())
        self.stop.set()
        with self.assertRaises(archive.ResourceCancelled):
            self.extract(zip_bytes())

    def test_install_rolls_back_previous_and_new_files(self):
        static, backup = self.root / 'static', self.root / 'backup'
        static.mkdir()
        files = [Path('a.png'), Path('b.png'), Path('c.png')]
        (static / 'a.png').write_bytes(b'old')
        for file in files:
            (self.stage / file).write_bytes(b'new')
        replace = os.replace

        def fail_last(source, target):
            if source == self.stage / 'c.png':
                raise OSError('simulated disk failure')
            return replace(source, target)

        with patch.object(archive.os, 'replace', side_effect=fail_last), self.assertRaises(OSError):
            archive.install_assets(self.stage, static, backup, files, Mock())
        self.assertEqual((static / 'a.png').read_bytes(), b'old')
        self.assertFalse((static / 'b.png').exists())
        self.assertFalse((static / 'c.png').exists())

    def test_install_rejects_linked_directory(self):
        static, outside = self.root / 'static', self.root / 'outside'
        static.mkdir()
        outside.mkdir()
        try:
            (static / 'pic').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symbolic links require OS privileges')
        with self.assertRaises(archive.ResourceError):
            archive.install_assets(self.stage, static, self.root / 'backup', [Path('pic/test.png')], Mock())
        self.assertFalse((outside / 'test.png').exists())

    def test_inventory_checks_fonts_images_and_theme(self):
        initial = resources.scan_resources(self.root)
        self.assertFalse(initial['ready'])
        pic = self.root / 'static/mai/pic'
        font_dir = self.root / 'static/font'
        pic.mkdir(parents=True)
        font_dir.mkdir()
        for name in (*resources.CORE_PICTURES, 'title.png', 'title_lengthen.png', 'design.png'):
            (pic / name).write_bytes(png())
        font = Path(ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 12).path) if os.name == 'nt' else None
        if font is None:
            for candidate in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'):
                if Path(candidate).is_file():
                    font = Path(candidate)
                    break
        if font is None:
            self.skipTest('no system test font')
        for name in resources.CORE_FONTS:
            (font_dir / name).write_bytes(font.read_bytes())
        self.assertTrue(resources.scan_resources(self.root)['ready'])
        (pic / resources.CORE_PICTURES[0]).write_bytes(b'broken')
        self.assertFalse(resources.scan_resources(self.root)['ready'])


class ResourceManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.callback = AsyncMock()
        self.manager = manager.ResourceManager(self.root, self.callback, AsyncMock())

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temp.cleanup()

    async def test_upload_installs_and_reloads_without_importing_user_data(self):
        upload = Upload(zip_bytes([('static/mai/pic/test.png', png()), ('static/data/user_data.json', b'{}')]))
        await self.manager.upload(upload)
        await self.manager._task
        self.assertTrue(upload.closed)
        self.assertEqual(self.manager.job['phase'], 'completed')
        self.callback.assert_awaited_once()
        self.assertEqual((self.root / 'static/mai/pic/test.png').read_bytes(), png())
        self.assertFalse((self.root / 'static/data').exists())
        self.assertEqual(list((self.root / 'resource_work').iterdir()), [])

    async def test_install_releases_cache_before_replacing_and_restores_after_failure(self):
        order = []

        async def prepare():
            order.append('prepare')

        async def reload():
            self.assertFalse(self.manager.render_available)
            order.append('reload')

        def install(*args):
            order.append('install')
            raise OSError('failed')

        self.manager.prepare = prepare
        self.manager.on_installed = reload
        with patch.object(manager, 'install_assets', side_effect=install):
            await self.manager.upload(Upload())
            await self.manager._task
        self.assertEqual(order, ['prepare', 'install', 'reload'])
        self.assertEqual(self.manager.job['phase'], 'failed')

    async def test_background_draw_falls_back_to_text_while_installing(self):
        music = SimpleNamespace(song_id=123, song_name='test')
        draw = AsyncMock(return_value='image')
        fallback = await resources.background_music_info(music, draw, self.manager)
        self.assertIn('123', str(fallback))
        draw.assert_not_awaited()
        self.manager.inventory['ready'] = True
        self.assertEqual(await resources.background_music_info(music, draw, self.manager), 'image')
        self.assertEqual(self.manager.active_draws, 0)

    async def test_resource_cache_closes_shared_images_once(self):
        from ..libraries.maimai_best_50 import ScoreBaseImage

        image = Mock()
        with patch.multiple(ScoreBaseImage, _diff=[image], _rise=[], _themed_images={('theme', 'title'): image},
                            title_bg=image):
            MaimaiDXPlugin._release_resource_images()
            self.assertEqual(ScoreBaseImage._diff, [])
            self.assertEqual(ScoreBaseImage._themed_images, {})
            self.assertIsNone(ScoreBaseImage.title_bg)
        image.close.assert_called_once()

    async def test_invalid_upload_and_duplicate_job_close_uploads(self):
        upload = Upload(filename='resource.exe')
        with self.assertRaises(archive.ResourceError):
            await self.manager.upload(upload)
        self.assertTrue(upload.closed)
        self.assertEqual(self.manager.job['phase'], 'failed')
        self.manager._begin('downloading', 'existing')
        upload = Upload()
        with self.assertRaises(archive.ResourceError):
            await self.manager.upload(upload)
        self.assertTrue(upload.closed)
        self.assertEqual(self.manager.job['title'], 'existing')

    async def test_upload_limit_and_invalid_archive_do_not_replace_existing(self):
        with patch.object(manager, 'MAX_UPLOAD', 1), self.assertRaises(archive.ResourceError):
            await self.manager.upload(Upload())
        await self.manager.upload(Upload(b'not archive'))
        await self.manager._task
        self.assertEqual(self.manager.job['phase'], 'failed')
        self.assertFalse((self.root / 'static').exists())

    async def test_cancel_upload_reports_cancelled_and_closes_file(self):
        upload = Upload()
        read = upload.read

        async def cancel_during_read(size):
            data = await read(size)
            self.manager.cancel()
            return data

        upload.read = cancel_during_read
        with self.assertRaises(archive.ResourceCancelled):
            await self.manager.upload(upload)
        self.assertEqual(self.manager.job['phase'], 'cancelled')
        self.assertTrue(upload.closed)
        self.assertFalse((self.root / 'static').exists())

    async def test_download_cancel_failure_and_fixed_sources(self):
        with self.assertRaises(archive.ResourceError):
            self.manager.start_download('https://127.0.0.1/evil')
        self.assertFalse(self.manager.busy)

        async def download(source, destination):
            destination.write_bytes(zip_bytes())
            self.manager.cancel()

        self.manager._download = download
        self.manager.start_download('base')
        await self.manager._task
        self.assertEqual(self.manager.job['phase'], 'cancelled')
        self.assertFalse((self.root / 'static').exists())

    async def test_install_cannot_cancel_and_rescan_reloads(self):
        self.manager._begin('installing', 'install')
        with self.assertRaises(archive.ResourceError):
            self.manager.cancel()
        self.manager.progress('completed', 'done')
        await self.manager.rescan()
        self.callback.assert_awaited_once()
        self.assertEqual(self.manager.job['phase'], 'completed')

    async def test_rebuild_requires_resources_and_waits_for_draws(self):
        with self.assertRaises(archive.ResourceError):
            self.manager.start_rebuild()
        self.manager.inventory['ready'] = True
        self.manager.active_draws = 1
        self.manager.start_rebuild()
        await asyncio.sleep(0.01)
        self.manager.rebuild.assert_not_awaited()
        self.manager.active_draws = 0
        await self.manager._task
        self.manager.rebuild.assert_awaited_once()

    async def test_private_url_and_dns_rejected(self):
        for url in ('http://example.com/a', 'https://127.0.0.1/a', 'https://[::1]/a',
                    'https://10.0.0.1/a', 'https://169.254.169.254/a', 'https://u:p@example.com/a',
                    'https://example.com:8080/a'):
            with self.subTest(url=url), self.assertRaises(archive.ResourceError):
                manager.validate_download_url(url)
        manager.validate_download_url('https://cloud.yuzuchan.moe/f/test')
        resolver = manager.PublicResolver()
        with patch.object(manager.DefaultResolver, 'resolve', AsyncMock(return_value=[{'host': '127.0.0.1'}])):
            with self.assertRaises(archive.ResourceError):
                await resolver.resolve('private.example', 443)
        await resolver.close()

    async def test_download_streams_records_digest_and_keeps_tls_verification(self):
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get.return_value = response(content=b'download fixture')
        target = self.root / 'download.bin'
        with patch.object(manager.aiohttp, 'TCPConnector') as connector, patch.object(
                manager.aiohttp, 'ClientSession', return_value=session) as factory:
            await self.manager._download({'url': 'https://example.com/a.7z'}, target)
        self.assertEqual(target.read_bytes(), b'download fixture')
        self.assertEqual(len(self.manager.job['sha256']), 64)
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertNotIn('ssl', connector.call_args.kwargs)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])

    async def test_download_rejects_private_redirect_and_oversized_response(self):
        for result in (response(302, headers={'Location': 'https://127.0.0.1/private'}),
                       response(size=manager.MAX_ARCHIVE + 1)):
            session = MagicMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            session.get.return_value = result
            with patch.object(manager.aiohttp, 'TCPConnector'), patch.object(
                    manager.aiohttp, 'ClientSession', return_value=session), self.assertRaises(archive.ResourceError):
                await self.manager._download({'url': 'https://example.com/a.7z'}, self.root / 'download.bin')
            self.assertEqual(session.get.call_count, 1)
        self.assertFalse((self.root / 'download.bin').exists())

    async def test_missing_resources_does_not_skip_initialization(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.resources = self.manager
        for name in ('_load_disabled_groups', '_load_arcade_enabled_groups', '_setup_configuration',
                     '_perform_initial_checks', '_setup_schedulers', '_start_update_notice'):
            setattr(plugin, name, Mock())
        plugin._load_maimai_data = AsyncMock(side_effect=OSError('offline'))
        plugin._load_arcade_data = AsyncMock()
        plugin._reload_resources = AsyncMock()
        await plugin.initialize()
        plugin._load_arcade_data.assert_awaited_once()
        plugin._reload_resources.assert_awaited_once()
        plugin._setup_schedulers.assert_called_once()
        plugin._start_update_notice.assert_called_once()

    async def test_drawing_guard_and_disabled_group(self):
        plugin = SimpleNamespace(resources=self.manager, _is_group_enabled=lambda _: True)
        event = SimpleNamespace(message_obj=SimpleNamespace(group_id='200'), plain_result=lambda text: text)
        called = Mock()

        @resources.requires_resources
        async def drawing(self, event):
            called()
            yield 'image'

        self.assertEqual([result async for result in drawing(plugin, event)], [resources.RESOURCE_NOTICE])
        called.assert_not_called()
        plugin._is_group_enabled = lambda _: False
        self.assertEqual([result async for result in drawing(plugin, event)], [])
        plugin._is_group_enabled = lambda _: True
        self.manager.inventory['ready'] = True
        self.assertEqual([result async for result in drawing(plugin, event)], ['image'])
        self.assertEqual(self.manager.active_draws, 0)

    async def test_arcade_help_falls_back_to_text_without_resources(self):
        from ..command.mai_arcade import dx_arcade_help_handler, sv_help

        event = SimpleNamespace(plain_result=lambda text: text)
        self.assertEqual([result async for result in dx_arcade_help_handler(event, self.manager)], [sv_help])


class ResourceWebTest(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_endpoints_and_invalid_source(self):
        try:
            from ..libraries import maimaidx_resource_web as web
        except ImportError:
            self.skipTest('AstrBot does not provide the new native Web API')
        with tempfile.TemporaryDirectory() as temp:
            resource_manager = manager.ResourceManager(Path(temp))
            context = Mock()
            endpoint = web.ResourceWeb(context, resource_manager)
            self.assertEqual(context.register_web_api.call_count, 6)
            request = SimpleNamespace(username=None, json=AsyncMock(return_value={}), files=AsyncMock(return_value={}))
            with patch.object(web, 'request', request):
                for name in ('status', 'download', 'upload', 'cancel', 'rescan', 'rebuild'):
                    self.assertEqual((await getattr(endpoint, name)()).status_code, 401)
                request.username = 'admin'
                self.assertEqual((await endpoint.status()).status_code, 200)
                request.json.return_value = {'source_id': 'https://127.0.0.1/a'}
                self.assertEqual((await endpoint.download()).status_code, 409)
                request.json.return_value = []
                self.assertEqual((await endpoint.download()).status_code, 409)
                self.assertEqual((await endpoint.upload()).status_code, 409)
                response = await endpoint.status()
                self.assertFalse(json.loads(response.body)['busy'])


if __name__ == '__main__':
    unittest.main()
