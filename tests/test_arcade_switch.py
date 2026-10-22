import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ..command import mai_arcade
from ..libraries.maimaidx_nearcade import NearcadeClient
from ..main import MaimaiDXPlugin


class ArcadeSwitchPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        self.plugin.arcade_switch_file = (
            Path(self.temp_dir.name) / 'enabled_arcade_groups.json'
        )
        self.plugin.arcade_enabled_groups = set()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load_enabled_groups(self):
        self.plugin.arcade_enabled_groups = {'200', '100'}
        self.plugin._save_arcade_enabled_groups()

        saved = json.loads(
            self.plugin.arcade_switch_file.read_text(encoding='utf-8')
        )
        self.assertEqual(saved, {'enabled_groups': ['100', '200']})

        self.plugin.arcade_enabled_groups = set()
        self.plugin._load_arcade_enabled_groups()
        self.assertTrue(self.plugin._is_arcade_enabled('100'))
        self.assertFalse(self.plugin._is_arcade_enabled('300'))

    def test_load_normalizes_numeric_group_ids(self):
        self.plugin.arcade_switch_file.write_text(
            json.dumps({'enabled_groups': [12345]}),
            encoding='utf-8',
        )
        self.plugin._load_arcade_enabled_groups()
        self.assertTrue(self.plugin._is_arcade_enabled('12345'))

    def test_missing_file_defaults_to_disabled(self):
        self.plugin._load_arcade_enabled_groups()
        self.assertFalse(self.plugin._is_arcade_enabled('12345'))


class ArcadeSearchSwitchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        self.plugin.config = {}
        self.plugin.superusers = []
        self.plugin.nearcade = NearcadeClient({})
        self.plugin.enabled_groups = set()
        self.plugin.disabled_groups = set()
        self.plugin.arcade_enabled_groups = set()
        self.request = SimpleNamespace(message_obj=SimpleNamespace(group_id='200'))

    @staticmethod
    async def reply(*args, **kwargs):
        yield 'handled'

    async def call(self, name='search_arcade'):
        return [message async for message in getattr(self.plugin, name)(self.request)]

    async def test_local_and_nearcade_search_work_with_queue_disabled(self):
        for source in ('local', 'nearcade'):
            self.plugin.nearcade = NearcadeClient({'arcade_data_source': source})
            with patch.object(mai_arcade, 'search_arcade_handler', Mock(side_effect=self.reply)) as handler:
                self.assertEqual(await self.call(), ['handled'])
                expected = {'nearcade': self.plugin.nearcade} if source == 'nearcade' else {}
                handler.assert_called_once_with(self.request, **expected)

    async def test_search_still_respects_whitelist_and_disabled_groups(self):
        self.plugin.config = {'maimaidxwhitelist': True}
        with patch.object(mai_arcade, 'search_arcade_handler', Mock(side_effect=self.reply)) as handler:
            self.assertEqual(await self.call(), [])
            handler.assert_not_called()
            self.plugin.enabled_groups.add('200')
            self.assertEqual(await self.call(), ['handled'])
            self.plugin.disabled_groups.add('200')
            self.assertEqual(await self.call(), [])
            self.assertEqual(handler.call_count, 1)

    async def test_search_remains_available_in_private_chat(self):
        self.request.message_obj.group_id = None
        self.plugin.config = {'maimaidxwhitelist': True}
        with patch.object(mai_arcade, 'search_arcade_handler', Mock(side_effect=self.reply)) as handler:
            self.assertEqual(await self.call(), ['handled'])
            handler.assert_called_once_with(self.request)

    async def test_private_chat_does_not_reach_queue_person_handler(self):
        self.request.message_obj.group_id = None
        with patch.object(mai_arcade, 'arcade_person_handler', Mock(side_effect=self.reply)) as handler:
            self.assertEqual(await self.call('arcade_person'), [])
            handler.assert_not_called()

    async def test_other_queue_operations_keep_their_queue_switch(self):
        for method, handler_name in (
            ('arcade_person', 'arcade_person_handler'),
            ('arcade_query_multiple', 'arcade_query_multiple_handler'),
            ('arcade_query_person', 'arcade_query_person_handler'),
            ('subscribe_arcade', 'subscribe_arcade_handler'),
            ('check_subscribe', 'check_subscribe_handler'),
        ):
            self.plugin.arcade_enabled_groups.clear()
            with patch.object(mai_arcade, handler_name, Mock(side_effect=self.reply)) as handler:
                self.assertEqual(await self.call(method), [])
                handler.assert_not_called()
                self.plugin.arcade_enabled_groups.add('200')
                self.assertEqual(await self.call(method), ['handled'])
                self.assertEqual(handler.call_count, 1)


if __name__ == '__main__':
    unittest.main()
