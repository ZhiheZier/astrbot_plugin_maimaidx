import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import astrbot.api.message_components as Comp

from ..command import mai_arcade, mai_guess
from ..command.permissions import can_manage_group
from ..main import MaimaiDXPlugin


def event(text='', sender='123456', role='member', group='200'):
    return SimpleNamespace(
        message_str=text,
        message_obj=SimpleNamespace(group_id=group, message=[Comp.Plain(text)]),
        get_sender_id=lambda: sender,
        plain_result=lambda message: message,
        bot=SimpleNamespace(get_group_member_info=AsyncMock(return_value={'role': role})),
    )


async def collect(handler, request, admins):
    return [result async for result in handler(request, admins)]


class GroupPermissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_administrator_does_not_need_group_admin_role_or_member_lookup(self):
        for admins in (['123456'], [123456]):
            request = event()
            request.bot.get_group_member_info.side_effect = RuntimeError('lookup unavailable')
            self.assertTrue(await can_manage_group(request, admins))
            request.bot.get_group_member_info.assert_not_awaited()

    async def test_group_owner_and_admin_are_allowed_but_ordinary_members_are_denied(self):
        for role, expected in (('owner', True), ('admin', True), ('member', False)):
            for admins in ([], ['other-admin']):
                self.assertEqual(await can_manage_group(event(role=role), admins), expected)

    async def test_group_context_is_required_even_for_administrator(self):
        request = event(group=None)
        self.assertFalse(await can_manage_group(request, ['123456']))
        request.bot.get_group_member_info.assert_not_awaited()

    async def test_lookup_failure_does_not_allow_non_administrator(self):
        request = event()
        request.bot.get_group_member_info.side_effect = RuntimeError('lookup unavailable')
        with patch('astrbot.api.logger.warning'):
            self.assertFalse(await can_manage_group(request, []))


class GroupCommandPermissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_administrator_can_reset_guess_without_group_admin_role(self):
        request = event('重置猜歌')
        with (patch.object(mai_guess.guess, 'Group', {'200': object()}),
              patch.object(mai_guess.guess, 'end') as end):
            self.assertEqual(await collect(mai_guess.reset_guess_handler, request, ['123456']), ['已重置该群猜歌'])
            end.assert_called_once_with('200')
        request.bot.get_group_member_info.assert_not_awaited()

    async def test_guess_switch_works_for_administrator_and_group_admin(self):
        for role, admins in (('member', ['123456']), ('admin', [])):
            for command, function in (('开启mai猜歌', 'on'), ('关闭mai猜歌', 'off')):
                with patch.object(mai_guess.guess, function, AsyncMock(return_value='ok')) as change:
                    self.assertEqual(await collect(mai_guess.guess_on_off_handler, event(command, role=role), admins), ['ok'])
                    change.assert_awaited_once_with('200')

    async def test_administrator_can_modify_and_subscribe_arcade(self):
        total = SimpleNamespace(search_fullname=lambda name: [object()])
        for handler, text, function, args in (
            (mai_arcade.modify_arcade_handler, '修改机厅 店名 数量 2', 'updata_arcade', ('店名', '2')),
            (mai_arcade.subscribe_arcade_handler, '订阅机厅 店名', 'subscribe', (200, '店名', True)),
            (mai_arcade.subscribe_arcade_handler, '取消订阅机厅 店名', 'subscribe', (200, '店名', False)),
        ):
            request = event(text)
            with (patch.object(mai_arcade.arcade, 'total', total, create=True),
                  patch.object(mai_arcade, function, AsyncMock(return_value='ok')) as change):
                self.assertEqual(await collect(handler, request, ['123456']), ['ok'])
                change.assert_awaited_once_with(*args)
                request.bot.get_group_member_info.assert_not_awaited()

    async def test_ordinary_member_is_rejected_before_any_mutation(self):
        for handler, text, owner, function in (
            (mai_guess.reset_guess_handler, '重置猜歌', mai_guess.guess, 'end'),
            (mai_guess.guess_on_off_handler, '开启mai猜歌', mai_guess.guess, 'on'),
            (mai_arcade.modify_arcade_handler, '修改机厅 店名 数量 2', mai_arcade, 'updata_arcade'),
            (mai_arcade.subscribe_arcade_handler, '订阅机厅 店名', mai_arcade, 'subscribe'),
        ):
            with patch.object(owner, function) as change:
                result = await collect(handler, event(text), [])
                self.assertIn('仅允许管理员或群管理员', result[0])
                change.assert_not_called()

    async def test_group_admin_does_not_gain_administrator_only_commands(self):
        for handler, text in ((mai_arcade.add_arcade_handler, '添加机厅 店名 地址 2'),
                               (mai_arcade.delete_arcade_handler, '删除机厅 店名')):
            result = await collect(handler, event(text, role='admin'), [])
            self.assertIn('仅允许管理员', result[0])

    async def test_main_passes_administrator_config_to_all_group_management_handlers(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.superusers = ['123456']
        plugin.disabled_groups = set()
        plugin.arcade_enabled_groups = {'200'}

        async def results():
            yield 'ok'

        for method, module, handler, text in (
            ('reset_guess', mai_guess, 'reset_guess_handler', '重置猜歌'),
            ('guess_on_off', mai_guess, 'guess_on_off_handler', '开启mai猜歌'),
            ('modify_arcade', mai_arcade, 'modify_arcade_handler', '修改机厅 店名 数量 2'),
            ('subscribe_arcade', mai_arcade, 'subscribe_arcade_handler', '订阅机厅 店名'),
        ):
            request = event(text)
            with patch.object(module, handler, Mock(side_effect=lambda *args: results())) as handle:
                result = [item async for item in getattr(plugin, method)(request)]
                self.assertEqual(result, ['ok'])
                handle.assert_called_once_with(request, plugin.superusers)
