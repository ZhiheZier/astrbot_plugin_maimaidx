import asyncio
import inspect
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ..libraries.maimaidx_activity import ActivityLog, usage_logged
from ..libraries.maimaidx_management_store import ManagementError
from ..main import MaimaiDXPlugin
from .test_official_sync import event


class ActivityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ActivityLog(Path(directory.name) / 'activity.json')
        await self.store.load()
        self.plugin = SimpleNamespace(activity=self.store)

    async def asyncTearDown(self):
        await self.store.shutdown()

    async def test_response_logs_metadata_only_and_persists_after_restart(self):
        @usage_logged
        async def auth_code(plugin, request):
            """完成授权"""
            yield 'secret-response-token'

        request = event('SGWCMAID-secret-input', group='200')
        self.assertEqual([result async for result in auth_code(self.plugin, request)], ['secret-response-token'])
        rows = self.store.listing({})['items']
        self.assertEqual(rows[0]['actor'], '123456')
        self.assertEqual(rows[0]['group'], '200')
        self.assertEqual(rows[0]['status'], 'responded')
        await self.store.shutdown()
        self.assertNotIn('secret', self.store.path.read_text(encoding='utf-8'))
        restored = ActivityLog(self.store.path)
        await asyncio.gather(restored.load(), restored.load())
        self.assertEqual(restored.listing({})['total'], 1)
        self.assertTrue(inspect.isasyncgenfunction(auth_code))
        self.assertEqual(list(inspect.signature(auth_code).parameters), ['plugin', 'request'])

    async def test_quiet_listener_records_nothing_and_error_does_not_leak(self):
        @usage_logged
        async def listener(plugin, request):
            if False:
                yield None

        @usage_logged
        async def broken(plugin, request):
            raise ValueError('SGID-secret-response')
            yield None

        self.assertEqual([row async for row in listener(self.plugin, event())], [])
        self.assertEqual(self.store.listing({})['total'], 0)
        with self.assertRaises(ValueError):
            await anext(broken(self.plugin, event()))
        row = self.store.listing({})['items'][0]
        self.assertEqual(row['status'], 'error')
        self.assertNotIn('secret', json.dumps(row))

    async def test_generator_close_releases_inner_context_and_records_interruption(self):
        closed = Mock()

        @usage_logged
        async def waiting(plugin, request):
            try:
                yield 'progress'
                await asyncio.sleep(60)
            finally:
                closed()

        stream = waiting(self.plugin, event())
        await anext(stream)
        await stream.aclose()
        closed.assert_called_once()
        self.assertEqual(self.store.listing({})['items'][0]['status'], 'cancelled')

    async def test_capacity_retention_filters_and_readonly_listing(self):
        self.store.capacity = 35
        for index in range(50):
            self.store.record('chat' if index % 2 else 'web', 'best50', 'B50', 'responded')
        self.store._rows[0]['at'] = time.time() - 8 * 86400
        self.assertEqual(self.store.listing({})['total'], 34)
        self.assertEqual(len(self.store.listing({'page': '2'})['items']), 4)
        self.assertTrue(all(row['scope'] == 'chat' for row in self.store.listing({'scope': 'chat'})['items']))
        with self.assertRaises(ManagementError):
            self.store.listing({'scope': '../../secrets'})
        self.store.record('chat', 'best50', 'B50', 'responded', actor='secret-input')
        self.assertEqual(self.store.listing({})['total'], 34)

    async def test_corrupt_file_is_not_overwritten_and_write_error_never_breaks_calls(self):
        await self.store.shutdown()
        self.store.path.write_text('{broken', encoding='utf-8')
        broken = ActivityLog(self.store.path)
        await broken.load()
        broken.record('web', 'change_group', '修改群功能', 'handled')
        await broken.shutdown()
        self.assertEqual(self.store.path.read_text(encoding='utf-8'), '{broken')
        self.assertFalse(broken.listing({})['persistent'])

    async def test_resource_callbacks_are_threadsafe_and_ignore_raw_messages(self):
        await asyncio.to_thread(self.store.resource_phase, 'completed')
        await asyncio.sleep(0)
        self.assertEqual(self.store.listing({'scope': 'resource'})['items'][0]['label'], '资源任务完成')

    async def test_web_log_reads_require_auth_and_do_not_log_themselves_or_error_bodies(self):
        from ..libraries import maimaidx_resource_web as web
        from ..libraries.maimaidx_resource_archive import ResourceError

        api = web.ResourceWeb(Mock(), Mock(), SimpleNamespace(plugin=self.plugin))
        request = SimpleNamespace(username=None, query={})
        with patch.object(web, 'request', request):
            self.assertEqual((await api.activity()).status_code, 401)
            request.username = 'private-admin-name'
            self.assertEqual((await api.activity()).status_code, 200)
            self.assertEqual(self.store.listing({})['total'], 0)
            audit = {'feature': 'manage_players', 'label': '维护玩家设置'}
            await api._execute(AsyncMock(return_value={'secret': 'private-response'}), audit)
            await api._execute(AsyncMock(side_effect=ResourceError('private-error-secret')), audit)
        payload = json.dumps(self.store.listing({}), ensure_ascii=False)
        self.assertNotIn('private-', payload)
        self.assertEqual({row['status'] for row in self.store.listing({})['items']}, {'handled', 'error'})

    async def test_disk_write_failure_keeps_memory_and_does_not_raise_to_commands(self):
        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_activity.write_json', side_effect=OSError('private-path')):
            self.store.record('chat', 'best50', 'B50', 'responded')
            await self.store.shutdown()
        self.assertFalse(self.store.listing({})['persistent'])
        self.assertEqual(self.store.listing({})['total'], 1)

    def test_all_registered_message_methods_are_wrapped(self):
        from astrbot.core.star.star_handler import star_handlers_registry, EventType

        handlers = [item for item in star_handlers_registry if item.handler_module_path == MaimaiDXPlugin.__module__
                    and item.event_type == EventType.AdapterMessageEvent]
        self.assertGreater(len(handlers), 60)
        for handler in handlers:
            self.assertTrue(hasattr(handler.handler, '__wrapped__'), handler.handler_name)
