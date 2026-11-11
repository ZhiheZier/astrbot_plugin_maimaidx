import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import astrbot.api.message_components as Comp

from ..command.mai_base import _authorize_url
from ..command.mai_sync import pc_handler, sync_command_handler, sync_credential_handler
from ..libraries import maimaidx_sync as sync
from ..libraries.maimaidx_api_data import MaimaiAPI, maiApi
from ..libraries.maimaidx_error import DivingFishOAuthNotBoundError
from ..libraries.maimaidx_lxns import LxnsAPI
from ..libraries.maimaidx_official import (
    OfficialClient, OfficialData, OfficialError, OfficialSession,
    decode_packet, encode_packet, official_tls_context, validate_sgid,
)
from ..main import MaimaiDXPlugin


def sgid(age=0):
    stamp = datetime.fromtimestamp(time.time() - age, timezone(timedelta(hours=8))).strftime('%y%m%d%H%M%S')
    return f'SGWCMAID{stamp}' + 'A' * 64


def detail(**changes):
    return {'musicId': 11103, 'level': 3, 'achievement': 1005000, 'deluxscoreMax': 2400,
            'comboStatus': 4, 'syncStatus': 4, 'playCount': 7, **changes}


def data(details=None):
    return OfficialData(12345, '测试玩家', 16000, [detail()] if details is None else details)


def event(text='', *, user='123456', bot='100', group=None, platform='onebot'):
    return SimpleNamespace(
        message_obj=SimpleNamespace(group_id=group, message_id='42', message=[Comp.Plain(text)]),
        get_sender_id=lambda: user, get_self_id=lambda: bot, get_platform_id=lambda: platform,
        get_platform_name=lambda: 'aiocqhttp', plain_result=lambda value: value,
        chain_result=lambda value: value, bot=SimpleNamespace(delete_msg=AsyncMock()),
    )


class OfficialProtocolTest(unittest.IsolatedAsyncioTestCase):
    def test_sgid_uses_shanghai_time_and_rejects_stale_or_embedded_text(self):
        self.assertEqual(validate_sgid(sgid())[:8], 'SGWCMAID')
        for value in (sgid(181), sgid(-70), 'text ' + sgid(), 'SGWCMAIDinvalid', sgid() + '\nextra'):
            with self.subTest(value=value), self.assertRaises(OfficialError):
                validate_sgid(value)

    def test_codec_roundtrip_and_non_objects_rejected(self):
        payload = {'userId': 123, '中文': [1, 2]}
        self.assertEqual(decode_packet(encode_packet(payload)), payload)
        with self.assertRaises(Exception):
            decode_packet(b'broken')
        self.assertNotIn('secret', repr(OfficialSession(123, 'secret')))

    async def test_disabled_backend_never_opens_https_session(self):
        client = OfficialClient(Path('.'), {})
        with patch.object(client, 'http_session') as http:
            with self.assertRaisesRegex(OfficialError, '尚未开启'):
                await client.fetch(sgid())
        http.assert_not_called()

    async def test_native_files_and_legacy_hash_no_longer_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'official_sync_enabled': True, 'official_client_id': 'TEST0000001',
                      'official_runtime_sha256': 'obsolete-and-ignored'}
            client = OfficialClient(root, config)
            self.assertIs(await client.check_available(), official_tls_context())
            self.assertFalse((root / 'official').exists())

    async def test_full_fetch_paginates_and_logs_out_on_success_or_failure(self):
        client = OfficialClient(Path('.'), {})
        client.client_id = 'TEST0000001'
        calls = []

        async def post(http, base, api, uid, payload):
            calls.append((api, payload))
            if api == 'GetGameSettingApi':
                return {'gameSetting': {'movieServerUri': 'runtime/'}}
            if api == 'GetUserDataApi':
                return {'userData': {'userName': 'Player', 'playerRating': 16000}}
            if api == 'UserLoginApi':
                return {'returnCode': 1}
            if api == 'GetUserMusicApi':
                return {'userMusicList': [{'musicId': 11103, 'userMusicDetailList': [detail()]}],
                        'nextIndex': 50 if payload['nextIndex'] == 0 else 0}
            return {}

        with (patch.object(client, 'check_available', AsyncMock(return_value=official_tls_context())),
              patch.object(client, '_resolve_session', AsyncMock(return_value=OfficialSession(123, 'secret'))),
              patch.object(client, '_post', side_effect=post)):
            result = await client.fetch(sgid())
        self.assertEqual((result.name, result.rating, len(result.details)), ('Player', 16000, 2))
        self.assertEqual([p['nextIndex'] for api, p in calls if api == 'GetUserMusicApi'], [0, 50])
        self.assertEqual(calls[-1][0], 'UserLogoutApi')
        for api, payload in calls:
            if api == 'UserLoginApi':
                self.assertEqual(payload['token'], 'secret')

        async def failure(*args):
            if args[2] == 'GetUserMusicApi':
                raise OfficialError('offline')
            return await post(*args)

        calls.clear()
        with (patch.object(client, 'check_available', AsyncMock(return_value=official_tls_context())),
              patch.object(client, '_resolve_session', AsyncMock(return_value=OfficialSession(123, 'secret'))),
              patch.object(client, '_post', side_effect=failure)):
            with self.assertRaises(OfficialError):
                await client.fetch(sgid())
        self.assertEqual(calls[-1][0], 'UserLogoutApi')

    async def test_setting_cannot_redirect_credentials_to_another_host(self):
        client = OfficialClient(Path('.'), {})
        with (patch.object(client, 'check_available', AsyncMock(return_value=official_tls_context())),
              patch.object(client, '_resolve_session', AsyncMock(return_value=OfficialSession(123, 'secret'))),
              patch.object(client, '_post', AsyncMock(return_value={
                  'gameSetting': {'movieServerUri': 'https://example.com/'},
              })) as request):
            with self.assertRaisesRegex(OfficialError, '地址不合法'):
                await client.fetch(sgid())
        request.assert_awaited_once()

    async def test_account_already_playing_is_not_logged_in_or_logged_out(self):
        client = OfficialClient(Path('.'), {})

        async def post(http, base, api, uid, payload):
            if api == 'GetGameSettingApi':
                return {'gameSetting': {'movieServerUri': 'runtime/'}}
            return {'isLogin': True}

        with (patch.object(client, 'check_available', AsyncMock(return_value=official_tls_context())),
              patch.object(client, '_resolve_session', AsyncMock(return_value=OfficialSession(123, 'secret'))),
              patch.object(client, '_post', AsyncMock(side_effect=post)) as request):
            with self.assertRaisesRegex(OfficialError, '正在游戏'):
                await client.fetch(sgid())
        self.assertEqual([call.args[2] for call in request.await_args_list],
                         ['GetGameSettingApi', 'GetUserPreviewApi'])

    async def test_unconfirmed_login_stops_read_and_attempts_logout(self):
        client = OfficialClient(Path('.'), {})
        for response in ({}, {'returnCode': 0}, {'returnCode': True}):
            async def post(http, base, api, uid, payload):
                if api == 'GetGameSettingApi':
                    return {'gameSetting': {'movieServerUri': 'runtime/'}}
                if api == 'UserLoginApi':
                    return response
                return {}

            with (patch.object(client, 'check_available', AsyncMock(return_value=official_tls_context())),
                  patch.object(client, '_resolve_session', AsyncMock(return_value=OfficialSession(123, 'secret'))),
                  patch.object(client, '_post', AsyncMock(side_effect=post)) as request):
                with self.assertRaisesRegex(OfficialError, '未确认登录成功'):
                    await client.fetch(sgid())
            apis = [call.args[2] for call in request.await_args_list]
            self.assertNotIn('GetUserDataApi', apis)
            self.assertNotIn('GetUserMusicApi', apis)
            self.assertEqual(apis[-1], 'UserLogoutApi')


class SnapshotTest(unittest.IsolatedAsyncioTestCase):
    def test_pc_is_per_chart_missing_is_not_zero_and_duplicates_not_added(self):
        source = [detail(), detail(), detail(level=4, playCount=20), detail(level=2, playCount=None)]
        snapshot = sync.snapshot_from_official(data(source))
        self.assertEqual(len(snapshot.scores), 3)
        self.assertEqual([score.play_count for score in snapshot.pc50(lambda _: False)], [20, 7])
        self.assertIsNone(snapshot.scores[2].play_count)

    def test_bad_fields_and_inconsistent_duplicates_are_rejected(self):
        broken = detail()
        del broken['comboStatus']
        for source in ([], [broken], [detail(playCount=-1)], [detail(playCount=True)],
                       [detail(achievement=1005000.5)], [detail(syncStatus=9)],
                       [detail(), detail(playCount=3)]):
            with self.subTest(source=source), self.assertRaises(OfficialError):
                sync.snapshot_from_official(data(source))

    async def test_atomic_store_isolated_by_user_bot_platform_and_account_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            store = sync.PCStore(Path(directory))
            identity = ('onebot', '100', '123456')
            snapshot = sync.snapshot_from_official(data())
            await store.save(identity, snapshot)
            for other in (('other', '100', '123456'), ('onebot', '200', '123456'), ('onebot', '100', '654321')):
                self.assertIsNone(await store.load(other))
            text = store.path(identity).read_text(encoding='utf-8')
            self.assertNotIn('token', text)
            self.assertNotIn('12345"', text)
            with patch.object(store, '_write', side_effect=OSError('full')):
                with self.assertRaises(OSError):
                    await store.save(identity, snapshot.model_copy(update={'name': 'Changed'}))
            self.assertEqual((await store.load(identity)).name, snapshot.name)
            replacement = sync.snapshot_from_official(OfficialData(999, 'Other', 1, [detail(playCount=2)]))
            await store.save(identity, replacement)
            self.assertEqual((await store.load(identity)).scores[0].play_count, 2)
            await store.delete(identity)
            self.assertIsNone(await store.load(identity))


class SyncServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = sync.ScoreSync(Path(self.directory.name), {'official_sync_enabled': True})
        self.service.client.check_available = AsyncMock(return_value=Path('unused'))
        self.service.client.fetch = AsyncMock(return_value=data())

    async def test_snapshot_saved_before_upload_and_same_qr_not_reused_across_users(self):
        async def upload(*args, **kwargs):
            self.assertIsNotNone(await self.service.store.load(self.service.identity(event())))
            return ['水鱼：失败', '落雪：成功']

        value = sgid()
        with patch.object(sync, 'upload_bound_sources', side_effect=upload):
            first, statuses = await self.service.sync(event(), value)
            self.assertEqual(first.scores[0].play_count, 7)
            self.assertEqual(statuses, ['水鱼：失败', '落雪：成功'])
            with self.assertRaisesRegex(OfficialError, '已经尝试'):
                await self.service.sync(event(user='other'), value)
        self.service.client.fetch.assert_awaited_once()

    async def test_failed_fetch_does_not_replace_existing_snapshot(self):
        previous = sync.snapshot_from_official(data())
        await self.service.store.save(self.service.identity(event()), previous)
        self.service.client.fetch.side_effect = OfficialError('offline')
        with patch.object(sync, 'upload_bound_sources', AsyncMock()) as upload:
            with self.assertRaises(OfficialError):
                await self.service.sync(event(), sgid())
        self.assertEqual(await self.service.store.load(self.service.identity(event())), previous)
        upload.assert_not_awaited()

    async def test_pending_session_is_scoped_to_conversation_and_cancellable(self):
        request = event('同步成绩', group='10')
        messages = [item async for item in sync_command_handler(request, self.service)]
        self.assertIn('180', messages[0])
        self.assertTrue(self.service.waiting(request))
        for other in (event(group='20'), event(), event(group='10', user='other'), event(group='10', bot='other')):
            self.assertFalse(self.service.waiting(other))
        await anext(sync_command_handler(event('取消成绩更新', group='10'), self.service))
        self.assertFalse(self.service.waiting(request))

    async def test_retry_does_not_need_or_reuse_sgid(self):
        await self.service.store.save(self.service.identity(event()), sync.snapshot_from_official(data()))
        await self.service.bindings.bind(event().get_sender_id(), data().account_id)
        with patch.object(sync, 'upload_bound_sources', AsyncMock(return_value=['OK'])) as upload:
            self.assertEqual(await self.service.retry(event()), ['OK'])
        upload.assert_awaited_once()
        self.service.client.fetch.assert_not_awaited()

    async def test_own_busy_sync_rejected_and_error_does_not_echo_credentials(self):
        identity = self.service.identity(event())
        async with self.service.lock(identity):
            with self.assertRaisesRegex(OfficialError, '正在同步'):
                await self.service.sync(event(), sgid())
        self.service.client.fetch.side_effect = RuntimeError('secret-token-do-not-echo')
        with patch('astrbot.api.logger.warning'):
            messages = [item async for item in sync_credential_handler(event(), self.service, sgid())]
        self.assertNotIn('secret-token', str(messages))

    async def test_pc_queries_never_fetch_remote_and_disabled_group_ignored(self):
        messages = [item async for item in pc_handler(event('pc50'), self.service)]
        self.assertIn('尚无', messages[0])
        self.service.client.fetch.assert_not_awaited()
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.score_sync = self.service
        plugin._is_group_enabled = lambda _: False
        for handler in (plugin.sync_official_scores, plugin.pc50, plugin.official_score_input):
            self.assertEqual([item async for item in handler(event('同步成绩', group='10'))], [])


class ProberUploadTest(unittest.IsolatedAsyncioTestCase):
    async def test_both_bound_sources_uploaded_and_pc_not_sent(self):
        fish, lxns = [{'title': 'Test'}], [{'id': 1}]
        with (patch.object(sync, 'upload_records', return_value=(fish, lxns)),
              patch.object(maiApi.config, 'df_client_id', 'id'),
              patch.object(maiApi.config, 'df_client_secret', 'secret'),
              patch.object(maiApi, '_divingfish_access_token', AsyncMock(return_value='token')),
              patch.object(maiApi, 'upload_user_records', AsyncMock()) as df,
              patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token='lx', refresh_token='r')),
              patch.object(LxnsAPI, 'upload_scores_personal', AsyncMock()) as lx):
            result = await sync.upload_bound_sources('123456', sync.snapshot_from_official(data()))
        df.assert_awaited_once_with('123456', fish)
        lx.assert_awaited_once_with(lxns)
        self.assertEqual(sum('上传成功' in item for item in result), 2)
        self.assertIn('水鱼：上传成功，共上传 1 条成绩。', result)
        self.assertIn('落雪：上传成功，共上传 1 条成绩。', result)
        self.assertNotIn('张谱面', str(result))

    async def test_partial_batch_failure_does_not_report_count_or_stop_other_source(self):
        for failing in ('fish', 'lxns'):
            fish = AsyncMock(side_effect=[None, RuntimeError('secret')]) if failing == 'fish' else AsyncMock()
            lxns = AsyncMock(side_effect=[None, RuntimeError('secret')]) if failing == 'lxns' else AsyncMock()
            with (patch.object(sync, 'upload_records', return_value=([{}] * 501, [{}] * 501)),
                  patch.object(maiApi.config, 'df_client_id', 'id'),
                  patch.object(maiApi.config, 'df_client_secret', 'secret'),
                  patch.object(maiApi, '_divingfish_access_token', AsyncMock(return_value='token')),
                  patch.object(maiApi, 'upload_user_records', fish),
                  patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token='lx', refresh_token='r')),
                  patch.object(LxnsAPI, 'upload_scores_personal', lxns)):
                result = await sync.upload_bound_sources('123456', sync.snapshot_from_official(data()))
            failed = next(line for line in result if '上传失败' in line)
            self.assertIn('部分成绩可能已上传', failed)
            self.assertNotRegex(failed, r'\d')
            self.assertNotIn('secret', str(result))
            self.assertEqual(sum('共上传 501 条成绩' in line for line in result), 1)
            self.assertEqual((fish.await_count, lxns.await_count), (2, 2))

    async def test_skipped_scores_do_not_report_chart_counts(self):
        snapshot = sync.snapshot_from_official(data([detail(), detail(musicId=11104)]))
        with (patch.object(sync, 'upload_records', return_value=([{}], [{}])),
              patch.object(maiApi.config, 'df_client_id', ''),
              patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token=''))):
            result = await sync.upload_bound_sources('123456', snapshot)
        self.assertIn('部分成绩因曲库未收录或暂不支持已跳过，原始 PC 数据仍保留。', result)
        self.assertNotRegex(str(result), r'\d')

    async def test_fish_failure_does_not_prevent_lxns_and_unbound_is_skipped(self):
        for failure in (RuntimeError('secret'), DivingFishOAuthNotBoundError()):
            with (patch.object(sync, 'upload_records', return_value=([{}], [{}])),
                  patch.object(maiApi.config, 'df_client_id', 'id'),
                  patch.object(maiApi.config, 'df_client_secret', 'secret'),
                  patch.object(maiApi, '_divingfish_access_token', AsyncMock(side_effect=failure)),
                  patch.object(sync.userstore, 'get', return_value=SimpleNamespace(access_token='lx', refresh_token='r')),
                  patch.object(LxnsAPI, 'upload_scores_personal', AsyncMock()) as lx):
                result = await sync.upload_bound_sources('123456', sync.snapshot_from_official(data()))
            lx.assert_awaited_once()
            self.assertIn('落雪：上传成功', str(result))
            self.assertNotIn('secret', str(result))

    async def test_oauth_upload_uses_write_scope_and_separate_cache(self):
        api = MaimaiAPI()
        api.config.df_client_id = 'id'
        api.config.df_client_secret = 'secret'
        api._oauth_tokens['ref:test'] = ('read-token', time.monotonic() + 600)
        with patch.object(api, '_request_divingfish_oauth', AsyncMock(return_value={
            'access_token': 'write-token', 'expires_in': 300,
        })) as request:
            token = await api._divingfish_access_token('ref:test', scope='prober.records.write')
        self.assertEqual(token, 'write-token')
        self.assertEqual(request.await_args.kwargs['data']['scope'], 'prober.records.write')
        self.assertEqual(api._oauth_tokens['ref:test'][0], 'read-token')
        api._clear_divingfish_tokens('ref:test')
        self.assertEqual(api._oauth_tokens, {})
        with patch.object(api, '_requestmai_oauth', AsyncMock(return_value={})) as request:
            await api.upload_user_records('123', [{'title': 'Test'}])
        self.assertEqual(request.await_args.args, ('POST', '/player/update_records'))
        self.assertEqual(request.await_args.kwargs['oauth_scope'], 'prober.records.write')

    async def test_write_scopes_requested_only_when_sync_enabled(self):
        with patch.object(maiApi.config, 'official_sync_enabled', True):
            self.assertEqual(parse_qs(urlparse(_authorize_url()).query)['scope'], ['read_player write_player'])
        api = MaimaiAPI()
        api.config.df_client_id = 'id'
        api.config.df_client_secret = 'secret'
        api.config.official_sync_enabled = True
        with patch.object(api, '_request_divingfish_oauth', AsyncMock(return_value={})) as request:
            await api.start_divingfish_binding('123')
        self.assertIn('prober.records.write', request.await_args.kwargs['data']['scope'])

    async def test_lxns_upload_personal_endpoint_and_business_failure(self):
        api = LxnsAPI(access_token='token')
        with patch.object(api, '_request', AsyncMock(return_value={'success': True})) as request:
            await api.upload_scores_personal([{'id': 1}])
        self.assertEqual(request.await_args.args, ('POST', 'https://maimai.lxns.net/api/v0/user/maimai/player/scores'))
        self.assertEqual(request.await_args.kwargs['headers'], {'Authorization': 'Bearer token'})
        with patch.object(api, '_request', AsyncMock(return_value={'success': False})):
            with self.assertRaises(Exception):
                await api.upload_scores_personal([{'id': 1}])
