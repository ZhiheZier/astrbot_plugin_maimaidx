import re
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_messages import split_arcade_query_messages
from ..libraries.maimaidx_nearcade import NearcadeError
from .test_arcade_games import arcade
from .test_help_image import command_decorators
from .test_nearcade import client, collect, event, game, shop


def attendance(*games, reported=None):
    if not games:
        games = (game(total=5), game(gameId=456001, titleId=3, name='中二节奏', quantity=2, total=3))
    if reported is None:
        stamp = datetime.now(timezone.utc).isoformat()
        reported = [{'gameId': item['gameId'], 'currentAttendances': item['total'],
                     'reportedAt': stamp, 'reporter': {'displayName': '云端玩家'}} for item in games]
    return {'success': True, 'total': 999, 'games': list(games), 'reported': reported, 'registered': []}


class ArcadeCloudReadTest(unittest.IsolatedAsyncioTestCase):
    def api(self, **changes):
        api = client(nearcade_api_token='', nearcade_upload_enabled=False, **changes)
        api._request = AsyncMock(side_effect=[{'shop': shop(name='云端新店名')}, attendance()])
        return api

    async def test_reads_public_per_game_counts_and_quantities_without_token_or_upload_switch(self):
        api = self.api()
        result = (await api.read_attendance(['123']))['123']
        self.assertEqual(result.name, '云端新店名')
        self.assertEqual(result.source, 'nearcade')
        self.assertEqual([(row.game.key, row.person, row.machines) for row in result.games], [('mai', 5, 2), ('chu', 3, 2)])
        self.assertTrue(all(row.by == '云端玩家' for row in result.games))
        self.assertEqual([call.args for call in api._request.await_args_list],
                         [('GET', '/shops/456'), ('GET', '/shops/456/attendance')])
        self.assertTrue(all('authenticated' not in call.kwargs for call in api._request.await_args_list))
        self.assertNotIn(999, [row.person for row in result.games])

    async def test_unmapped_shops_are_not_guessed_by_name_and_never_make_requests(self):
        api = self.api(nearcade_shop_bindings=[])
        self.assertEqual(await api.read_attendance(['123']), {})
        api._request.assert_not_awaited()

    async def test_multiple_local_mappings_fetch_the_shared_cloud_shop_only_once(self):
        api = self.api(nearcade_shop_bindings=['123=456', '124=456'])
        results = await api.read_attendance(['123', '124', '123'])
        self.assertEqual(set(results), {'123', '124'})
        self.assertEqual(api._request.await_count, 2)

    async def test_partitions_are_not_duplicated_in_the_base_game_total(self):
        api = self.api(nearcade_shop_bindings=[
            '123=456', '123[maiA区]=456:456000', '123[maiB区]=456:456001',
        ])
        api._request.side_effect = [{'shop': shop()}, attendance(
            game(total=5), game(gameId=456001, total=2), game(gameId=456002, titleId=3, total=3),
        )]
        result = (await api.read_attendance(['123']))['123']
        self.assertEqual([(row.game.key, row.person) for row in result.games], [('mai:a区', 5), ('mai:b区', 2), ('chu', 3)])
        self.assertEqual(api._request.await_count, 2)

    async def test_unpartitioned_same_family_rows_are_combined_for_reading_only(self):
        api = self.api()
        api._request.side_effect = [{'shop': shop()}, attendance(game(total=5), game(gameId=456001, total=2, quantity=1))]
        result = (await api.read_attendance(['123']))['123']
        self.assertEqual(len(result.games), 1)
        self.assertEqual((result.games[0].person, result.games[0].machines), (7, 3))
        self.assertIn('多记录汇总', result.games[0].by)

    async def test_no_report_is_not_faked_as_zero_but_explicit_zero_is_preserved(self):
        api = self.api()
        api._request.side_effect = [{'shop': shop()}, attendance(game(total=0), reported=[])]
        result = (await api.read_attendance(['123']))['123']
        self.assertIsNone(result.games[0].person)
        api._request.side_effect = [{'shop': shop()}, attendance(game(total=0))]
        result = (await api.read_attendance(['123']))['123']
        self.assertEqual(result.games[0].person, 0)

    async def test_positive_registered_count_remains_readable_without_public_reporter(self):
        api = self.api()
        api._request.side_effect = [{'shop': shop()}, attendance(game(total=2), reported=[])]
        row = (await api.read_attendance(['123']))['123'].games[0]
        self.assertEqual(row.person, 2)
        self.assertIn('登记数据', row.by)
        self.assertTrue(row.time)

    async def test_old_timestamps_and_closed_shop_status_are_preserved(self):
        api = self.api()
        payload = attendance(game(total=0))
        payload['reported'][0]['reportedAt'] = '2020-01-01T01:00:00Z'
        api._request.side_effect = [{'shop': shop(isOpen=False)}, payload]
        row = (await api.read_attendance(['123']))['123'].games[0]
        self.assertEqual(row.status, '闭店')
        self.assertTrue(row.time.startswith('2020-01-01'))

    async def test_missing_named_mapping_and_duplicate_targets_do_not_produce_wrong_totals(self):
        for bindings in (['123=456', '123=789'],
                         ['123[maiA区]=456:456000', '123[maiB区]=456:456000']):
            api = self.api(nearcade_shop_bindings=bindings)
            self.assertIsInstance((await api.read_attendance(['123']))['123'], NearcadeError)
            api._request.assert_not_awaited()
        api = self.api(nearcade_shop_bindings=['123[maiA区]=456:789000'])
        self.assertIsInstance((await api.read_attendance(['123']))['123'], NearcadeError)

    async def test_bad_cloud_responses_fail_closed_without_returning_zero(self):
        for payload in ({}, {'success': False}, attendance(game(total=-1)), attendance(game(total=True)),
                        attendance(game(total=3), game(total=3)), attendance(game(total=1, quantity='wrong'))):
            api = self.api()
            api._request.side_effect = [{'shop': shop()}, payload]
            result = (await api.read_attendance(['123']))['123']
            self.assertIsInstance(result, NearcadeError)


class ArcadeCloudQueryHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_one_line_per_shop_and_details_use_live_quantities_without_saving(self):
        record, other = arcade(), arcade(name='本地另一机厅', id='124')
        record.num = 9
        other.group = [200]
        before = [record.model_dump(), other.model_dump()]
        api = client(nearcade_upload_enabled=False)
        api._request = AsyncMock(side_effect=[{'shop': shop(name='云端新店名')}, attendance()])
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record, other]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock()) as save):
            result = (await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), api))[0]
            self.assertEqual(result.splitlines(), ['云端新店名 舞萌5人｜中二3人', '本地另一机厅（本地） 舞萌2人'])
            self.assertNotIn('预计等待', result)
            api._request.side_effect = [{'shop': shop(name='云端新店名')}, attendance()]
            details = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅几人'), api))[0]
            self.assertIn('数据来源：nearcade', details)
            self.assertIn('机台数量：2 台', details)
            self.assertNotIn('机台数量：9 台', details)
            self.assertIn('每轮时间：12 分钟', details)
            self.assertIn('预计等待：约 6 分钟', details)
            save.assert_not_awaited()
        self.assertEqual([record.model_dump(), other.model_dump()], before)
        self.assertTrue(all(call.args[0] == 'GET' for call in api._request.await_args_list))

    async def test_failed_shop_falls_back_with_label_without_blocking_other_shops(self):
        records = a.ArcadeList([arcade(), arcade(name='另一机厅', id='124')])
        api = client(nearcade_shop_bindings=['123=456', '124=789'])
        async def request(method, path, **kwargs):
            if path == '/shops/456':
                raise NearcadeError('云端连接超时')
            if path == '/shops/789':
                return {'shop': shop(id=789, name='另一机厅')}
            return attendance()
        api._request = AsyncMock(side_effect=request)
        with patch.object(mai_arcade.arcade, 'total', records, create=True):
            result = (await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), api))[0]
            self.assertIn('测试机厅（本地·云端查询失败） 舞萌2人', result)
            self.assertIn('另一机厅 舞萌5人｜中二3人', result)
            details = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅几人'), api))[0]
            self.assertIn('云端查询失败：云端连接超时', details)

    async def test_invalid_configuration_isolated_to_affected_local_shop(self):
        records = a.ArcadeList([arcade(), arcade(name='另一机厅', id='124')])
        api = client(nearcade_shop_bindings=['123[unknown]=456:1'])
        api._request = AsyncMock()
        with patch.object(mai_arcade.arcade, 'total', records, create=True):
            result = (await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), api))[0]
        self.assertIn('测试机厅（本地·云端查询失败）', result)
        self.assertIn('另一机厅（本地）', result)
        api._request.assert_not_awaited()

    async def test_specific_game_query_filters_cloud_rows_and_respects_group_subscriptions(self):
        record, hidden = arcade(), arcade(name='未订阅机厅', id='125')
        hidden.group = []
        api = client(nearcade_shop_bindings=['123=456', '125=789'])
        api._request = AsyncMock(side_effect=[{'shop': shop()}, attendance()])
        with patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record, hidden]), create=True):
            details = (await collect(mai_arcade.arcade_query_person_handler, event(text='chu几人'), api))[0]
        self.assertIn('中二节奏：当前 3 人', details)
        self.assertNotIn('舞萌 DX', details)
        self.assertFalse(any('/789' in call.args[1] for call in api._request.await_args_list))

    def test_summary_route_is_not_also_matched_as_a_detailed_query(self):
        patterns = command_decorators()
        for command in ('机厅几人', 'jtj'):
            for prefix in ('', '/'):
                text = prefix + command
                self.assertIsNotNone(re.fullmatch(patterns['arcade_query_multiple'][1], text))
                self.assertIsNone(re.fullmatch(patterns['arcade_query_person'][1], text))
        for command in ('机厅几', '机厅j', '机厅jr'):
            for prefix in ('', '/'):
                text = prefix + command
                self.assertIsNone(re.fullmatch(patterns['arcade_query_multiple'][1], text))
                self.assertIsNone(re.fullmatch(patterns['arcade_query_person'][1], text))
        for suffix in ('有多少人', '有几人', '有几卡', '多少人', '多少卡', '几人', '几卡', '有几', '几', 'jr', 'j'):
            for prefix in ('', '/'):
                self.assertIsNotNone(re.fullmatch(patterns['arcade_query_person'][1], prefix + '测试机厅' + suffix))

    def test_summary_pagination_preserves_all_lines_without_wait_details(self):
        lines = [f'{i}号机厅 舞萌5人｜中二3人' for i in range(200)]
        chunks = list(split_arcade_query_messages(lines, compact=True))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1800 for chunk in chunks))
        self.assertEqual('\n'.join(chunks).splitlines(), lines)
