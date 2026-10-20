import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from ..main import MaimaiDXPlugin
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_catalog import NearcadeArcadeList
from ..command.mai_arcade_queries import query_arcade_messages
from .test_arcade_games import arcade
from .test_arcade_cloud_query import attendance
from .test_nearcade import client, shop


class DailyUpdateTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_update_refreshes_all_maimai_data(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)

        with patch(
            'data.plugins.astrbot_plugin_maimaidx.main.mai.update',
            new=AsyncMock(),
        ) as update:
            await plugin._daily_update()

        update.assert_awaited_once_with()

    async def test_arcade_daily_task_remains_scheduled_at_three(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.scheduler = Mock()
        plugin._setup_schedulers()
        call = next(call for call in plugin.scheduler.add_job.call_args_list
                    if call.kwargs['name'] == 'arcade_daily_update')
        self.assertEqual(call.args[0], plugin._arcade_daily_update)
        self.assertIn("hour='3'", str(call.kwargs['trigger']))

    async def test_local_reset_is_persisted_without_changing_shop_settings_or_closure(self):
        record = arcade()
        record.status = '闭店'
        record.group_aliases = {'200': ['天街']}
        record.game_machine_counts = {'mai': 2, 'chu': 1}
        record.game_attendance = {'chu': a.ArcadeAttendance(person=5, by='B', time='2026-10-19 12:00:00',
                                                         status='闭店', label='中二节奏')}
        before = record.model_dump()
        records = a.ArcadeList([record])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'arcades.json'
            with patch.object(a, 'arcades_json', path), patch.object(a.arcade, 'arcades', []):
                self.assertTrue(await records.reset_local_attendance())
                saved = json.loads(path.read_text(encoding='utf-8'))[0]
                self.assertEqual(a.arcade.arcades, [saved])
        self.assertEqual(saved['person'], 0)
        self.assertEqual(saved['game_attendance']['chu']['person'], 0)
        self.assertEqual(saved['by'], '系统每日清零')
        self.assertEqual(saved['time'], '')
        self.assertEqual(saved['game_attendance']['chu']['time'], '')
        for field in ('name', 'location', 'id', 'alias', 'group_aliases', 'group', 'num', 'game_machine_counts', 'status'):
            self.assertEqual(saved[field], before[field])
        self.assertEqual(saved['game_attendance']['chu']['status'], '闭店')

    async def test_empty_reset_does_not_write_and_save_failure_restores_counts(self):
        empty = a.ArcadeList()
        empty.save_arcade = AsyncMock()
        self.assertTrue(await empty.reset_local_attendance())
        empty.save_arcade.assert_not_awaited()
        for failure in (False, OSError('disk failure')):
            record = arcade()
            record.game_attendance['chu'] = a.ArcadeAttendance(person=3, by='B', time='old')
            before = record.model_dump()
            records = a.ArcadeList([record])
            records.save_arcade = (AsyncMock(side_effect=failure) if isinstance(failure, Exception)
                                   else AsyncMock(return_value=failure))
            if isinstance(failure, Exception):
                with self.assertRaises(OSError):
                    await records.reset_local_attendance()
            else:
                self.assertFalse(await records.reset_local_attendance())
            self.assertEqual(record.model_dump(), before)

    async def test_daily_refresh_and_both_local_stores_reset_without_cloud_writes(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.nearcade = client(arcade_data_source='nearcade')
        plugin.nearcade._request = AsyncMock()
        local = a.ArcadeList([arcade()])
        local.save_arcade = AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nearcade_arcades.json'
            native = NearcadeArcadeList(path)
            native.append(arcade(id='456'))
            native[0].game_attendance['chu'] = a.ArcadeAttendance(person=7, time='old')
            plugin.nearcade._catalog = native
            with (patch.object(a.arcade, 'total', local, create=True),
                  patch.object(a.arcade, 'getArcade', AsyncMock()) as refresh):
                await plugin._arcade_daily_update()
            refresh.assert_awaited_once_with()
            self.assertEqual(local[0].person, 0)
            saved = NearcadeArcadeList(path)[0]
            self.assertEqual(saved.person, 0)
            self.assertEqual(saved.game_attendance['chu'].person, 0)
            self.assertEqual(saved.group, [200])
            plugin.nearcade._request.assert_not_awaited()
            plugin.nearcade._request.side_effect = [{'shop': shop()}, attendance()]
            reply = await query_arcade_messages(native, {}, plugin.nearcade, compact=True)
            self.assertEqual(reply, ['店名：测试机厅\n  - 舞萌 DX：5人\n  - 中二节奏：3人'])
            self.assertEqual(native[0].person, 0)

    async def test_refresh_failure_still_resets_local_counts(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        records = a.ArcadeList([arcade()])
        records.save_arcade = AsyncMock(return_value=True)
        with (patch.object(a.arcade, 'total', records, create=True),
              patch.object(a.arcade, 'getArcade', AsyncMock(side_effect=RuntimeError('offline')))):
            await plugin._arcade_daily_update()
        self.assertEqual(records[0].person, 0)
        records.save_arcade.assert_awaited_once_with()

    async def test_daily_reset_waits_for_in_progress_reports(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.nearcade = client()
        records = a.ArcadeList([arcade()])
        records.save_arcade = AsyncMock(return_value=True)
        lock = plugin.nearcade.lock_for('123')
        with (patch.object(a.arcade, 'total', records, create=True),
              patch.object(a.arcade, 'getArcade', AsyncMock())):
            async with lock:
                task = asyncio.create_task(plugin._arcade_daily_update())
                await asyncio.sleep(0)
                self.assertEqual(records[0].person, 2)
                self.assertFalse(task.done())
            await task
        self.assertEqual(records[0].person, 0)


if __name__ == '__main__':
    unittest.main()
