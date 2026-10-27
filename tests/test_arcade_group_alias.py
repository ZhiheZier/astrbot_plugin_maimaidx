import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_catalog import NearcadeArcadeList
from ..libraries.maimaidx_nearcade import CatalogShop, NearcadeError
from ..main import MaimaiDXPlugin
from .test_arcade_games import arcade
from .test_help_image import command_decorators
from .test_nearcade import client, collect, event, game, shop


class ArcadeGroupAliasTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.first, self.second = arcade(name='一号机厅'), arcade(name='二号机厅', id='124')
        for record in (self.first, self.second):
            record.alias = []
            record.group = [200, 300]
        self.records = a.ArcadeList([self.first, self.second])
        self.records.save_arcade = AsyncMock(return_value=True)
        self.patcher = patch.object(a.arcade, 'total', self.records, create=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.api = client(nearcade_upload_enabled=False, nearcade_shop_bindings=[])
        self.api.read_attendance = AsyncMock(return_value={})

    async def change(self, text='添加群机厅别名 123 天街', group='200', admins=None):
        admins = ['123456'] if admins is None else admins
        return [message async for message in mai_arcade.group_arcade_alias_handler(
            event(text=text, group=group), admins, nearcade=self.api,
        )]

    async def test_add_by_id_does_not_modify_global_aliases_subscriptions_or_other_groups(self):
        self.first.alias = ['全局别名']
        result = await self.change()
        self.assertEqual(result, ['机厅：一号机厅（123）\n已添加本群别名：天街'])
        self.assertEqual(self.first.group_aliases, {'200': ['天街']})
        self.assertEqual(self.first.alias, ['全局别名'])
        self.assertEqual(self.first.group, [200, 300])
        self.assertEqual(self.second.group_aliases, {})

    async def test_add_requires_subscription_to_the_target_shop_in_the_current_group(self):
        self.first.group = [300]
        result = await self.change()
        self.assertIn('本群尚未订阅该机厅', result[0])
        self.assertIn('订阅机厅 123', result[0])
        self.assertEqual(self.first.group_aliases, {})
        self.records.save_arcade.assert_not_awaited()

    async def test_add_accepts_full_name_and_optional_command_prefix(self):
        self.first.name = '测试 店名'
        result = await self.change('/添加群机厅别名 测试 店名 天街')
        self.assertIn('已添加', result[0])
        self.assertEqual(self.first.group_aliases['200'], ['天街'])

    async def test_same_alias_can_point_to_different_shops_in_different_groups(self):
        await self.change()
        await self.change('添加群机厅别名 124 天街', group='300')
        self.assertEqual(a.match_arcades(self.records, '天街', 200), [self.first])
        self.assertEqual(a.match_arcades(self.records, '天街', 300), [self.second])
        self.assertEqual(a.match_arcades(self.records, '天街', 400), [])
        self.assertEqual(a.match_arcades(self.records, '天街'), [])

    async def test_member_and_private_chat_are_rejected_before_saving(self):
        for admins, group in (([], '200'), (['123456'], None)):
            result = await self.change(admins=admins, group=group)
            self.assertTrue('仅允许' in result[0] or '仅在群聊' in result[0])
        self.records.save_arcade.assert_not_awaited()

    async def test_group_admin_can_manage_alias_without_bot_admin(self):
        request = event(text='添加群机厅别名 123 天街')
        request.bot.get_group_member_info.return_value = {'role': 'admin'}
        result = [message async for message in mai_arcade.group_arcade_alias_handler(request, [], nearcade=self.api)]
        self.assertIn('已添加', result[0])

    async def test_duplicate_and_same_group_collision_do_not_overwrite_alias(self):
        await self.change()
        self.records.save_arcade.reset_mock()
        self.assertIn('请勿重复添加', (await self.change())[0])
        self.assertIn('已指向', (await self.change('添加群机厅别名 124 天街'))[0])
        self.records.save_arcade.assert_not_awaited()
        self.assertEqual(self.second.group_aliases, {})

    async def test_rejects_numeric_reserved_long_aliases_and_other_shop_name(self):
        for alias in ('123', '机厅', '长' * 41, self.second.name):
            await self.change(f'添加群机厅别名 123 {alias}')
        self.records.save_arcade.assert_not_awaited()
        self.assertEqual(self.first.group_aliases, {})

    async def test_delete_is_scoped_and_retains_same_named_global_alias(self):
        self.first.alias = ['天街']
        await self.change()
        await self.change(group='300')
        result = await self.change('删除群机厅别名 123 天街')
        self.assertIn('已删除本群别名', result[0])
        self.assertEqual(self.first.group_aliases, {'300': ['天街']})
        self.assertEqual(self.first.alias, ['天街'])
        self.assertEqual(a.match_arcades(self.records, '天街', 200), [self.first])

    async def test_failed_save_rolls_back_only_group_alias_change(self):
        self.first.group_aliases = {'300': ['其他群']}
        self.records.save_arcade.return_value = False
        result = await self.change()
        self.assertIn('本地保存失败', result[0])
        self.assertEqual(self.first.group_aliases, {'300': ['其他群']})

    async def test_alias_is_available_for_search_subscription_and_detailed_query_only_in_own_group(self):
        await self.change()
        self.first.group = []
        result = [message async for message in mai_arcade.subscribe_arcade_handler(
            event(text='订阅机厅 天街'), ['123456'],
        )]
        self.assertIn('已添加订阅', result[0])
        for handler, text in ((mai_arcade.search_arcade_handler, '查找机厅 天街'),
                              (mai_arcade.arcade_query_person_handler, '天街几人')):
            arguments = (self.api,) if handler == mai_arcade.arcade_query_person_handler else ()
            own = '\n'.join(await collect(handler, event(text=text), *arguments))
            other = '\n'.join(await collect(handler, event(text=text, group='300'), *arguments))
            self.assertIn(self.first.name, own)
            if handler == mai_arcade.arcade_query_person_handler:
                self.assertEqual(other, '')
            else:
                self.assertIn('没有这样的机厅', other)
        self.assertEqual(self.first.group, [200])

    async def test_single_and_batch_reports_use_group_alias_with_game_suffixes(self):
        await self.change()
        await collect(mai_arcade.arcade_person_handler, event(text='天街3人'), self.api)
        self.assertEqual(self.first.person, 3)
        for text, count in (('天街4', 4), ('天街mai3', 3)):
            result = await collect(mai_arcade.arcade_person_handler, event(text=text), self.api)
            self.assertTrue(result)
            self.assertEqual(self.first.person, count)
        await collect(mai_arcade.arcade_person_handler, event(text='天街chu=4'), self.api)
        self.assertEqual(self.first.game_attendance['chu'].person, 4)
        await collect(mai_arcade.arcade_person_handler, event(text='天街mai5chu2'), self.api)
        self.assertEqual((self.first.person, self.first.game_attendance['chu'].person), (5, 2))
        self.assertEqual(self.second.person, 2)
        before = self.first.model_dump()
        await collect(mai_arcade.arcade_person_handler, event(text='天街+1', group='300'), self.api)
        self.assertEqual(self.first.model_dump(), before)

    async def test_all_count_query_suffixes_work_with_name_and_group_alias(self):
        await self.change()
        patterns = command_decorators()
        suffixes = ('有多少人', '有几人', '有几卡', '多少人', '多少卡', '几人', '几卡', '有几', '几', 'jr', 'j')
        self.records.save_arcade.reset_mock()
        expected = await collect(mai_arcade.arcade_query_person_handler, event(text='天街几人'), self.api)
        for name in (self.first.name, '天街'):
            for suffix in suffixes:
                for prefix in ('', '/'):
                    text = prefix + name + suffix
                    with self.subTest(text=text):
                        self.assertIsNotNone(re.fullmatch(patterns['arcade_query_person'][1], text))
                        result = await collect(mai_arcade.arcade_query_person_handler, event(text=text), self.api)
                        self.assertEqual(result, expected)
        self.records.save_arcade.assert_not_awaited()

    async def test_short_count_suffixes_preserve_game_selection(self):
        await self.change()
        for suffix in ('几', 'j', 'jr', '有几'):
            result = '\n'.join(await collect(mai_arcade.arcade_query_person_handler, event(text='天街chu' + suffix), self.api))
            self.assertIn('中二节奏', result)
            self.assertNotIn('舞萌 DX', result)

    async def test_unsubscribed_group_alias_never_falls_back_to_another_shop_global_alias(self):
        await self.change()
        self.first.group = []
        self.second.alias = ['天街']
        for text in ('天街+1', '天街3人', '天街3', '天街mai3', '天街mai3chu2'):
            await collect(mai_arcade.arcade_person_handler, event(text=text), self.api)
        self.assertEqual(self.first.person, 2)
        self.assertEqual(self.second.person, 2)
        self.assertEqual(self.second.game_attendance, {})

    async def test_group_alias_shadows_global_alias_in_current_group_only(self):
        await self.change()
        self.second.alias = ['天街']
        await collect(mai_arcade.arcade_person_handler, event(text='天街+1'), self.api)
        self.assertEqual((self.first.person, self.second.person), (3, 2))
        await collect(mai_arcade.arcade_person_handler, event(text='天街+1', group='300'), self.api)
        self.assertEqual((self.first.person, self.second.person), (3, 3))

    async def test_subscription_view_never_displays_other_group_aliases(self):
        await self.change()
        await self.change('添加群机厅别名 123 其他群秘密简称', group='300')
        replies = await collect(mai_arcade.check_subscribe_handler, event(text='查看订阅'))
        self.assertEqual(len(replies), 1)
        result = replies[0]
        self.assertIn('本群别名：天街', result)
        self.assertNotIn('其他群秘密简称', result)

    async def test_concurrent_same_group_alias_additions_cannot_bind_two_shops(self):
        results = await asyncio.gather(self.change(), self.change('添加群机厅别名 124 天街'))
        self.assertEqual(sum('已添加' in result[0] for result in results), 1)
        self.assertEqual(len(a.match_arcades(self.records, '天街', 200)), 1)

    def test_group_alias_json_round_trip_and_old_record_compatibility(self):
        self.first.group_aliases = {'200': ['天街'], '300': ['金沙']}
        restored = a.Arcade.model_validate_json(self.first.model_dump_json())
        self.assertEqual(restored.group_aliases, self.first.group_aliases)
        legacy = self.first.model_dump()
        legacy.pop('group_aliases')
        self.assertEqual(a.Arcade.model_validate(legacy).group_aliases, {})

    async def test_main_route_obeys_group_feature_and_arcade_switches(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.superusers = ['123456']
        plugin.nearcade = self.api
        plugin.disabled_groups = set()
        plugin.arcade_enabled_groups = {'200'}
        self.assertIn('已添加', (await collect(plugin.group_arcade_alias, event(text='添加群机厅别名 123 天街')))[0])
        plugin.disabled_groups.add('200')
        self.assertEqual(await collect(plugin.group_arcade_alias, event(text='删除群机厅别名 123 天街')), [])
        plugin.disabled_groups.clear()
        plugin.arcade_enabled_groups.clear()
        self.assertEqual(await collect(plugin.group_arcade_alias, event(text='删除群机厅别名 123 天街')), [])


class NearcadeGroupAliasTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'cloud.json'
        self.api = client(arcade_data_source='nearcade', nearcade_upload_enabled=False, nearcade_shop_bindings=[])
        self.api._catalog = NearcadeArcadeList(self.path)
        self.api._request = AsyncMock(return_value={'shop': shop()})
        record = self.api.catalog.remember(CatalogShop.model_validate(shop()), [])
        record.group = [200, 300]

    async def change(self, command='添加群机厅别名 456 天街', group='200'):
        return [message async for message in mai_arcade.group_arcade_alias_handler(
            event(text=command, group=group), ['123456'], nearcade=self.api,
        )]

    async def test_native_group_alias_persists_for_subscribed_shop_and_resolves_cloud_id(self):
        self.assertIn('已添加', (await self.change())[0])
        self.api._catalog = NearcadeArcadeList(self.path)
        self.assertEqual(self.api.catalog[0].group, [200, 300])
        self.assertEqual(self.api.catalog[0].group_aliases, {'200': ['天街']})
        self.api._request.reset_mock()
        records = await self.api.find_catalog('天街', group_id='200')
        self.assertEqual(records[0].id, '456')
        self.api._request.assert_awaited_once_with('GET', '/shops/456')
        self.api._request.reset_mock()
        self.api._request.return_value = {'shops': [], 'hasNextPage': False}
        self.assertEqual(await self.api.find_catalog('天街', group_id='300'), [])
        self.assertEqual(await self.api.find_catalog('天街'), [])
        self.assertTrue(all(call.args[1] == '/shops' for call in self.api._request.await_args_list))

    async def test_native_add_rejects_unsubscribed_shop_without_querying_or_saving(self):
        self.api.catalog[0].group = [300]
        result = await self.change()
        self.assertIn('本群尚未订阅该机厅', result[0])
        self.api._request.assert_not_awaited()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.api.catalog[0].group_aliases, {})

    async def test_native_add_rechecks_subscription_after_cloud_metadata_refresh(self):
        async def refresh(*args, **kwargs):
            self.api.catalog[0].group = []
            return {'shop': shop()}

        self.api._request.side_effect = refresh
        result = await self.change()
        self.assertIn('本群尚未订阅该机厅', result[0])
        self.assertEqual(self.api.catalog[0].group_aliases, {})
        self.assertFalse(self.path.exists())

    async def test_native_search_subscribe_and_metadata_refresh_keep_scoped_alias(self):
        await self.change()
        records, _ = await self.api.search_catalog('天街', group_id='200')
        self.assertEqual(records[0].id, '456')
        self.api.catalog[0].group.remove(200)
        result = [message async for message in mai_arcade.subscribe_arcade_handler(
            event(text='订阅机厅 天街'), ['123456'], nearcade=self.api,
        )]
        self.assertIn('订阅添加成功', result[0])
        self.assertEqual(set(self.api.catalog[0].group), {200, 300})
        self.api.catalog[0].group_aliases['300'] = ['其他群秘密简称']
        result = [message async for message in mai_arcade.check_subscribe_handler(event(text='查看订阅'), nearcade=self.api)]
        self.assertIn('本群别名：天街', '\n'.join(result))
        self.assertNotIn('其他群秘密简称', '\n'.join(result))

    async def test_native_delete_works_offline_and_removes_only_current_group(self):
        await self.change()
        await self.change(group='300')
        self.api.catalog[0].group.remove(200)
        self.api._request.reset_mock()
        self.api._request.side_effect = NearcadeError('offline')
        self.assertIn('已删除', (await self.change('删除群机厅别名 456 天街'))[0])
        self.api._request.assert_not_awaited()
        self.assertEqual(NearcadeArcadeList(self.path)[0].group_aliases, {'300': ['天街']})

    async def test_native_group_alias_is_used_in_multigame_reports_without_upload(self):
        await self.change()
        self.api.catalog[0].group = [200]
        result = '\n'.join(await collect(mai_arcade.arcade_person_handler, event(text='天街mai3chu2'), self.api))
        self.assertIn('本地更新成功', result)
        record = self.api.catalog[0]
        self.assertEqual((record.person, record.game_attendance['chu'].person), (3, 2))
        self.assertFalse(any(call.args[0] == 'POST' for call in self.api._request.await_args_list))
