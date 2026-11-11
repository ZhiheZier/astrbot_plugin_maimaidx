import asyncio
import copy
import json
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from . import test_management as fixtures
from ..libraries.maimaidx_management_store import ManagementError, write_json
from ..libraries.maimaidx_ranking import SongRankingStore
from ..libraries.maimaidx_sync import PCSnapshot, PCStore, OfficialScore
from ..libraries.maimaidx_sync_limits import SyncLimits, Usage, UsageFile, SHANGHAI
from ..libraries.maimaidx_update_notice import UpdateNotifier
from ..libraries.maimaidx_user import User, userstore
from ..libraries.maimaidx_account_binding import AccountBindings
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_nearcade import NearcadeError


class RawConfig(dict):
    def __init__(self, path, data):
        super().__init__(data)
        self.path, self.fail = path, False

    def save_config(self):
        if self.fail:
            raise OSError('simulated disk failure')
        write_json(self.path, dict(self))


class ExtraManagementTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ManagementTest.asyncSetUp(self)
        import data.plugins.astrbot_plugin_maimaidx as package
        for name in ('music_file', 'chart_file', 'alias_file', 'lxns_music_file', 'lxns_alias_file', 'merge_music_file', 'merge_alias_file'):
            context = patch.object(package, name, self.root / f'{name}.json')
            context.start(); self.addCleanup(context.stop)
        self.player = User(qqid='200', access_token='never-expose-access', refresh_token='never-expose-refresh')
        for name, value in (('_path', self.root / 'players.json'), ('_data', {'200': self.player})):
            context = patch.object(userstore, name, value)
            context.start(); self.addCleanup(context.stop)
        write_json(userstore.path, {'200': self.player.model_dump(exclude={'qqid'}, mode='json')})
        self.plugin._raw_plugin_config = RawConfig(self.root / 'config.json', {'arcade': {}, 'official_sync': {}, 'divingfish': {'df_client_secret': 'never-expose-client-secret'}})
        self.plugin.score_sync = SimpleNamespace(store=PCStore(self.root / 'pc'), limits=SyncLimits(self.root, self.plugin.config), _locks={})
        self.plugin.score_sync.bindings = AccountBindings(self.root / 'official_bindings.json')
        self.plugin.song_ranking_store = SongRankingStore(self.root / 'ranking.json')
        changelog = self.root / 'CHANGELOG.md'
        changelog.write_text('## v1.5.8\n\n### 新功能\n\n- 测试更新\n', encoding='utf-8')
        self.plugin.update_notifier = UpdateNotifier(self.root / 'version.json', '1.5.8', changelog)
        self.plugin.update_notice_task = None
        self.client = SimpleNamespace(get_login_info=AsyncMock(return_value={'user_id': 987, 'nickname': 'test'}), send_group_forward_msg=AsyncMock())
        platform = SimpleNamespace(meta=lambda: SimpleNamespace(name='aiocqhttp', id='qq-test'), get_client=lambda: self.client)
        self.plugin.context = SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform]))
        self.extra = self.manager.extra

    async def asyncTearDown(self):
        await self.extra.shutdown()

    async def list(self, kind, **params):
        return await self.extra.listing({'kind': kind, **params})

    async def test_mapping_roundtrip_live_config_and_secret_isolation(self):
        data = await self.list('mappings', source='local')
        await self.extra.change({'kind': 'mappings', 'action': 'save', 'source': 'local', 'local': '123',
                                 'alias': 'mml', 'shop': '456', 'game': '456000', 'revision': data['meta']['revision']})
        self.assertEqual(self.plugin.config['nearcade_shop_bindings'], ['123[mml]=456:456000'])
        self.assertEqual(self.plugin.nearcade._bindings('123')[0][2], 456000)
        data = await self.list('mappings', source='local')
        self.assertNotIn('never-expose', json.dumps(data))
        await self.extra.change({'kind': 'mappings', 'action': 'delete', 'source': 'local', 'id': data['items'][0]['id'],
                                 'revision': data['meta']['revision'], 'confirm': '删除映射'})
        self.assertEqual(self.plugin.config['nearcade_shop_bindings'], [])
        self.assertEqual(self.plugin._raw_plugin_config['divingfish']['df_client_secret'], 'never-expose-client-secret')

    async def test_invalid_native_alias_and_failed_config_save_do_not_publish(self):
        data = await self.list('mappings', source='nearcade')
        body = {'kind': 'mappings', 'action': 'save', 'source': 'nearcade', 'local': '456', 'alias': 'mai',
                'game': '456000', 'revision': data['meta']['revision']}
        with self.assertRaises(NearcadeError):
            await self.extra.change(body)
        body['alias'] = 'mml'
        self.plugin._raw_plugin_config.fail = True
        before = copy.deepcopy(dict(self.plugin._raw_plugin_config))
        with self.assertRaises(ManagementError):
            await self.extra.change(body)
        self.assertNotIn('nearcade_game_aliases', self.plugin.config)
        self.assertEqual(dict(self.plugin._raw_plugin_config), before)

    async def test_player_preferences_and_unlink_are_scoped_and_never_return_tokens(self):
        data = await self.list('players')
        serialized = json.dumps(data)
        self.assertNotIn('never-expose', serialized)
        self.assertNotIn('access_token', serialized)
        row = data['items'][0]
        result = await self.extra.change({'kind': 'players', 'action': 'save', 'id': '200', 'service': 'Lxns-Network',
                                          'theme': 'circle', 'revision': row['revision']})
        self.assertEqual(userstore.get('200').theme.value, 'circle')
        self.assertEqual(userstore.get('200').access_token, 'never-expose-access')
        await self.extra.change({'kind': 'players', 'action': 'unlink_lxns', 'id': '200', 'confirm': '清除本地授权',
                                 'revision': result['item']['revision']})
        self.assertIsNone(userstore.get('200').access_token)
        self.assertEqual(userstore.get('200').service.value, 'Lxns-Network')

    async def test_player_failed_write_keeps_credentials_and_unknown_qq_can_be_configured(self):
        row = (await self.list('players', q='300'))['items'][0]
        self.assertEqual(row['id'], '300')
        with patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_management_extra.write_json', side_effect=OSError()):
            with self.assertRaises(OSError):
                await self.extra.change({'kind': 'players', 'action': 'save', 'id': '300', 'service': 'Diving-Fish',
                                         'theme': 'circle', 'revision': row['revision']})
        self.assertFalse(userstore.exists('300'))
        self.assertEqual(userstore.get('200').refresh_token, 'never-expose-refresh')

    async def test_official_binding_shown_without_oauth_record_and_unbind_preserves_authorization(self):
        store = self.plugin.score_sync.bindings
        await store.bind('200', 12345)
        await store.bind('300', 54321)
        identity, _ = await self.make_snapshot()
        snapshot_path = self.plugin.score_sync.store.path(identity)
        saved_snapshot = snapshot_path.read_bytes()
        self.assertEqual((await self.list('players', q='300'))['items'][0]['official_user_id'], 54321)
        row = (await self.list('players', q='200'))['items'][0]
        result = await self.extra.change({'kind': 'players', 'action': 'unbind_official', 'id': '200',
                                          'confirm': '解绑游戏账号', 'official_revision': row['official_revision']})
        self.assertIsNone(result['item']['official_user_id'])
        self.assertEqual(await store.records(), {'300': 54321})
        self.assertEqual(userstore.get('200').access_token, 'never-expose-access')
        self.assertEqual(snapshot_path.read_bytes(), saved_snapshot)

    async def test_official_unbind_requires_confirmation_current_binding_and_idle_player(self):
        store = self.plugin.score_sync.bindings
        await store.bind('200', 12345)
        row = (await self.list('players', q='200'))['items'][0]
        body = {'kind': 'players', 'action': 'unbind_official', 'id': '200',
                'confirm': '解绑游戏账号', 'official_revision': row['official_revision']}
        async with store.account_lock('200'):
            with self.assertRaisesRegex(ManagementError, '正在同步'):
                await self.extra.change(body)
        with self.assertRaises(ManagementError):
            await self.extra.change({**body, 'confirm': ''})
        await store.unbind('200', 12345)
        await store.bind('200', 54321)
        with self.assertRaisesRegex(ManagementError, '变化'):
            await self.extra.change(body)
        self.assertEqual(await store.records(), {'200': 54321})

    async def test_search_matches_visible_fields_not_settings_or_revision(self):
        await self.plugin.score_sync.bindings.bind('200', 54321)
        rows = (await self.list('players', q='54321'))['items']
        self.assertEqual([row['id'] for row in rows], ['54321'])
        self.assertEqual((await self.list('players', q='Diving-Fish'))['items'], [])
        self.assertEqual((await self.list('players', q='200'))['items'][0]['id'], '200')

    async def test_cleared_local_authorization_is_not_restored_by_a_stale_api_instance(self):
        from ..libraries.maimaidx_lxns import LxnsAPI

        api = LxnsAPI(qqid='200', access_token='never-expose-access', refresh_token='never-expose-refresh')
        row = (await self.list('players'))['items'][0]
        await self.extra.change({'kind': 'players', 'action': 'unlink_lxns', 'id': '200', 'confirm': '清除本地授权',
                                 'revision': row['revision']})
        with patch.object(api, 'oauth_refresh_token', AsyncMock()) as refresh:
            self.assertFalse(await api._refresh_oauth_token('never-expose-access'))
            refresh.assert_not_awaited()

    async def test_explicit_waterfish_probe_discards_token_and_clear_is_not_revocation(self):
        probe = AsyncMock(return_value='never-expose-probe-token')
        with patch.object(maiApi.config, 'df_client_id', 'test-client'), patch.object(maiApi.config, 'df_client_secret', 'test-secret'), patch.object(maiApi, '_divingfish_access_token', probe):
            result = await self.extra.change({'kind': 'players', 'action': 'check_df', 'id': '200'})
            self.assertNotIn('never-expose', json.dumps(result))
            subject = maiApi._divingfish_subject(qqid='200')
            with patch.object(maiApi, '_oauth_tokens', {subject: ('token', time.monotonic() + 300)}):
                row = self.extra.player_row('200')
                self.assertTrue(row['df_cached'])
                result = await self.extra.change({'kind': 'players', 'action': 'clear_df_cache', 'id': '200',
                                                  'revision': row['revision'], 'confirm': '清除短期缓存'})
                self.assertFalse(result['item']['df_cached'])
                self.assertIn('未解除', result['message'])

    async def test_ranking_only_removes_selected_opt_in_member(self):
        store = self.plugin.song_ranking_store
        await store.set_member('987:200', '200', '测试昵称', joined=True)
        await store.set_member('987:300', '200', '测试昵称', joined=True)
        row = (await self.list('rankings', q='987:200'))['items'][0]
        await self.extra.change({'kind': 'rankings', 'action': 'remove', 'scope': row['scope'], 'qq': row['qq'],
                                 'revision': row['revision'], 'confirm': '移出排行榜'})
        self.assertEqual(await store.members('987:200'), {})
        self.assertIn('200', await store.members('987:300'))
        with self.assertRaises(ManagementError):
            await self.extra.change({'kind': 'rankings', 'action': 'add', 'confirm': '移出排行榜'})

    async def make_snapshot(self):
        identity = ('qq-test', '987', '200')
        snapshot = PCSnapshot(account_hash='private-account-hash', name='测试玩家', rating=12345, updated_at=1,
                              scores=[OfficialScore(song_id=1, level_index=3, achievements=100.5,
                                                    dx_score=100, combo=0, sync=0, play_count=7)])
        await self.plugin.score_sync.store.save(identity, snapshot)
        return identity, (await self.list('pc'))['items'][0]

    async def test_pc_snapshot_readonly_detail_and_scoped_delete(self):
        identity, row = await self.make_snapshot()
        self.assertNotIn('private-account-hash', json.dumps(await self.list('pc')))
        details = await self.extra.detail({'kind': 'pc', 'id': row['id']})
        self.assertEqual(details['items'][0]['pc'], 7)
        lock = asyncio.Lock()
        self.plugin.score_sync._locks[identity] = lock
        async with lock:
            with self.assertRaises(ManagementError):
                await self.extra.change({'kind': 'pc', 'id': row['id'], 'action': 'delete', 'revision': row['revision'], 'confirm': '删除快照'})
        await self.extra.change({'kind': 'pc', 'id': row['id'], 'action': 'delete', 'revision': row['revision'], 'confirm': '删除快照'})
        self.assertIsNone(await self.plugin.score_sync.store.load(identity))
        with self.assertRaises(ManagementError):
            await self.extra.detail({'kind': 'pc', 'id': '../players'})

    async def test_limits_reset_preserves_other_action_and_settings_keep_counts(self):
        store = self.plugin.score_sync.limits
        key = 'a' * 64
        usage = Usage(day=datetime.now(SHANGHAI).date().isoformat(), count=2, last_at=time.time())
        store._state = UsageFile(users={key: {'fetch': usage, 'upload': usage.model_copy()}})
        row = next(item for item in (await self.list('limits'))['items'] if item['action'] == 'fetch')
        await self.extra.change({'kind': 'limits', 'id': key, 'action': 'fetch', 'revision': row['revision'], 'confirm': '重置额度'})
        self.assertNotIn('fetch', store._state.users[key])
        self.assertEqual(store._state.users[key]['upload'].count, 2)
        data = await self.list('limits')
        await self.extra.change({'kind': 'limits', 'action': 'settings', 'revision': data['meta']['revision'],
                                 'settings': {key: 0 for key in data['meta']['settings']}})
        self.assertEqual(store._state.users['a' * 64]['upload'].count, 2)
        self.assertEqual(store._settings('upload'), [0, 0])

    async def test_cache_clear_is_allowlisted_and_refresh_is_background(self):
        paths = self.extra.cache_paths()
        write_json(paths['merged_music'], [])
        write_json(paths['music'], [])
        row = next(item for item in (await self.list('cache'))['items'] if item['id'] == 'merged_music')
        await self.extra.change({'kind': 'cache', 'action': 'clear', 'id': row['id'], 'revision': row['revision'], 'confirm': '清理派生缓存'})
        self.assertFalse(paths['merged_music'].exists())
        self.assertTrue(paths['music'].exists())
        with self.assertRaises(ManagementError):
            await self.extra.change({'kind': 'cache', 'action': 'clear', 'id': '../players', 'revision': row['revision'], 'confirm': '清理派生缓存'})
        with patch.object(self.manager.plugin, '_load_maimai_data', create=True), patch.object(
                __import__('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_music', fromlist=['mai']).mai, 'update', AsyncMock()):
            result = await self.extra.change({'kind': 'cache', 'action': 'refresh_music'})
            self.assertEqual(result['job']['phase'], 'running')
            await self.extra._task
            self.assertEqual(self.extra.job['phase'], 'completed')

    async def test_notice_preview_does_not_send_and_manual_send_requires_enabled_group(self):
        data = await self.list('notices')
        self.client.send_group_forward_msg.assert_not_awaited()
        row = data['items'][0]
        with self.assertRaises(ManagementError):
            await self.extra.change({'kind': 'notices', 'action': 'send', 'platform': 'qq-test', 'group': '300',
                                     'confirm': '300', 'revision': row['revision']})
        await self.extra.change({'kind': 'notices', 'action': 'send', 'platform': 'qq-test', 'group': '200',
                                 'confirm': '200', 'revision': row['revision']})
        self.client.send_group_forward_msg.assert_awaited_once()
        self.assertEqual(self.client.send_group_forward_msg.call_args.kwargs['group_id'], 200)
        self.assertFalse(self.plugin.update_notifier.path.exists())
        audit = json.loads((self.root / 'management_notice_log.json').read_text(encoding='utf-8'))
        self.assertEqual(audit[0]['status'], 'success')

    async def test_notice_rows_filter_by_version_or_content_while_preview_and_send_stay_current(self):
        self.plugin.update_notifier.changelog.write_text(
            '## v1.5.8\n### 新功能\n- 最新更新\n## v1.5.7\n### 修复\n- 历史修复\n'
            '## v1.5.6\n### 优化\n- 历史优化\n', encoding='utf-8')
        listing = await self.list('notices')
        self.assertEqual([row['version'] for row in listing['items']], ['1.5.8', '1.5.7', '1.5.6'])
        data = await self.list('notices', q='修复')
        self.assertEqual((data['page'], data['pages'], data['total']), (1, 1, 1))
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['version'], '1.5.7')
        notes = data['items'][0]['notes']
        self.assertIn('历史修复', notes)
        self.assertEqual(data['items'][0]['summary'], '历史修复')
        self.assertEqual((await self.list('notices', q='v1.5.7'))['items'][0]['version'], '1.5.7')
        self.assertEqual((await self.list('notices', q='contact@'))['items'], [])
        for excluded in ('最新更新', '历史优化', '完整更新日志：', '/maihelp', 'contact@'):
            self.assertNotIn(excluded, notes)
        full = data['meta']['preview']
        for included in ('舞萌DX插件当前版本：v1.5.8', '最新更新', '完整更新日志：', '/maihelp', 'contact@'):
            self.assertIn(included, full)
        self.assertNotIn('历史修复', full)
        await self.extra.change({'kind': 'notices', 'action': 'send', 'group': '200', 'platform': 'qq-test',
                                 'confirm': '200', 'revision': data['meta']['revision']})
        sent = ''.join(node['data']['content'][0]['data']['text'] for node in self.client.send_group_forward_msg.call_args.kwargs['messages'])
        self.assertIn('最新更新', sent)
        self.assertNotIn('历史修复', sent)
        self.assertEqual((await self.list('notices', page='999'))['page'], 1)

    async def test_notice_rows_paginate_thirty_versions_at_a_time(self):
        self.plugin.update_notifier.changelog.write_text(
            '## v1.5.8\n### 新功能\n- 当前更新\n' + ''.join(
                f'## v1.4.{number}\n### 修复\n- 历史修复 {number}\n' for number in range(1, 36)), encoding='utf-8')
        first, second = await self.list('notices'), await self.list('notices', page='2')
        self.assertEqual((first['total'], first['pages'], len(first['items'])), (36, 2, 30))
        self.assertEqual(len(second['items']), 6)
        self.assertEqual(second['items'][-1]['version'], '1.4.1')

    async def test_unknown_modules_and_incorrect_confirmation_do_not_change_data(self):
        with self.assertRaises(ManagementError):
            await self.extra.listing({'kind': '../config'})
        with self.assertRaises(ManagementError):
            await self.extra.change({'kind': 'pc', 'id': 'a' * 64, 'action': 'delete', 'confirm': 'wrong'})


if __name__ == '__main__':
    unittest.main()
