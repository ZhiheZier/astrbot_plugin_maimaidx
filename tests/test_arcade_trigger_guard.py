import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_catalog import NearcadeArcadeList
from .test_arcade_games import arcade
from .test_nearcade import client, collect, event


class ArcadeTriggerGuardTest(unittest.IsolatedAsyncioTestCase):
    async def check_ignored(self, source, texts, handler, *, subscribed=True):
        record = arcade(id='1000')
        record.alias.append('1000')
        record.group_aliases = {'200': ['天街']}
        record.group = [200] if subscribed else []
        hidden = arcade(name='未订阅机厅', id='1001')
        hidden.alias = ['未订阅别名']
        hidden.group = []
        api = client(arcade_data_source=source)
        api._request = AsyncMock()
        api.prepare_cabinet_aliases = AsyncMock()
        api.upload_attendance = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            records = a.ArcadeList([record, hidden])
            if source == 'nearcade':
                api._catalog = NearcadeArcadeList(Path(directory) / 'catalog.json')
                api.catalog.extend(records)
            before = [item.model_dump() for item in records]
            with (patch.object(a.arcade, 'total', records, create=True),
                  patch.object(a.ArcadeList, 'save_arcade', AsyncMock()) as save):
                for text in texts:
                    with self.subTest(source=source, subscribed=subscribed, text=text):
                        self.assertEqual(await collect(handler, event(text=text), api), [])
                save.assert_not_awaited()
            self.assertEqual([item.model_dump() for item in records], before)
            api._request.assert_not_awaited()
            api.prepare_cabinet_aliases.assert_not_awaited()
            api.upload_attendance.assert_not_awaited()

    async def test_numeric_reports_and_unknown_shops_are_silent_without_side_effects(self):
        texts = ('1000-7', '1000+1', '/1000=3', '1000mai3', '1000 mai3chu2',
                 '10003人', '1000 3', '1000chu=-1', '9999+1', '１０００+1',
                 '未订阅机厅+1', '未订阅别名mai3chu2', '不存在=4', '今天吃饭3人')
        for source in ('local', 'nearcade'):
            await self.check_ignored(source, texts, mai_arcade.arcade_person_handler)

    async def test_numeric_queries_and_unknown_shops_are_silent_without_network_requests(self):
        texts = ('1000几人', '/1000mai几', '1000chujr', '１０００几人', '9999j',
                 '未订阅机厅几人', '未订阅别名chujr', '不存在几', '地址几人')
        for source in ('local', 'nearcade'):
            await self.check_ignored(source, texts, mai_arcade.arcade_query_person_handler)

    async def test_unsubscribed_groups_ignore_reports_and_single_shop_queries(self):
        for source in ('local', 'nearcade'):
            await self.check_ignored(source, ('天街+1', '测试机厅3', 'mai3chu2', '+1'),
                                     mai_arcade.arcade_person_handler, subscribed=False)
            await self.check_ignored(source, ('天街几人', '测试机厅chujr', 'chu几人'),
                                     mai_arcade.arcade_query_person_handler, subscribed=False)

    async def test_shop_names_starting_with_digits_still_work(self):
        record = arcade(name='1号机厅')
        records = a.ArcadeList([record])
        with (patch.object(a.arcade, 'total', records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            messages = await collect(mai_arcade.arcade_person_handler, event(text='1号机厅3'))
            self.assertTrue(messages)
            self.assertEqual(record.person, 3)
            messages = await collect(mai_arcade.arcade_query_person_handler, event(text='1号机厅几人'))
            self.assertIn('当前 3 人', messages[0])
