import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ..libraries import maimaidx_sync as sync
from ..libraries import maimaidx_sync_limits as limits
from ..libraries.maimaidx_error import DivingFishOAuthNotBoundError
from ..libraries.maimaidx_official import OfficialError
from .test_official_sync import data, event, sgid


class SyncLimitsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.identity = ('platform', 'bot', 'private-user-id')
        self.budget = limits.SyncLimits(self.root, {})
        self.now = datetime(2026, 11, 11, 12, tzinfo=limits.SHANGHAI).timestamp()

    async def test_check_does_not_consume_and_cooldown_survives_restart(self):
        with patch.object(limits.time, 'time', return_value=self.now):
            await self.budget.check(self.identity, 'fetch')
            await self.budget.check(self.identity, 'fetch')
            self.assertFalse(self.budget.path.exists())
            await self.budget.reserve(self.identity, 'fetch')
            text = self.budget.path.read_text(encoding='utf-8')
            self.assertNotIn('private-user-id', text)
            self.assertNotIn('SGWCMAID', text)
            restored = limits.SyncLimits(self.root, {})
            with self.assertRaisesRegex(limits.SyncLimitError, '300 秒'):
                await restored.check(self.identity, 'fetch')
        with patch.object(limits.time, 'time', return_value=self.now + 300):
            await restored.reserve(self.identity, 'fetch')

    async def test_daily_limit_resets_at_shanghai_midnight_but_cooldown_does_not(self):
        self.budget.config = {'official_fetch_cooldown': 0, 'official_fetch_daily_limit': 1}
        before = datetime(2026, 11, 11, 23, 59, 50, tzinfo=limits.SHANGHAI).timestamp()
        with patch.object(limits.time, 'time', return_value=before):
            await self.budget.reserve(self.identity, 'fetch')
            with self.assertRaisesRegex(limits.SyncLimitError, '次数已达上限（1 次）'):
                await self.budget.reserve(self.identity, 'fetch')
        with patch.object(limits.time, 'time', return_value=before + 10):
            self.budget.config['official_fetch_cooldown'] = 300
            with self.assertRaisesRegex(limits.SyncLimitError, '290 秒'):
                await self.budget.check(self.identity, 'fetch')
        with patch.object(limits.time, 'time', return_value=before + 300):
            await self.budget.reserve(self.identity, 'fetch')
            entry = next(iter(self.budget._state.users.values()))['fetch']
            self.assertEqual((entry.day, entry.count), ('2026-11-12', 1))

    async def test_actions_and_sender_bot_platform_are_independent(self):
        with patch.object(limits.time, 'time', return_value=self.now):
            await self.budget.reserve(self.identity, 'fetch')
            await self.budget.reserve(self.identity, 'upload')
            for identity in (('other', 'bot', 'private-user-id'), ('platform', 'other', 'private-user-id'),
                             ('platform', 'bot', 'other')):
                await self.budget.reserve(identity, 'fetch')
            self.assertEqual(len(self.budget._state.users), 4)

    async def test_concurrent_reservations_only_one_allowed(self):
        with patch.object(limits.time, 'time', return_value=self.now):
            results = await asyncio.gather(*(self.budget.reserve(self.identity, 'fetch') for _ in range(10)),
                                           return_exceptions=True)
        self.assertEqual(sum(value is None for value in results), 1)
        self.assertEqual(sum(isinstance(value, limits.SyncLimitError) for value in results), 9)

    async def test_zero_disables_each_limit_and_invalid_configuration_is_rejected(self):
        self.budget.config = {'official_fetch_cooldown': 0, 'official_fetch_daily_limit': 0}
        for _ in range(3):
            await self.budget.reserve(self.identity, 'fetch')
        self.assertFalse(self.budget.path.exists())
        for value in (-1, True, '60', None):
            self.budget.config['official_fetch_cooldown'] = value
            with self.assertRaisesRegex(OfficialError, '非负整数'):
                await self.budget.check(self.identity, 'fetch')

    async def test_storage_failure_is_fail_closed_without_losing_previous_state(self):
        self.budget.path.write_text('invalid', encoding='utf-8')
        with self.assertRaisesRegex(OfficialError, '损坏'):
            await self.budget.reserve(self.identity, 'fetch')
        self.budget.path.unlink()
        with patch.object(self.budget, '_write', side_effect=PermissionError('secret-path')):
            with self.assertRaisesRegex(OfficialError, '保存失败') as caught:
                await self.budget.reserve(self.identity, 'fetch')
        self.assertNotIn('secret-path', str(caught.exception))
        await self.budget.reserve(self.identity, 'fetch')

    async def test_cancellation_waits_for_disk_commit_before_releasing_lock(self):
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        write = self.budget._write

        def delayed(state):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise RuntimeError('test write timed out')
            write(state)

        with patch.object(self.budget, '_write', side_effect=delayed):
            task = asyncio.create_task(self.budget.reserve(self.identity, 'fetch'))
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        restored = limits.SyncLimits(self.root, {})
        with self.assertRaises(limits.SyncLimitError):
            await restored.check(self.identity, 'fetch')


class SyncBudgetIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = {'official_sync_enabled': True}
        self.service = sync.ScoreSync(self.root, self.config)
        self.service.client.check_available = AsyncMock()
        self.service.client.fetch = AsyncMock(return_value=data())

    async def test_failed_fetch_consumes_budget_but_invalid_or_replayed_sgid_does_not(self):
        self.service.client.fetch.side_effect = OfficialError('offline')
        with self.assertRaisesRegex(OfficialError, '格式'):
            await self.service.sync(event(), 'bad')
        self.assertFalse(self.service.limits.path.exists())
        value = sgid()
        with self.assertRaisesRegex(OfficialError, 'offline'):
            await self.service.sync(event(), value)
        with self.assertRaisesRegex(OfficialError, '已经尝试'):
            await self.service.sync(event(), value)
        with self.assertRaisesRegex(OfficialError, '冷却中'):
            await self.service.sync(event(group='other'), sgid()[:-1] + 'B')
        self.service.client.fetch.assert_awaited_once()
        with self.assertRaisesRegex(OfficialError, '冷却中'):
            await self.service.begin(event(group='other'))

    async def test_upload_budget_shared_by_both_sources_batches_retry_and_restart(self):
        snapshot = sync.snapshot_from_official(data())
        await self.service.bindings.bind(event().get_sender_id(), data().account_id)
        await self.service.store.save(self.service.identity(event()), snapshot)
        with (patch.object(sync, 'upload_records', return_value=([{}] * 501, [{}] * 501)),
              patch.object(sync.maiApi.config, 'df_client_id', 'id'),
              patch.object(sync.maiApi.config, 'df_client_secret', 'secret'),
              patch.object(sync.maiApi, '_divingfish_access_token', AsyncMock(return_value='token')),
              patch.object(sync.maiApi, 'upload_user_records', AsyncMock()) as fish,
              patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token='lx', refresh_token='r')),
              patch.object(sync.LxnsAPI, 'upload_scores_personal', AsyncMock()) as lxns):
            first = await self.service.retry(event())
            second = await sync.ScoreSync(self.root, self.config).retry(event(group='other'))
        self.assertEqual(sum('共上传 501 条成绩' in value for value in first), 2)
        self.assertIn('查分器上传冷却中', second[0])
        self.assertEqual((fish.await_count, lxns.await_count), (2, 2))
        users = json.loads(self.service.limits.path.read_text(encoding='utf-8'))['users']
        self.assertEqual(next(iter(users.values()))['upload']['count'], 1)

    async def test_upload_failure_consumes_once_and_still_attempts_other_provider(self):
        with (patch.object(sync, 'upload_records', return_value=([{}], [{}])),
              patch.object(sync.maiApi.config, 'df_client_id', 'id'),
              patch.object(sync.maiApi.config, 'df_client_secret', 'secret'),
              patch.object(sync.maiApi, '_divingfish_access_token', AsyncMock(return_value='token')),
              patch.object(sync.maiApi, 'upload_user_records', AsyncMock(side_effect=RuntimeError('offline'))),
              patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token='lx', refresh_token='r')),
              patch.object(sync.LxnsAPI, 'upload_scores_personal', AsyncMock()) as lxns):
            result = await self.service._upload(event(), sync.snapshot_from_official(data()))
        self.assertIn('水鱼：上传失败', result[0])
        lxns.assert_awaited_once()
        with self.assertRaises(limits.SyncLimitError):
            await self.service.limits.check(self.service.identity(event()), 'upload')

    async def test_unbound_or_unmatched_records_do_not_consume_upload_budget(self):
        for records in (([], []), ([{}], [{}])):
            with (patch.object(sync, 'upload_records', return_value=records),
                  patch.object(sync.maiApi.config, 'df_client_id', 'id'),
                  patch.object(sync.maiApi.config, 'df_client_secret', 'secret'),
                  patch.object(sync.maiApi, '_divingfish_access_token', AsyncMock(side_effect=DivingFishOAuthNotBoundError())),
                  patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token=''))):
                await self.service._upload(event(), sync.snapshot_from_official(data()))
            self.assertFalse(self.service.limits.path.exists())

    async def test_upload_limit_does_not_discard_fetched_snapshot_or_call_upload_api(self):
        await self.service.limits.reserve(self.service.identity(event()), 'upload')
        with patch.object(sync, 'upload_bound_sources', AsyncMock()) as upload:
            snapshot, messages = await self.service.sync(event(), sgid())
        self.assertIn('上传冷却中', messages[0])
        self.assertIn('本地成绩已保留', messages[0])
        self.assertEqual(await self.service.store.load(self.service.identity(event())), snapshot)
        upload.assert_not_awaited()

    async def test_upload_quota_write_failure_prevents_both_providers(self):
        with (patch.object(sync, 'upload_records', return_value=([{}], [{}])),
              patch.object(sync.maiApi.config, 'df_client_id', 'id'),
              patch.object(sync.maiApi.config, 'df_client_secret', 'secret'),
              patch.object(sync.maiApi, '_divingfish_access_token', AsyncMock(return_value='token')),
              patch.object(sync.maiApi, 'upload_user_records', AsyncMock()) as fish,
              patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token='lx', refresh_token='r')),
              patch.object(sync.LxnsAPI, 'upload_scores_personal', AsyncMock()) as lxns,
              patch.object(self.service.limits, '_write', side_effect=PermissionError('secret-path'))):
            messages = await self.service._upload(event(), sync.snapshot_from_official(data()))
        self.assertIn('限流记录保存失败', messages[0])
        self.assertNotIn('secret-path', str(messages))
        fish.assert_not_awaited()
        lxns.assert_not_awaited()
