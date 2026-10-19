import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_catalog import NearcadeArcadeList
from ..libraries.maimaidx_nearcade import NearcadeClient, NearcadeError
from ..main import MaimaiDXPlugin
from .test_arcade_cloud_query import attendance
from .test_arcade_games import arcade
from .test_nearcade import client, collect, event, game, shop


class ArcadeCatalogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'nearcade_arcades.json'
        self.api = client(arcade_data_source='nearcade', nearcade_shop_bindings=['456=999'])
        self.api._catalog = NearcadeArcadeList(self.path)
        self.games = [game(), game(gameId=456001, titleId=3, name='中二节奏')]
        self.counts = {'mai': 5, 'chu': 3}
        self.api._request = AsyncMock(side_effect=self.request)
        self.local = a.ArcadeList([arcade(id='456', name='本地另一家店')])
        self.local.save_arcade = AsyncMock(return_value=True)
        self.local_patch = patch.object(a.arcade, 'total', self.local, create=True)
        self.local_patch.start()
        self.addCleanup(self.local_patch.stop)

    async def request(self, method, path, **kwargs):
        if method == 'POST':
            for item in kwargs['payload']['games']:
                target = next(game for game in self.games if game['gameId'] == item['id'])
                key = 'mai' if target['titleId'] == 1 else 'chu'
                self.counts[key] = item['currentAttendances']
            return {'success': True}
        if path == '/shops':
            word = kwargs['params']['q']
            matches = [shop(games=self.games)] if word in '测试机厅' else []
            return {'shops': matches, 'hasNextPage': False}
        if path == '/shops/456':
            return {'shop': shop(games=self.games)}
        if path == '/shops/456/attendance':
            return attendance(*(dict(item, total=self.counts['mai' if item['titleId'] == 1 else 'chu']) for item in self.games))
        raise AssertionError(f'unexpected {method} {path}')

    async def command(self, handler, text, *, admins=None, group='200'):
        args = (event(text=text, group=group),)
        if admins is not None:
            args += (admins,)
        return [message async for message in handler(*args, nearcade=self.api)]

    async def subscribe(self):
        return await self.command(mai_arcade.subscribe_arcade_handler, '订阅机厅 456', admins=['123456'])

    def test_default_and_invalid_source_preserve_local_mode(self):
        for config in ({}, {'arcade_data_source': 'local'}, {'arcade_data_source': 'unknown'}):
            api = NearcadeClient(config)
            self.assertFalse(api.uses_cloud_catalog)
            self.assertIs(mai_arcade._arcade_records(api), self.local)

    async def test_search_queries_live_catalog_without_mapping_or_local_shop(self):
        result = '\n'.join(await self.command(mai_arcade.search_arcade_handler, '查找机厅 测试'))
        self.assertIn('测试机厅', result)
        self.assertIn('456（nearcade）', result)
        self.assertIn('中二节奏×2', result)
        self.assertNotIn('本地另一家店', result)
        self.api._request.assert_awaited_once_with('GET', '/shops', params={'q': '测试', 'page': 1, 'limit': 20})
        self.assertFalse(self.path.exists())
        self.local.save_arcade.assert_not_awaited()

    async def test_id_lookup_is_direct_and_ignores_legacy_mappings(self):
        self.api.config['nearcade_shop_bindings'] = 'malformed-unused-config'
        records, _ = await self.api.search_catalog('456')
        self.assertEqual(records[0].id, '456')
        self.assertEqual({game.key for game in self.api.games_for('456')}, {'mai', 'chu'})
        self.api._request.assert_awaited_once_with('GET', '/shops/456')

    async def test_subscriptions_are_isolated_and_survive_reload_and_source_switch(self):
        self.assertIn('订阅添加成功', (await self.subscribe())[0])
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data[0]['group'], [200])
        self.assertEqual(self.local[0].name, '本地另一家店')
        self.assertEqual(self.local[0].group, [200])
        self.api._catalog = NearcadeArcadeList(self.path)
        self.assertEqual(self.api.catalog.group_subscribe_arcade(200)[0].name, '测试机厅')
        self.api.config['arcade_data_source'] = 'local'
        self.assertIs(mai_arcade._arcade_records(self.api), self.local)
        self.api.config['arcade_data_source'] = 'nearcade'
        self.assertIs(mai_arcade._arcade_records(self.api), self.api.catalog)
        self.local.save_arcade.assert_not_awaited()

    async def test_ordinary_member_cannot_subscribe_and_private_context_cannot_subscribe(self):
        for admins, group in (([], '200'), (['123456'], None)):
            result = await self.command(mai_arcade.subscribe_arcade_handler, '订阅机厅 456', admins=admins, group=group)
            self.assertTrue('仅允许' in result[0] or '仅在群聊' in result[0])
        self.api._request.assert_not_awaited()
        self.assertFalse(self.path.exists())

    async def test_ambiguous_names_never_subscribe_arbitrary_first_shop(self):
        self.api._request.side_effect = None
        self.api._request.return_value = {'shops': [shop(), shop(id=457)], 'hasNextPage': False}
        result = await self.command(mai_arcade.subscribe_arcade_handler, '订阅机厅 测试机厅', admins=['123456'])
        self.assertIn('找到多个机厅', result[0])
        self.assertFalse(any(record.group for record in self.api.catalog))
        self.assertFalse(self.path.exists())

    async def test_truncated_search_never_binds_a_possibly_ambiguous_shop(self):
        self.api._request.side_effect = None
        self.api._request.return_value = {'shops': [shop()], 'hasNextPage': True}
        result = await self.command(mai_arcade.subscribe_arcade_handler, '订阅机厅 测试机厅', admins=['123456'])
        self.assertIn('缩小关键词', result[0])
        self.assertFalse(self.path.exists())

    async def test_cancel_can_remove_subscription_offline_without_cloud_access(self):
        await self.subscribe()
        self.api._request.reset_mock()
        self.api._request.side_effect = NearcadeError('offline')
        result = await self.command(mai_arcade.subscribe_arcade_handler, '取消订阅机厅 456', admins=['123456'])
        self.assertIn('订阅取消成功', result[0])
        self.api._request.assert_not_awaited()
        self.assertEqual(NearcadeArcadeList(self.path).group_subscribe_arcade(200), [])

    async def test_aliases_belong_to_cloud_id_and_resolve_to_fresh_cloud_metadata(self):
        await self.command(mai_arcade.arcade_alias_handler, '添加机厅别名 456 云端别名')
        self.api._catalog = NearcadeArcadeList(self.path)
        self.api._request.reset_mock()
        records = await self.api.find_catalog('云端别名')
        self.assertEqual(records[0].id, '456')
        self.api._request.assert_awaited_once_with('GET', '/shops/456')
        self.assertNotIn('云端别名', self.local[0].alias)

    async def test_previously_cached_partial_name_does_not_bypass_live_ambiguity_check(self):
        self.api._request.side_effect = None
        self.api._request.return_value = {'shops': [shop()], 'hasNextPage': True}
        await self.api.search_catalog('测试机厅')
        result = await self.command(mai_arcade.subscribe_arcade_handler, '订阅机厅 测试机厅', admins=['123456'])
        self.assertIn('缩小关键词', result[0])
        self.assertFalse(self.api.catalog[0].group)

    async def test_public_native_queries_need_neither_token_nor_upload_permission(self):
        self.api.config['nearcade_api_token'] = ''
        self.api.config['nearcade_upload_enabled'] = False
        self.api.config['nearcade_shop_bindings'] = []
        result = (await self.api.read_attendance(['456']))['456']
        self.assertEqual(result.source, 'nearcade')
        self.assertEqual([(row.game.key, row.person) for row in result.games], [('mai', 5), ('chu', 3)])
        self.assertTrue(all('authenticated' not in call.kwargs for call in self.api._request.await_args_list))

    async def test_unknown_cloud_count_requires_absolute_setting_before_increment(self):
        await self.subscribe()
        self.api._request.side_effect = [{'shop': shop()}, attendance(game(total=0), reported=[])]
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456+1'), self.api))
        self.assertIn('请先使用 =人数', result)
        self.assertEqual(self.api.catalog[0].person, 0)
        self.assertFalse(self.api.catalog[0].time)

    async def test_failed_report_save_restores_local_snapshot_without_post(self):
        await self.subscribe()
        original = self.api.catalog[0].model_dump()
        with patch.object(self.api.catalog, 'save_arcade', AsyncMock(return_value=False)):
            result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456+1'), self.api))
        self.assertIn('本地保存失败', result)
        self.assertEqual(self.api.catalog[0].model_dump(), original)
        self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))

    async def test_duplicate_cloud_game_ids_are_rejected_before_catalog_mutation(self):
        self.api._request.side_effect = None
        self.api._request.return_value = {'shops': [shop(games=[game(), game()])]}
        with self.assertRaises(NearcadeError):
            await self.api.search_catalog('测试')
        self.assertEqual(self.api.catalog, [])

    async def test_summary_reads_native_cloud_ids_and_remains_one_line_per_shop(self):
        await self.subscribe()
        result = await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api)
        self.assertEqual(result, ['测试机厅 舞萌5人｜中二3人'])
        self.assertTrue(all(call.args[1] != '/shops/999' for call in self.api._request.await_args_list))

    async def test_single_query_finds_unsubscribed_cloud_shop_not_in_local_catalog(self):
        self.local.clear()
        result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅chu几人'), self.api))
        self.assertIn('数据来源：nearcade', result)
        self.assertIn('中二节奏', result)
        self.assertNotIn('舞萌 DX', result)
        self.assertNotIn('没有这样的', result)
        self.assertFalse(self.path.exists())

    async def test_reports_use_live_baseline_and_cloud_game_id_without_mapping(self):
        await self.subscribe()
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456chu+1'), self.api))
        self.assertIn('当前 4 人', result.replace('人数已更新为', '当前'))
        self.assertIn('已同步 nearcade', result)
        writes = [call for call in self.api._request.await_args_list if call.args[0] == 'POST']
        self.assertEqual(writes[0].args, ('POST', '/shops/456/attendance'))
        self.assertEqual(writes[0].kwargs['payload'], {'games': [{'id': 456001, 'currentAttendances': 4}]})
        self.assertEqual(self.api.catalog[0].game_attendance['chu'].person, 4)
        self.local.save_arcade.assert_not_awaited()

    async def test_batch_reports_update_only_requested_cloud_games(self):
        await self.subscribe()
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456mai3chu2'), self.api))
        self.assertEqual(result.count('云端同步成功'), 2)
        self.assertEqual(self.counts, {'mai': 3, 'chu': 2})

    async def test_upload_disabled_keeps_report_local_and_performs_no_cloud_write(self):
        await self.subscribe()
        self.api.config['nearcade_upload_enabled'] = False
        self.api._request.reset_mock()
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='4563人'), self.api))
        self.assertIn('仅保存本地记录', result)
        self.api._request.assert_not_awaited()
        self.assertEqual(NearcadeArcadeList(self.path)[0].person, 3)
        self.api._request.side_effect = NearcadeError('offline')
        fallback = '\n'.join(await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api))
        self.assertIn('舞萌3人', fallback)

    async def test_cloud_read_failure_does_not_apply_increment_against_stale_local_count(self):
        await self.subscribe()
        self.api._request.side_effect = NearcadeError('offline')
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456chu+1'), self.api))
        self.assertIn('无法核对云端当前人数', result)
        self.assertNotIn('chu', self.api.catalog[0].game_attendance)
        self.assertEqual(self.counts['chu'], 3)

    async def test_missing_cloud_game_prevents_upload_and_local_change(self):
        await self.subscribe()
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456太鼓3人'), self.api))
        self.assertIn('云端未找到该游戏', result)
        self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))

    async def test_duplicate_game_records_get_automatic_partition_names(self):
        self.games.append(game(gameId=456002, quantity=1))
        await self.subscribe()
        self.assertEqual({item.key for item in self.api.games_for('456')}, {'mai:机台456000号', 'mai:机台456002号', 'chu'})
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456mai3人'), self.api))
        self.assertIn('mai机台456000', result)
        self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))
        await collect(mai_arcade.arcade_person_handler, event(text='456mai机台456002号3人'), self.api)
        writes = [call for call in self.api._request.await_args_list if call.args[0] == 'POST']
        self.assertEqual(writes[0].kwargs['payload']['games'][0]['id'], 456002)

    async def test_closure_is_local_only_and_never_changes_cloud_status(self):
        await self.subscribe()
        self.api._request.reset_mock()
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='456=-1'), self.api))
        self.assertIn('本地已闭店', result)
        self.api._request.assert_not_awaited()
        self.assertEqual(self.api.catalog[0].status, '闭店')

    async def test_save_failure_rolls_back_subscription_and_never_touches_local_store(self):
        with patch.object(self.api.catalog, 'save_arcade', AsyncMock(return_value=False)):
            result = await self.subscribe()
        self.assertIn('本地保存失败', result[0])
        self.assertFalse(self.api.catalog[0].group)
        self.local.save_arcade.assert_not_awaited()

    async def test_native_catalog_crud_never_mutates_local_catalog(self):
        for handler, command in ((mai_arcade.add_arcade_handler, '添加机厅 店名 地址 2'),
                                 (mai_arcade.delete_arcade_handler, '删除机厅 店名'),
                                 (mai_arcade.modify_arcade_handler, '修改机厅 店名 数量 3')):
            result = await self.command(handler, command, admins=['123456'])
            self.assertIn('请在 https://nearca.de/shops 维护', result[0])
        self.local.save_arcade.assert_not_awaited()
        self.api._request.assert_not_awaited()

    async def test_cloud_query_failure_is_labeled_as_cached_local_fallback(self):
        await self.subscribe()
        self.api._request.side_effect = NearcadeError('offline')
        result = '\n'.join(await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api))
        self.assertIn('本地·云端查询失败', result)
        self.assertNotIn('本地另一家店', result)

    async def test_bad_cloud_catalog_payload_does_not_pollute_cache(self):
        self.api._request.side_effect = None
        for payload in ({'shops': 'invalid'}, {'shops': [shop(id=999, address={'general': [None]})]}):
            self.api._request.return_value = payload
            with self.assertRaises(NearcadeError):
                await self.api.search_catalog('测试')
        self.assertEqual(self.api.catalog, [])

    async def test_main_routes_catalog_commands_and_preserves_group_gating(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.nearcade = self.api
        plugin.superusers = ['123456']
        plugin.disabled_groups = set()
        plugin.arcade_enabled_groups = {'200'}
        result = await collect(plugin.search_arcade, event(text='查找机厅 测试'))
        self.assertIn('nearcade', '\n'.join(result))
        self.api._request.reset_mock()
        plugin.disabled_groups = {'200'}
        self.assertEqual(await collect(plugin.search_arcade, event(text='查找机厅 测试')), [])
        self.api._request.assert_not_awaited()
