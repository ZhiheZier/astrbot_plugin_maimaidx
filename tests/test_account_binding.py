import asyncio
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ..libraries.maimaidx_account_binding import AccountBindings
from ..libraries.maimaidx_official import OfficialData, OfficialError
from ..libraries.maimaidx_sync import ScoreSync, snapshot_from_official
from .test_official_sync import data, event, sgid


class AccountBindingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = AccountBindings(self.root / 'bindings.json')

    async def test_first_bind_restart_and_same_account_are_idempotent(self):
        await self.store.bind('123456', 12345)
        await AccountBindings(self.store.path).bind('123456', 12345)
        self.assertEqual(await self.store.records(), {'123456': 12345})
        await self.store.verify_snapshot('123456', hashlib.sha256(b'12345').hexdigest())

    async def test_one_to_one_in_both_directions_and_concurrent_claims(self):
        outcomes = await asyncio.gather(self.store.bind('123456', 12345), self.store.bind('654321', 12345), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, OfficialError) for value in outcomes), 1)
        owner = next(iter(await self.store.records()))
        with self.assertRaisesRegex(OfficialError, '不一致'):
            await self.store.bind(owner, 54321)
        self.assertEqual(len(await self.store.records()), 1)

    async def test_unbind_allows_new_claim_but_blocks_old_snapshot_retry(self):
        await self.store.bind('123456', 12345)
        with self.assertRaisesRegex(OfficialError, '变化'):
            await self.store.unbind('123456', 999)
        await self.store.unbind('123456', 12345)
        with self.assertRaisesRegex(OfficialError, '尚未绑定'):
            await self.store.verify_snapshot('123456', hashlib.sha256(b'12345').hexdigest())
        await self.store.bind('123456', 54321)
        with self.assertRaisesRegex(OfficialError, '不一致'):
            await self.store.verify_snapshot('123456', hashlib.sha256(b'12345').hexdigest())

    async def test_failed_write_and_corrupted_file_fail_closed(self):
        await self.store.bind('123456', 12345)
        before = self.store.path.read_bytes()
        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_account_binding.write_json', side_effect=OSError('private-path')):
            with self.assertRaisesRegex(OfficialError, '保存失败') as caught:
                await self.store.unbind('123456', 12345)
        self.assertNotIn('private-path', str(caught.exception))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.path.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(OfficialError, '文件异常'):
            await self.store.bind('654321', 54321)
        self.assertEqual(self.store.path.read_text(encoding='utf-8'), '{broken')

    async def test_duplicate_owners_in_file_and_invalid_claims_are_rejected(self):
        from ..libraries.maimaidx_management_store import write_json

        for qq, uid in (('other', 1), ('123456', True), ('123456', 0), ('001', 1)):
            with self.assertRaises(OfficialError):
                await self.store.bind(qq, uid)
        write_json(self.store.path, {'schema_version': 1, 'bindings': {'123456': 12, '654321': 12}})
        with self.assertRaises(OfficialError):
            await self.store.records()

    def service(self):
        service = ScoreSync(self.root, {'official_sync_enabled': True})
        service.client.check_available = AsyncMock()
        service.client.fetch = AsyncMock(return_value=data())
        return service

    async def test_sync_binds_server_user_id_before_save_and_upload(self):
        service = self.service()

        async def upload(*args, **kwargs):
            self.assertEqual(await service.bindings.records(), {'123456': data().account_id})
            return ['OK']

        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_sync.upload_bound_sources', side_effect=upload):
            await service.sync(event(), sgid())
        self.assertIsNotNone(await service.store.load(service.identity(event())))

    async def test_mismatched_account_keeps_snapshot_and_never_uploads(self):
        service = self.service()
        await service.bindings.bind('123456', 999)
        previous = snapshot_from_official(data())
        await service.store.save(service.identity(event()), previous)
        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_sync.upload_bound_sources', AsyncMock()) as upload:
            with self.assertRaisesRegex(OfficialError, '不一致'):
                await service.sync(event(), sgid())
            upload.assert_not_awaited()
        self.assertEqual(await service.store.load(service.identity(event())), previous)

    async def test_other_qq_cannot_save_or_upload_claimed_account(self):
        service = self.service()
        await service.bindings.bind('654321', data().account_id)
        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_sync.upload_bound_sources', AsyncMock()) as upload:
            with self.assertRaisesRegex(OfficialError, '其他 QQ'):
                await service.sync(event(), sgid())
            upload.assert_not_awaited()
        self.assertIsNone(await service.store.load(service.identity(event())))

    async def test_binding_failure_stops_before_snapshot_or_upload(self):
        service = self.service()
        with patch.object(service.bindings, '_save', AsyncMock(side_effect=OfficialError('保存失败'))), patch.object(service, '_upload', AsyncMock()) as upload:
            with self.assertRaisesRegex(OfficialError, '保存失败'):
                await service.sync(event(), sgid())
        upload.assert_not_awaited()
        self.assertIsNone(await service.store.load(service.identity(event())))

    async def test_invalid_scores_do_not_claim_account(self):
        service = self.service()
        service.client.fetch.return_value = OfficialData(12345, 'invalid', 1, [])
        with self.assertRaises(OfficialError):
            await service.sync(event(), sgid())
        self.assertEqual(await service.bindings.records(), {})

    async def test_qq_lock_serializes_different_bot_scopes(self):
        service = self.service()
        async with service.bindings.account_lock('123456'):
            with self.assertRaisesRegex(OfficialError, '正在同步'):
                await service.sync(event(bot='200'), sgid())
            with self.assertRaisesRegex(OfficialError, '正在同步'):
                await service.retry(event(bot='200'))
        service.client.fetch.assert_not_awaited()

    async def test_retry_requires_binding_and_matching_cached_account(self):
        service = self.service()
        await service.store.save(service.identity(event()), snapshot_from_official(data()))
        with patch.object(service, '_upload', AsyncMock()) as upload:
            with self.assertRaisesRegex(OfficialError, '尚未绑定'):
                await service.retry(event())
            await service.bindings.bind('123456', 999)
            with self.assertRaisesRegex(OfficialError, '不一致'):
                await service.retry(event())
        upload.assert_not_awaited()

    async def test_cancelled_write_finishes_before_another_owner_can_claim(self):
        from ..libraries.maimaidx_management_store import write_json

        entered, release = threading.Event(), threading.Event()

        def blocked_write(*args):
            entered.set()
            release.wait(timeout=3)
            write_json(*args)

        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_account_binding.write_json', side_effect=blocked_write):
            first = asyncio.create_task(self.store.bind('123456', 12345))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                first.cancel()
                second = asyncio.create_task(self.store.bind('654321', 12345))
                await asyncio.sleep(0.01)
                self.assertFalse(second.done())
            finally:
                release.set()
                await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        self.assertTrue(first.cancelled())
        self.assertIsInstance(second.exception(), OfficialError)
        self.assertEqual(await self.store.records(), {'123456': 12345})
