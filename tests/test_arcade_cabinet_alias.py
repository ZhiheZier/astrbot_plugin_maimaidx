import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_nearcade import NearcadeError
from ..libraries.maimaidx_arcade_catalog import NearcadeArcadeList
from .test_arcade_cloud_query import attendance
from .test_arcade_games import arcade
from .test_nearcade import client, collect, event, game, shop


class CabinetAliasTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = arcade()
        self.records = a.ArcadeList([self.record])
        self.records.save_arcade = AsyncMock(return_value=True)
        self.api = client(nearcade_shop_bindings=[
            '123=456', '123[mml]=456:456001', '123[抬手乐]=456:456002',
        ])
        self.games = [game(), game(gameId=456001, version='mml'),
                      game(gameId=456002, titleId=3, name='CHUNITHM', version='mml')]
        self.counts = {456000: 5, 456001: 2, 456002: 1}
        self.api._request = AsyncMock(side_effect=self.request)
        self.patcher = patch.object(a.arcade, 'total', self.records, create=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def request(self, method, path, **kwargs):
        if method == 'POST':
            for item in kwargs['payload']['games']:
                self.counts[item['id']] = item['currentAttendances']
            return {'success': True}
        if path == '/shops/456':
            return {'shop': shop(games=self.games)}
        if path == '/shops/456/attendance':
            return attendance(*(dict(item, total=self.counts[item['gameId']]) for item in self.games))
        raise AssertionError(path)

    async def report(self, text):
        return '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text=text), self.api))

    async def test_arbitrary_aliases_infer_game_by_id_and_update_only_that_record(self):
        for text, game_id, count in (('测试机厅mml3', 456001, 3), ('别名抬手乐4', 456002, 4)):
            result = await self.report(text)
            self.assertIn('已同步 nearcade', result)
            self.assertEqual(self.counts[game_id], count)
            self.assertEqual(self.api._request.await_args.kwargs['payload']['games'],
                             [{'id': game_id, 'currentAttendances': count}])
        aliases = self.api.games_for('123')
        self.assertEqual([(item.name, item.family_key) for item in aliases], [('mml', 'mai'), ('抬手乐', 'chu')])
        self.assertEqual(self.counts, {456000: 5, 456001: 3, 456002: 4})
        self.assertEqual(self.record.person, 2)

    async def test_batch_and_case_insensitive_aliases_use_exact_ids(self):
        result = await self.report('/别名 MML3 抬手乐4')
        self.assertIn('mml：本地更新成功', result)
        self.assertIn('抬手乐：本地更新成功', result)
        writes = [call.kwargs['payload']['games'] for call in self.api._request.await_args_list if call.args[0] == 'POST']
        self.assertEqual(writes, [[{'id': 456001, 'currentAttendances': 3}],
                                  [{'id': 456002, 'currentAttendances': 4}]])

    async def test_increments_use_the_current_exact_cloud_counter(self):
        result = await self.report('测试机厅mml+1')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.counts[456001], 3)
        self.counts[456001] = 7
        result = await self.report('测试机厅mml-1')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.counts[456001], 6)
        self.assertEqual(self.counts[456000], 5)

    async def test_general_reports_still_use_total_distribution_with_aliases_configured(self):
        result = await self.report('测试机厅mai8')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.counts, {456000: 6, 456001: 2, 456002: 1})

    async def test_alias_queries_are_exact_and_general_queries_do_not_double_count(self):
        result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅mml几人'), self.api))
        self.assertIn('mml：当前 2 人', result)
        self.assertNotIn('抬手乐', result)
        result = '\n'.join(await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api))
        self.assertEqual(result, '店名：测试机厅\n  - 舞萌：7人\n  - 中二：1人')
        result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅mai几人'), self.api))
        self.assertIn('当前 7 人', result)
        self.assertNotIn('mml：', result)

    async def test_unavailable_metadata_or_invalid_alias_does_not_change_any_count(self):
        for bindings in (['123[mml]=456'], ['123[mml]=456:999'],
                         ['123[mml]=456:456001', '123[MML]=456:456002']):
            self.api.config['nearcade_shop_bindings'] = bindings
            before = self.record.model_dump()
            result = await self.report('测试机厅mml3')
            self.assertIn('未执行', result)
            self.assertEqual(self.record.model_dump(), before)
        self.api.config['nearcade_shop_bindings'] = ['123[离线别名]=456:456001']
        self.api._request.side_effect = NearcadeError('offline')
        before = self.record.model_dump()
        result = await self.report('测试机厅离线别名3')
        self.assertIn('offline', result)
        self.assertEqual(self.record.model_dump(), before)
        self.records.save_arcade.assert_not_awaited()

    async def test_local_reports_and_saved_aliases_work_without_upload(self):
        self.api.config['nearcade_upload_enabled'] = False
        result = await self.report('测试机厅mml3')
        self.assertIn('人数已更新为 3 人', result)
        self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))
        restored = a.Arcade.model_validate(json.loads(json.dumps(self.record.model_dump())))
        saved = next(item for item in restored.known_games() if item.name == 'mml')
        self.assertEqual(saved.family_key, 'mai')
        self.assertEqual(restored.attendance_for(saved).person, 3)

    async def test_same_alias_in_other_shop_does_not_override_target_game(self):
        other = arcade(name='另一机厅', id='124')
        self.records.append(other)
        self.api.config['nearcade_shop_bindings'].append('124[mml]=456:456002')
        result = await self.report('测试机厅mml3')
        self.assertIn('已同步 nearcade', result)
        result = await self.report('另一机厅mml4')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.counts[456001], 3)
        self.assertEqual(self.counts[456002], 4)
        result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅mml几人'), self.api))
        self.assertIn('mml：当前 3 人', result)

    async def test_mapping_change_does_not_reuse_old_game_id_or_family(self):
        await self.report('测试机厅mml3')
        self.api.config['nearcade_shop_bindings'] = ['123=456', '123[mml]=456:456002']
        result = await self.report('测试机厅mml4')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.api.games_for('123')[0].family_key, 'chu')
        self.assertEqual(self.counts[456001], 3)
        self.assertEqual(self.counts[456002], 4)

    async def test_saved_alias_can_be_queried_after_restart_when_cloud_is_offline(self):
        await self.report('测试机厅mml3')
        self.api = client(nearcade_shop_bindings=self.api.config['nearcade_shop_bindings'])
        self.api._request = AsyncMock(side_effect=NearcadeError('offline'))
        result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅mml几人'), self.api))
        self.assertIn('mml：当前 3 人', result)
        self.assertIn('云端查询失败', result)

    async def test_alias_cannot_upload_without_confirming_current_cloud_counter(self):
        await self.api.prepare_cabinet_aliases(['123'])
        self.api._request.side_effect = NearcadeError('offline')
        before = self.record.model_dump()
        result = await self.report('测试机厅mml+1')
        self.assertIn('无法核对云端当前人数', result)
        self.assertEqual(self.record.model_dump(), before)
        self.records.save_arcade.assert_not_awaited()


class NativeCabinetAliasTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'nearcade_arcades.json'
        self.api = client(arcade_data_source='nearcade', nearcade_game_aliases=[
            '456[mml]=456001', '456[抬手乐]=456002',
        ], nearcade_shop_bindings=['456[不应读取]=999:999001'])
        self.api._catalog = NearcadeArcadeList(self.path)
        self.record = arcade(id='456')
        self.api.catalog.append(self.record)
        self.games = [game(version='CN1.56-A'), game(gameId=456001, version='mml'),
                      game(gameId=456002, titleId=3, name='CHUNITHM', version='mml')]
        self.counts = {456000: 5, 456001: 2, 456002: 1}
        self.api._request = AsyncMock(side_effect=self.request)

    async def request(self, method, path, **kwargs):
        if method == 'POST':
            for item in kwargs['payload']['games']:
                self.counts[item['id']] = item['currentAttendances']
            return {'success': True}
        if path == '/shops':
            matches = [shop(games=self.games)] if kwargs['params']['q'] in '测试机厅' else []
            return {'shops': matches, 'hasNextPage': False}
        if path == '/shops/456':
            return {'shop': shop(games=self.games)}
        if path == '/shops/456/attendance':
            return attendance(*(dict(item, total=self.counts[item['gameId']]) for item in self.games))
        raise AssertionError(path)

    async def report(self, text):
        return '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text=text), self.api))

    async def test_native_alias_reports_use_only_the_exact_game_id(self):
        for text, game_id, count in (('测试机厅mml3', 456001, 3), ('测试机厅抬手乐4', 456002, 4),
                                     ('/别名 MML+1', 456001, 4)):
            with self.subTest(text=text):
                result = await self.report(text)
                self.assertIn('已同步 nearcade', result)
                self.assertEqual(self.counts[game_id], count)
                self.assertEqual(self.api._request.await_args.kwargs['payload']['games'],
                                 [{'id': game_id, 'currentAttendances': count}])
        self.assertEqual(self.counts[456000], 5)

    async def test_native_alias_batch_and_family_total_reports_can_coexist(self):
        result = await self.report('测试机厅mml3抬手乐4')
        self.assertIn('云端同步成功', result)
        self.assertEqual(self.counts, {456000: 5, 456001: 3, 456002: 4})
        result = await self.report('测试机厅mai9')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.counts, {456000: 6, 456001: 3, 456002: 4})

    async def test_native_alias_query_is_exact_and_default_display_keeps_game_version(self):
        result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler,
                                         event(text='测试机厅mml几人'), self.api))
        self.assertIn('舞萌 DX（mml）：当前 2 人', result)
        self.assertNotIn('CN1.56-A', result)
        self.assertNotIn('中二', result)
        summary = '\n'.join(await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api))
        self.assertEqual(summary, '店名：测试机厅\n  - 舞萌 DX（CN1.56-A）：5人\n'
                         '  - 舞萌 DX（mml）：2人\n  - 中二节奏（mml）：1人')
        details = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅几人'), self.api))
        self.assertEqual(details.count('舞萌 DX（mml）：'), 1)
        self.assertEqual(details.count('中二节奏（mml）：'), 1)
        self.assertNotIn('抬手乐', details)
        self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))

    async def test_native_alias_is_available_again_after_restart(self):
        await self.report('测试机厅mml3')
        self.api._cabinet_aliases.clear()
        self.api._catalog = NearcadeArcadeList(self.path)
        self.record = self.api.catalog[0]
        result = await self.report('测试机厅mml+1')
        self.assertIn('已同步 nearcade', result)
        self.assertEqual(self.counts[456001], 4)

    async def test_native_alias_validates_format_scope_duplicates_and_reserved_game_names(self):
        for entries in (['456[mml]=999001'], ['456[mai]=456001'], ['456[中二]=456002'],
                        ['456[mml]=456001', '456[MML]=456002'],
                        ['456[mml]=456001', '456[另名]=456001'], ['456[mml]=456:456001']):
            with self.subTest(entries=entries):
                self.api.config['nearcade_game_aliases'] = entries
                self.api._request.reset_mock()
                before = self.record.model_dump()
                result = await self.report('测试机厅mml3')
                self.assertIn('未执行', result)
                self.assertEqual(self.record.model_dump(), before)
                self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))

    async def test_alias_config_does_not_allow_updates_to_unsubscribed_shops(self):
        self.record.group = []
        before = self.record.model_dump()
        await self.report('测试机厅mml3')
        self.assertEqual(self.record.model_dump(), before)
        self.api._request.assert_not_awaited()

    async def test_local_mode_ignores_the_native_alias_configuration(self):
        api = client(nearcade_game_aliases=['this is invalid but belongs to another source'])
        api._request = AsyncMock(side_effect=[{'shop': shop()}, {'success': True}])
        await api.upload_attendance('123', 3)
        self.assertEqual(api._request.await_args.kwargs['payload']['games'],
                         [{'id': 456000, 'currentAttendances': 3}])

    async def test_native_alias_game_type_is_not_inferred_from_its_text(self):
        self.api.config['nearcade_game_aliases'] = ['456[mai手台]=456002']
        result = await self.report('测试机厅mai手台4')
        self.assertIn('已同步 nearcade', result)
        selected = next(item for item in self.api.games_for('456') if item.name == 'mai手台')
        self.assertEqual(selected.family_key, 'chu')
        self.assertEqual(self.counts, {456000: 5, 456001: 2, 456002: 4})

    async def test_same_native_alias_in_two_shops_can_refer_to_different_games(self):
        self.api.config['nearcade_game_aliases'] = ['456[mml]=456001', '457[mml]=457001']
        self.api.catalog.append(arcade(name='另一机厅', id='457'))
        other_game = game(gameId=457001, titleId=3, version='mml')
        other_count = 2
        original = self.api._request.side_effect

        async def request(method, path, **kwargs):
            nonlocal other_count
            if path == '/shops/457/attendance':
                if method == 'POST':
                    self.assertEqual(kwargs['payload']['games'][0]['id'], 457001)
                    other_count = kwargs['payload']['games'][0]['currentAttendances']
                    return {'success': True}
                return attendance(dict(other_game, total=other_count))
            if path == '/shops/457':
                return {'shop': shop(id=457, name='另一机厅', games=[other_game])}
            return await original(method, path, **kwargs)

        self.api._request.side_effect = request
        self.assertIn('已同步 nearcade', await self.report('测试机厅mml3'))
        self.assertIn('已同步 nearcade', await self.report('另一机厅mml4'))
        self.assertEqual(self.counts[456001], 3)
        self.assertEqual(other_count, 4)
