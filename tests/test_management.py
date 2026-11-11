import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ..libraries import maimaidx_management as manage
from ..libraries import maimaidx_management_store as store
from ..libraries import maimaidx_music as music
from ..libraries import maimaidx_arcade as arcades
from ..libraries.maimaidx_arcade_catalog import NearcadeArcadeList
from ..libraries.maimaidx_model import Alias, AliasesPush, GuessSwitch
from ..main import MaimaiDXPlugin
from .test_arcade_games import arcade
from .test_nearcade import client
from .test_upstream_158 import song


class ManagementTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        def replace(target, name, value, create=False):
            context = patch.object(target, name, value, create=create)
            context.start()
            self.addCleanup(context.stop)

        import data.plugins.astrbot_plugin_maimaidx as package
        for field in ('local_alias_file', 'alias_file', 'lxns_alias_file', 'group_alias_file', 'guess_file'):
            replace(package, field, self.root / (field + '.json'))
        replace(music.mai, 'total_list', music.SongList([song(1), song(2)]))
        replace(music.mai, 'total_alias_list', music.AliasList([Alias(SongID=1, Name='Song 1', Alias=['remote'])]), create=True)
        replace(music.guess, 'switch', GuessSwitch(enable=['200']))
        replace(music.alias, 'push', AliasesPush(enable=['200']))
        replace(manage.maiApi.config, 'maimaidxaliaspush', True)
        replace(manage.maiApi.config, 'maimaidxaliaswhitelist', False)
        replace(arcades, 'arcades_json', self.root / 'arcades.json')
        self.records = arcades.ArcadeList([arcade(), arcade(id='124', name='另一家')])
        replace(arcades.arcade, 'total', self.records, create=True)
        replace(arcades.arcade, 'arcades', [])
        replace(arcades.arcade, 'idList', [123, 124])
        self.plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        self.plugin.config = {'maimaidxwhitelist': False}
        self.plugin.enabled_groups = {'200'}
        self.plugin.disabled_groups = set()
        self.plugin.arcade_enabled_groups = {'200'}
        self.plugin._group_settings_valid = True
        self.plugin.data_file = self.root / 'disabled_groups.json'
        self.plugin.arcade_switch_file = self.root / 'enabled_arcade_groups.json'
        self.plugin.nearcade = client()
        self.plugin.nearcade._catalog = NearcadeArcadeList(self.root / 'nearcade.json')
        self.plugin.nearcade._catalog.append(arcade(id='456', name='云端店'))
        self.manager = manage.MaimaiManagement(self.plugin)
        self.alias_path = package.local_alias_file
        self.source_path = package.alias_file
        self.guess_path = package.guess_file

    def group_change(self, field, enabled, group='200', **extra):
        return self.manager.change_group({'id': group, 'field': field, 'enabled': enabled,
                                          'revision': self.manager.group_row(group)['revision'], **extra})

    def arcade_change(self, action, aid='123', source='local', **extra):
        records = self.manager.records(source)
        item = next(record for record in records if record.id == aid)
        return self.manager.change_arcade({'action': action, 'source': source, 'id': aid,
            'revision': self.manager.arcade_row(item, source)['revision'], **extra})

    async def test_group_switches_persist_and_publish_without_broadcast(self):
        self.group_change('maimai', False)
        self.assertFalse(self.plugin._is_group_enabled('200'))
        self.assertIn('200', json.loads(self.plugin.data_file.read_text())['disabled_groups'])
        self.group_change('maimai', True)
        self.assertTrue(self.plugin._is_group_enabled('200'))
        self.group_change('arcade', False)
        self.assertNotIn('200', self.plugin.arcade_enabled_groups)
        with patch.object(music.guess, 'end') as end:
            self.group_change('guess', False)
            end.assert_called_once_with('200')
        self.assertIn('200', json.loads(self.guess_path.read_text())['disable'])
        self.group_change('alias', False)
        self.assertIn('200', music.alias.push.disable)

    async def test_group_effective_status_and_unknown_group_selection(self):
        self.assertTrue(self.manager.group_row('300')['effective']['maimai'])
        self.plugin.config['maimaidxwhitelist'] = True
        self.assertFalse(self.manager.group_row('300')['effective']['maimai'])
        data = self.manager.groups({'q': '300'})
        self.assertEqual([item['id'] for item in data['items']], ['300'])
        self.assertFalse(data['items'][0]['maimai'])
        self.group_change('maimai', True, '300')
        self.assertTrue(self.manager.group_row('300')['effective']['maimai'])
        with patch.object(music.alias.push, 'global_enabled', False):
            self.assertFalse(self.manager.group_row('200')['effective']['alias'])

    async def test_invalid_stale_and_failed_group_writes_do_not_publish(self):
        before = self.manager.group_row('200')
        for extra in ({'id': '../200'}, {'enabled': 'false'}, {'field': 'token'}, {'revision': 'stale'}):
            with self.assertRaises(store.ManagementError):
                self.manager.change_group({'id': '200', 'field': 'maimai', 'enabled': False,
                                          'revision': before['revision'], **extra})
        with patch.object(manage, 'write_json', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.group_change('maimai', False)
        self.assertEqual(before, self.manager.group_row('200'))
        self.plugin.data_file.write_text('{broken', encoding='utf-8')
        with self.assertRaises(store.ManagementError):
            self.group_change('maimai', False)
        self.assertEqual(self.plugin.data_file.read_text(), '{broken')

    async def test_arcade_subscriptions_alias_scope_and_conflict(self):
        await self.arcade_change('subscribe', group='300', enabled=True)
        await self.arcade_change('alias', group='300', aliases=['群别名'])
        self.assertEqual(self.records[0].group_aliases, {'300': ['群别名']})
        await self.arcade_change('alias', aliases=['全局别名'])
        self.assertEqual(self.records[0].alias, ['全局别名'])
        with self.assertRaises(store.ManagementError):
            await self.arcade_change('alias', aid='124', group='300', aliases=['群别名'])
        with self.assertRaises(store.ManagementError):
            await self.arcade_change('alias', group='400', aliases=['未订阅'])
        with self.assertRaises(store.ManagementError):
            await self.arcade_change('alias', aliases=['123'])
        await self.arcade_change('subscribe', group='300', enabled=False)
        self.assertEqual(self.records[0].group_aliases['300'], ['群别名'])
        persisted = json.loads((self.root / 'arcades.json').read_text(encoding='utf-8'))
        self.assertNotIn(300, persisted[0]['group'])

    async def test_cloud_records_are_readonly_but_local_subscriptions_can_change(self):
        for action in ('save', 'create', 'delete'):
            with self.assertRaises(store.ManagementError):
                await self.arcade_change(action, aid='456', source='nearcade', fields={}, confirm='456')
        await self.arcade_change('subscribe', aid='456', source='nearcade', group='300', enabled=True)
        await self.arcade_change('alias', aid='456', source='nearcade', group='300', aliases=['云端群别名'])
        self.assertIn('云端群别名', self.plugin.nearcade.catalog[0].group_aliases['300'])
        self.assertNotIn('云端群别名', self.records[0].alias)

    async def test_arcade_create_edit_delete_and_typed_confirmation(self):
        fields = {'name': '新机厅', 'location': '新地址', 'num': 3}
        result = await self.manager.change_arcade({'action': 'create', 'source': 'local', 'fields': fields})
        aid = result['item']['id']
        self.assertEqual(aid, '10000')
        self.assertIn(10000, arcades.arcade.idList)
        await self.arcade_change('save', aid=aid, fields={**fields, 'num': 4})
        self.assertEqual(self.records[-1].num, 4)
        with self.assertRaises(store.ManagementError):
            await self.arcade_change('delete', aid=aid, confirm='wrong')
        await self.arcade_change('delete', aid=aid, confirm=aid)
        self.assertNotIn(10000, arcades.arcade.idList)

    async def test_failed_arcade_save_rolls_back_without_changing_report(self):
        before = self.records[0].model_dump()
        with patch.object(self.records, 'save_arcade', AsyncMock(side_effect=OSError('disk full'))):
            with self.assertRaises(OSError):
                await self.arcade_change('alias', aliases=['失败别名'])
        self.assertEqual(self.records[0].model_dump(), before)
        with self.assertRaises(store.ManagementError):
            await self.arcade_change('save', fields={'name': 'x', 'location': 'y', 'num': 2, 'person': 999})

    async def test_remote_search_is_explicit_and_filtered_search_is_paginated(self):
        self.plugin.nearcade.search_catalog = AsyncMock(return_value=(list(self.plugin.nearcade.catalog), False))
        await self.manager.arcades({'source': 'nearcade'})
        self.plugin.nearcade.search_catalog.assert_not_awaited()
        data = await self.manager.arcades({'source': 'nearcade', 'remote': '1', 'q': '456', 'group': '200'})
        self.assertEqual(data['items'][0]['name'], '云端店')
        self.plugin.nearcade.search_catalog.assert_awaited_once_with('456', group_id='200')
        data = await self.manager.arcades({'source': 'local', 'subscribed': '1', 'group': '400'})
        self.assertEqual(data['total'], 0)

    async def test_local_song_alias_changes_live_search_and_persistence(self):
        row = self.manager.songs({'q': '1'})['items'][0]
        self.assertEqual(row['other'], ['remote'])
        await self.manager.change_song_alias({'id': '1', 'alias': 'NEW Alias', 'action': 'add', 'revision': row['revision']})
        self.assertTrue(music.mai.total_alias_list.by_alias('new alias'))
        self.assertEqual(json.loads(self.alias_path.read_text())['1'], ['new alias'])
        row = self.manager.songs({'q': 'new alias'})['items'][0]
        await self.manager.change_song_alias({'id': '1', 'alias': 'new alias', 'action': 'delete', 'revision': row['revision']})
        self.assertFalse(music.mai.total_alias_list.by_alias('new alias'))
        self.assertTrue(music.mai.total_alias_list.by_alias('remote'))

    async def test_alias_conflicts_corrupt_data_and_write_failure_preserve_memory(self):
        row = self.manager.songs({'q': '2'})['items'][0]
        body = {'id': '2', 'alias': 'remote', 'action': 'add', 'revision': row['revision']}
        with self.assertRaises(store.ManagementError):
            await self.manager.change_song_alias(body)
        body['alias'] = 'new'
        with patch.object(store.os, 'replace', side_effect=OSError('disk full')), self.assertRaises(OSError):
            await self.manager.change_song_alias(body)
        self.assertFalse(music.mai.total_alias_list.by_alias('new'))
        self.assertFalse(self.alias_path.exists())
        self.alias_path.write_text('{bad', encoding='utf-8')
        with self.assertRaises(store.ManagementError):
            await self.manager.change_song_alias(body)
        self.assertEqual(self.alias_path.read_text(), '{bad')

    async def test_delete_shared_local_alias_keeps_external_alias(self):
        store.write_json(self.alias_path, {'1': ['remote']})
        store.write_json(self.source_path, [{'SongID': 1, 'Alias': ['remote']}])
        row = self.manager.songs({'q': '1'})['items'][0]
        await self.manager.change_song_alias({'id': '1', 'alias': 'remote', 'action': 'delete', 'revision': row['revision']})
        self.assertTrue(music.mai.total_alias_list.by_alias('remote'))
        self.assertEqual(json.loads(self.alias_path.read_text()), {})

    async def test_chat_alias_commands_keep_existing_cross_song_alias_support(self):
        self.assertTrue(await music.update_local_alias('2', 'remote'))
        self.assertEqual({item.SongID for item in music.mai.total_alias_list.by_alias('remote')}, {1, 2})
        self.assertTrue(await music.delete_local_alias('2', 'remote'))
        self.assertEqual({item.SongID for item in music.mai.total_alias_list.by_alias('remote')}, {1})

    async def test_management_endpoints_require_dashboard_login(self):
        try:
            from ..libraries import maimaidx_resource_web as web
        except ImportError:
            self.skipTest('native web API unavailable')
        api = web.ResourceWeb(Mock(), Mock(), self.manager)
        request = SimpleNamespace(username=None, query={}, json=AsyncMock(return_value={}))
        with patch.object(web, 'request', request):
            for method in ('groups', 'change_group', 'arcades', 'change_arcade', 'songs', 'change_song_alias', 'manage_status',
                           'extra_list', 'extra_change', 'extra_detail', 'activity'):
                self.assertEqual((await getattr(api, method)()).status_code, 401)
            request.username = 'admin'
            self.assertEqual((await api.groups()).status_code, 200)
            request.json.return_value = []
            self.assertEqual((await api.change_group()).status_code, 409)


if __name__ == '__main__':
    unittest.main()
