import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp

from ..command import mai_base
from ..main import MaimaiDXPlugin


def event(text='', group='200', sender='123456'):
    return SimpleNamespace(
        message_str=text,
        message_obj=SimpleNamespace(group_id=group, message=[Comp.Plain(text)]),
        get_sender_id=lambda: sender,
        plain_result=lambda message: message,
        chain_result=lambda chain: chain,
    )


class FeatureWhitelistTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        self.plugin.config = {'maimaidxwhitelist': True}
        self.plugin.superusers = ['123456']
        self.plugin.enabled_groups = set()
        self.plugin.disabled_groups = set()
        self.plugin.data_file = Path(self.directory.name) / 'disabled_groups.json'

    async def toggle(self, text, **kwargs):
        return [item async for item in self.plugin.toggle_maimai_feature(event(text, **kwargs))]

    async def test_enable_close_and_reenable_persist_both_lists(self):
        self.assertFalse(self.plugin._is_group_enabled('200'))
        self.assertIn('已开启', (await self.toggle('/开启舞萌功能'))[0])
        self.assertTrue(self.plugin._is_group_enabled(200))
        self.assertEqual(json.loads(self.plugin.data_file.read_text(encoding='utf-8')),
                         {'disabled_groups': [], 'enabled_groups': ['200']})
        self.assertIn('已关闭', (await self.toggle('/关闭舞萌功能'))[0])
        self.assertFalse(self.plugin._is_group_enabled('200'))
        self.plugin._load_disabled_groups()
        self.assertEqual(self.plugin.enabled_groups, set())
        self.assertEqual(self.plugin.disabled_groups, {'200'})
        await self.toggle('开启舞萌功能')
        self.plugin._load_disabled_groups()
        self.assertTrue(self.plugin._is_group_enabled('200'))

    async def test_default_mode_preserves_blacklist_and_tracks_explicit_enables(self):
        self.plugin.config = {}
        self.assertTrue(self.plugin._is_group_enabled('200'))
        await self.toggle('开启舞萌功能')
        self.plugin.config['maimaidxwhitelist'] = True
        self.assertTrue(self.plugin._is_group_enabled('200'))
        self.assertFalse(self.plugin._is_group_enabled('300'))
        self.plugin.disabled_groups.add('200')
        self.assertFalse(self.plugin._is_group_enabled('200'))

    async def test_legacy_data_and_malformed_whitelist_are_handled(self):
        self.plugin.data_file.write_text('{"disabled_groups":[300]}', encoding='utf-8')
        self.plugin._load_disabled_groups()
        self.assertTrue(self.plugin._group_settings_valid)
        self.assertFalse(self.plugin._is_group_enabled('200'))
        self.assertEqual(self.plugin.disabled_groups, {'300'})
        self.plugin.data_file.write_text('{"enabled_groups":[200,"400"]}', encoding='utf-8')
        self.plugin._load_disabled_groups()
        self.assertTrue(self.plugin._is_group_enabled('200'))
        self.assertTrue(self.plugin._is_group_enabled('400'))
        for data in ('{"enabled_groups":true}', '{"enabled_groups":[{}]}'):
            self.plugin.data_file.write_text(data, encoding='utf-8')
            self.plugin._load_disabled_groups()
            self.assertFalse(self.plugin._group_settings_valid)
            self.assertFalse(self.plugin._is_group_enabled('200'))

    async def test_only_administrators_can_change_group_state(self):
        self.assertIn('仅允许管理员', (await self.toggle('开启舞萌功能', sender='other'))[0])
        self.assertIn('仅在群聊', (await self.toggle('开启舞萌功能', group=None))[0])
        self.assertFalse(self.plugin.data_file.exists())

    async def test_help_gates_groups_but_allows_private_chat_and_routes_separately(self):
        calls = []

        async def generated(request):
            calls.append('new')
            yield 'new'

        async def legacy(request):
            calls.append('old')
            yield 'old'

        with (patch.object(mai_base, 'maimaidxhelp_handler', side_effect=generated),
              patch.object(mai_base, 'legacy_help_handler', side_effect=legacy)):
            for handler, expected in ((self.plugin.maihelp, 'new'), (self.plugin.maimaidxhelp, 'old')):
                self.assertEqual([item async for item in handler(event())], [])
                self.assertEqual([item async for item in handler(event(group=None))], [expected])
                self.plugin.enabled_groups.add('200')
                self.assertEqual([item async for item in handler(event())], [expected])
                self.plugin.enabled_groups.clear()
        self.assertEqual(calls, ['new', 'new', 'old', 'old'])
