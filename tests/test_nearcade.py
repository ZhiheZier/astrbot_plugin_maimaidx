import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import aiohttp
import astrbot.api.message_components as Comp

from .. import Root
from ..command import mai_arcade
from ..command.mai_nearcade import nearby_arcades_handler
from ..libraries import maimaidx_nearcade as n
from ..libraries.maimaidx_arcade import Arcade, ArcadeList
from ..main import MaimaiDXPlugin


def event(*components, text='', group='200', platform='aiocqhttp'):
    return SimpleNamespace(
        message_str=text,
        message_obj=SimpleNamespace(group_id=group, message=list(components) or [Comp.Plain(text)]),
        get_sender_id=lambda: '123456',
        get_platform_name=lambda: platform,
        plain_result=lambda message: message,
        bot=SimpleNamespace(get_group_member_info=AsyncMock(return_value={'nickname': '测试用户'})),
    )


def location(**changes):
    data = {'lat': '31.2', 'lng': '121.5', 'name': '测试位置', **changes}
    return Comp.Json(data={'app': 'com.tencent.map', 'meta': {'Location.Search': data}})


def game(**changes):
    return {'gameId': 456000, 'titleId': 1, 'name': '舞萌 DX', 'quantity': 2, **changes}


def shop(**changes):
    return {'id': 456, 'name': '测试机厅', 'distance': 1.5,
            'games': [game(totalAttendance=5)], 'address': {'detailed': '测试地址'}, **changes}


def client(**changes):
    return n.NearcadeClient({
        'nearcade_api_token': 'test-secret-token', 'nearcade_upload_enabled': True,
        'nearcade_location_enabled': True, 'nearcade_shop_bindings': ['123=456'], **changes,
    })


async def collect(handler, *args):
    return [result async for result in handler(*args)]


class QQMapCardTest(unittest.TestCase):
    def test_map_card_and_standard_location_do_not_require_text(self):
        for card, name in ((location(), '测试位置'), (Comp.Location(lat=31.2, lon=121.5), ''),
                           (Comp.Location(lat=31.2, lon=121.5, title='地图选点'), '地图选点')):
            self.assertEqual(n.extract_qq_location(event(card)), n.QQCoordinates(31.2, 121.5, name))

    def test_json_constructor_accepts_wire_json_and_alternate_coordinate_keys(self):
        card = Comp.Json(data=json.dumps({'app': 'com.tencent.map', 'meta': {
            'location': {'latitude': 31.2, 'longitude': 121.5},
        }}))
        self.assertEqual(n.extract_qq_location(event(card)), n.QQCoordinates(31.2, 121.5))

    def test_non_location_json_and_plain_text_are_ignored(self):
        for card in (Comp.Json(data={'app': 'com.tencent.miniapp_01', 'meta': {'x': {'lat': 31.2, 'lng': 121.5}}}),
                     Comp.Json(data={'app': 'com.tencent.map', 'meta': {'news': {'url': 'https://example.com'}}}),
                     Comp.Plain('31.2,121.5'), Comp.Json(data={'app': 'com.tencent.map', 'meta': []})):
            self.assertIsNone(n.extract_qq_location(event(card)))

    def test_invalid_or_non_finite_coordinates_are_ignored(self):
        for changes in ({'lat': 91}, {'lng': 181}, {'lat': 'nan'}, {'lng': 'inf'},
                        {'lat': True}, {'lat': None}, {'lat': 'not-a-number'}):
            self.assertIsNone(n.extract_qq_location(event(location(**changes))))
        self.assertEqual(n.extract_qq_location(event(location(lat=0, lng=0))), n.QQCoordinates(0, 0, '测试位置'))

    def test_malformed_json_cannot_escape_parser(self):
        for data in ('not-json', 'a' * 65537, [], {'app': 'com.tencent.map', 'meta': {'x': 'not-an-object'}}):
            card = Comp.Json(data={})
            card.data = data
            self.assertIsNone(n.extract_qq_location(event(card)))


class NearcadeQueryTest(unittest.IsolatedAsyncioTestCase):
    async def test_nearby_uses_qq_coordinate_system_without_auth_and_enforces_bounds(self):
        api = client(nearcade_radius_km=7, nearcade_result_limit=2)
        api._request = AsyncMock(return_value={'shops': [
            shop(id=1, distance=6), shop(id=2, distance=2), shop(id=3, distance=8),
            shop(id=4, distance=1), shop(id=5, distance=0.2, isClosed=True),
            shop(id=6, distance=0.3, games=[game(titleId=3)]),
            shop(id=7, distance=0.4, games=[game(quantity=0)]),
            {'id': 'bad'}, shop(id=8, distance=float('nan')),
        ]})
        results = await api.nearby(n.QQCoordinates(31.2, 121.5))
        self.assertEqual([result.id for result in results], [4, 2])
        args = api._request.await_args
        self.assertEqual(args.args, ('GET', '/discover'))
        self.assertEqual(args.kwargs['params'], {
            'latitude': 31.2, 'longitude': 121.5, 'convertFrom': 'GCJ02',
            'radius': 10, 'limit': 150, 'gameTitleIds': '1',
            'fetchAttendance': 'true', 'includeTimeInfo': 'true',
        })
        self.assertNotIn('authenticated', args.kwargs)

    async def test_config_values_are_bounded_and_default_features_are_off(self):
        api = n.NearcadeClient({})
        self.assertFalse(api.location_enabled)
        self.assertFalse(api.upload_enabled)
        self.assertEqual((api.radius, api.limit), (5, 5))
        for settings, expected in (({'nearcade_radius_km': -1, 'nearcade_result_limit': 0}, (1, 1)),
                                   ({'nearcade_radius_km': 99, 'nearcade_result_limit': 50}, (30, 20)),
                                   ({'nearcade_radius_km': 'nan', 'nearcade_result_limit': 'bad'}, (5, 5))):
            api = n.NearcadeClient(settings)
            self.assertEqual((api.radius, api.limit), expected)

    async def test_non_list_response_is_reported(self):
        api = client()
        api._request = AsyncMock(return_value={'shops': None})
        with self.assertRaises(n.NearcadeError):
            await api.nearby(n.QQCoordinates(31.2, 121.5))

    async def test_response_uses_compact_name_distance_address_and_one_details_link(self):
        record = n.NearbyShop.model_validate(shop(games=[game(totalAttendance=5), game(titleId=3, totalAttendance=99)],
                                                  isOpen=True, currentReportedAttendance={'reportedAt': '2026-10-19T03:00:00Z'}))
        point = n.QQCoordinates(31.2, 121.5, '测试位置')
        text = n.format_nearby_shops([record], 5, point)
        self.assertTrue(text.startswith('🎮 测试机厅（1.5公里）\n📍 测试地址\n\n👉 更多详情请点开：'))
        self.assertEqual(text.count('https://'), 1)
        self.assertNotIn('云端人数', text)
        self.assertNotIn('最近上报', text)
        self.assertIn('未找到', n.format_nearby_shops([], 5, point))

    async def test_distances_under_one_kilometer_use_rounded_meters(self):
        records = [n.NearbyShop.model_validate(shop(id=i, distance=distance))
                   for i, distance in enumerate((0.113, 0.201, 0.746, 1.0, 1.234))]
        text = n.format_nearby_shops(records, 5, n.QQCoordinates(31.2, 121.5))
        for distance in ('113米', '201米', '746米', '1公里', '1.23公里'):
            self.assertIn(f'（{distance}）', text)
        self.assertEqual(text.count('🎮'), 5)
        self.assertEqual(text.count('📍'), 5)

    async def test_details_link_keeps_query_conditions_without_location_name(self):
        point = n.QQCoordinates(30.309953, 120.328264, '龙湖 杭州&金沙天街')
        text = n.format_nearby_shops([], 7.5, point)
        url = text.split('👉 更多详情请点开：')[1]
        self.assertEqual(urlparse(url).netloc, 'nearca.de')
        self.assertEqual(urlparse(url).path, '/discover')
        self.assertEqual(parse_qs(urlparse(url).query), {
            'latitude': ['30.309953'], 'longitude': ['120.328264'], 'radius': ['7.5'],
            'convertFrom': ['GCJ02'], 'gameTitleIds': ['1'],
        })
        self.assertNotIn('龙湖', url)
        self.assertNotIn('name=', url)
        self.assertEqual(text, n.format_nearby_shops([], 7.5, n.QQCoordinates(point.latitude, point.longitude)))


class NearcadeHttpTest(unittest.IsolatedAsyncioTestCase):
    def response(self, status=200, data=None):
        response = SimpleNamespace(status=status, json=AsyncMock(return_value=data or {'success': True}))
        response_context = MagicMock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=False)
        session = SimpleNamespace(request=Mock(return_value=response_context))
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=False)
        return session_context, session

    async def test_token_only_sent_on_authenticated_post_and_redirects_are_disabled(self):
        api = client()
        context, session = self.response()
        with patch.object(n.aiohttp, 'ClientSession', return_value=context):
            await api._request('GET', '/discover', params={'latitude': 31.2})
            self.assertNotIn('Authorization', session.request.call_args.kwargs['headers'])
            await api._request('POST', '/shops/456/attendance', authenticated=True, payload={'games': []})
            self.assertEqual(session.request.call_args.kwargs['headers']['Authorization'], 'Bearer test-secret-token')
            self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
        self.assertEqual(context.__aexit__.await_count, 2)

    async def test_http_errors_are_actionable_without_echoing_secrets(self):
        for status in (400, 401, 403, 404, 429, 500, 302):
            context, _ = self.response(status=status)
            with patch.object(n.aiohttp, 'ClientSession', return_value=context):
                with self.assertRaises(n.NearcadeError) as error:
                    await client()._request('POST', '/shops/456/attendance', authenticated=True)
                self.assertNotIn('test-secret-token', str(error.exception))

    async def test_network_failures_and_timeout_do_not_disclose_request_data(self):
        for exc in (aiohttp.ClientError('test-secret-token latitude=31.2'), asyncio.TimeoutError()):
            context, session = self.response()
            session.request.side_effect = exc
            with patch.object(n.aiohttp, 'ClientSession', return_value=context):
                with self.assertRaises(n.NearcadeError) as error:
                    await client()._request('POST', '/shops/456/attendance', authenticated=True)
                self.assertNotIn('test-secret-token', str(error.exception))
                self.assertNotIn('31.2', str(error.exception))


class NearcadeUploadTest(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_or_disabled_upload_never_contacts_cloud(self):
        for settings, error in (({'nearcade_upload_enabled': False}, False),
                                ({'nearcade_api_token': ''}, True),
                                ({'nearcade_shop_bindings': []}, True),
                                ({'nearcade_shop_bindings': ['123=456', '123=789']}, True)):
            api = client(**settings)
            api._request = AsyncMock()
            if error:
                with self.assertRaises(n.NearcadeError):
                    await api.upload_attendance('123', 5)
            else:
                self.assertEqual(await api.upload_attendance('123', 5), '')
            api._request.assert_not_awaited()

    async def test_upload_reports_absolute_count_using_verified_shop_and_game_ids(self):
        api = client()
        api._request = AsyncMock(side_effect=[{'shop': shop()}, {'success': True}])
        self.assertIn('5 人', await api.upload_attendance('123', 5))
        self.assertEqual(api._request.await_args_list[0].args, ('GET', '/shops/456'))
        self.assertEqual(api._request.await_args.kwargs, {
            'authenticated': True, 'payload': {'games': [{'id': 456000, 'currentAttendances': 5}]},
        })

    async def test_single_maimai_group_with_multiple_cabinets_ignores_chunithm(self):
        api = client()
        api._request = AsyncMock(side_effect=[{
            'shop': shop(games=[game(quantity=4), game(gameId=456001, titleId=3, name='中二节奏')]),
        }, {'success': True}])
        await api.upload_attendance('123', 5)
        self.assertEqual(api._request.await_args.kwargs['payload'], {
            'games': [{'id': 456000, 'currentAttendances': 5}],
        })

    async def test_negative_and_invalid_counts_are_never_uploaded(self):
        for count in (-1, True, 1.5):
            api = client()
            api._request = AsyncMock()
            with self.assertRaises(n.NearcadeError):
                await api.upload_attendance('123', count)
            api._request.assert_not_awaited()

    async def test_non_maimai_or_unavailable_games_are_never_posted(self):
        for games in ([game(titleId=3)], [game(quantity=0)], []):
            api = client()
            api._request = AsyncMock(return_value={'shop': shop(games=games)})
            with self.assertRaises(n.NearcadeError):
                await api.upload_attendance('123', 5)
            self.assertEqual(api._request.await_count, 1)

    async def test_explicit_game_binding_selects_only_that_maimai_machine(self):
        api = client(nearcade_shop_bindings=['123=456:456001'])
        api._request = AsyncMock(side_effect=[{'shop': shop(games=[game(), game(gameId=456001)])}, {'success': True}])
        await api.upload_attendance('123', 0)
        self.assertEqual(api._request.await_args.kwargs['payload']['games'], [{'id': 456001, 'currentAttendances': 0}])

    async def test_cloud_must_confirm_success(self):
        api = client()
        api._request = AsyncMock(side_effect=[{'shop': shop()}, {'success': False}])
        with self.assertRaises(n.NearcadeError):
            await api.upload_attendance('123', 5)

    async def test_turning_upload_off_before_post_prevents_cloud_write(self):
        api = client()

        async def detail(*args, **kwargs):
            api.config['nearcade_upload_enabled'] = False
            return {'shop': shop()}

        api._request = AsyncMock(side_effect=detail)
        self.assertEqual(await api.upload_attendance('123', 5), '')
        self.assertEqual(api._request.await_count, 1)


class MultiRecordUploadTest(unittest.IsolatedAsyncioTestCase):
    def api(self, counts, source='nearcade'):
        api = client(arcade_data_source=source)
        games = [game(gameId=456000 + index) for index in range(len(counts))]
        games.append(game(gameId=456099, titleId=3))
        current = {item['gameId']: count for item, count in zip(games, (*counts, 9))}

        async def request(method, path, **kwargs):
            if method == 'POST':
                for item in kwargs['payload']['games']:
                    current[item['id']] = item['currentAttendances']
                return {'success': True}
            if path == '/shops/456':
                return {'shop': shop(games=games)}
            if path == '/shops/456/attendance':
                return {'success': True, 'games': [dict(item, total=current[item['gameId']]) for item in games],
                        'reported': [], 'registered': []}
            raise AssertionError(path)

        api._request = AsyncMock(side_effect=request)
        return api, games, current

    async def test_distribution_minimizes_changed_records_and_preserves_api_order_for_ties(self):
        cases = (
            ((2, 5, 1), 10, (2, 7, 1)),
            ((2, 5, 1), 6, (2, 3, 1)),
            ((2, 5, 1), 4, (2, 1, 1)),
            ((2, 5, 1), 1, (0, 0, 1)),
            ((2, 5, 1), 0, (0, 0, 0)),
            ((3, 3, 1), 9, (5, 3, 1)),
            ((3, 3, 1), 5, (1, 3, 1)),
            ((3, 3, 3), 8, (2, 3, 3)),
            ((3, 3, 3), 2, (0, 0, 2)),
            ((0, 2, 2), 3, (0, 1, 2)),
            ((0, 0, 0), 3, (3, 0, 0)),
            ((0, 5, 2), 4, (0, 2, 2)),
            ((2, 5, 1), 8, (2, 5, 1)),
        )
        for source in ('local', 'nearcade'):
            for counts, target, expected in cases:
                with self.subTest(source=source, counts=counts, target=target):
                    api, _, current = self.api(counts, source)
                    await api.upload_attendance('123' if source == 'local' else '456', target)
                    actual = tuple(current[456000 + index] for index in range(len(counts)))
                    self.assertEqual(actual, expected)
                    self.assertEqual(sum(actual), target)
                    self.assertTrue(all(value >= 0 for value in actual))
                    self.assertEqual(current[456099], 9)
                    writes = [call for call in api._request.await_args_list if call.args[0] == 'POST']
                    self.assertEqual(len(writes), int(counts != expected))
                    if writes:
                        self.assertEqual(writes[0].kwargs['authenticated'], True)
                        self.assertNotIn(456099, [item['id'] for item in writes[0].kwargs['payload']['games']])
                        self.assertEqual(len(writes[0].kwargs['payload']['games']),
                                         sum(old != new for old, new in zip(counts, expected)))

    async def test_invalid_or_changed_attendance_never_posts(self):
        for mode in ('missing', 'negative', 'duplicate', 'timeout'):
            with self.subTest(mode=mode):
                api, games, _ = self.api((2, 5))
                data = [dict(item, total=2) for item in games]
                if mode == 'missing':
                    data.pop(0)
                elif mode == 'negative':
                    data[0]['total'] = -1
                elif mode == 'duplicate':
                    data.append(data[0])
                api._request.side_effect = [
                    {'shop': shop(games=games)},
                    n.NearcadeError('timeout') if mode == 'timeout' else {
                        'success': True, 'games': data, 'reported': [], 'registered': [],
                    },
                ]
                with self.assertRaises(n.NearcadeError):
                    await api.upload_attendance('456', 4)
                self.assertFalse(any(call.args[0] == 'POST' for call in api._request.await_args_list))

    async def test_disabling_upload_during_attendance_read_prevents_post(self):
        api, _, current = self.api((2, 5))
        original = api._request.side_effect

        async def request(method, path, **kwargs):
            result = await original(method, path, **kwargs)
            if path.endswith('/attendance'):
                api.config['nearcade_upload_enabled'] = False
            return result

        api._request.side_effect = request
        self.assertEqual(await api.upload_attendance('456', 9), '')
        self.assertEqual((current[456000], current[456001]), (2, 5))
        self.assertFalse(any(call.args[0] == 'POST' for call in api._request.await_args_list))


class NearcadeEventTest(unittest.IsolatedAsyncioTestCase):
    async def test_many_results_are_split_at_shop_boundaries(self):
        api = client(nearcade_result_limit=20)
        records = [n.NearbyShop.model_validate(shop(id=i, name=f'{i}号机厅 ' + '测试机厅' * 40,
                                                 address={'detailed': '测试地址' * 50})) for i in range(20)]
        api.nearby = AsyncMock(return_value=records)
        messages = await collect(nearby_arcades_handler, event(location()), api)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 1800 for message in messages))
        for i in range(20):
            self.assertEqual(sum(message.count(f'🎮 {i}号机厅 ') for message in messages), 1)
        self.assertEqual(sum(message.count('👉 更多详情请点开：') for message in messages), 1)
        self.assertIn('👉 更多详情请点开：', messages[-1])

    async def test_location_card_triggers_directly_without_a_prefix_and_without_token(self):
        api = client(nearcade_api_token='')
        api.nearby = AsyncMock(return_value=[n.NearbyShop.model_validate(shop())])
        result = await collect(nearby_arcades_handler, event(location()), api)
        self.assertEqual(len(result), 1)
        self.assertIn('测试机厅', result[0])
        api.nearby.assert_awaited_once_with(n.QQCoordinates(31.2, 121.5, '测试位置'))

    async def test_switch_off_or_non_location_messages_never_query(self):
        api = client()
        api.nearby = AsyncMock()
        self.assertEqual(await collect(nearby_arcades_handler, event(Comp.Plain('hello')), api), [])
        api.config['nearcade_location_enabled'] = False
        self.assertEqual(await collect(nearby_arcades_handler, event(location()), api), [])
        api.nearby.assert_not_awaited()

    async def test_failures_return_one_clear_message(self):
        api = client()
        api.nearby = AsyncMock(side_effect=n.NearcadeError('服务暂不可用'))
        self.assertEqual(await collect(nearby_arcades_handler, event(location()), api), ['附近机厅查询失败：服务暂不可用'])

    async def test_main_honors_whitelist_and_private_chat_but_not_arcade_switch(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.config = {'maimaidxwhitelist': True}
        plugin.enabled_groups, plugin.disabled_groups, plugin.arcade_enabled_groups = {'200'}, set(), set()
        plugin.nearcade = client()
        plugin.nearcade.nearby = AsyncMock(return_value=[])
        self.assertEqual(len(await collect(plugin.nearby_arcades, event(location()))), 1)
        self.assertEqual(len(await collect(plugin.nearby_arcades, event(location(), group=None))), 1)
        for request in (event(location(), group='300'), event(location(), platform='other')):
            self.assertEqual(await collect(plugin.nearby_arcades, request), [])
        plugin.disabled_groups.add('200')
        self.assertEqual(await collect(plugin.nearby_arcades, event(location())), [])
        self.assertEqual(plugin.nearcade.nearby.await_count, 2)

    async def test_config_schema_exposes_all_requested_settings(self):
        schema = json.loads((Root / '_conf_schema.json').read_text(encoding='utf-8'))
        schema = {key: value for node in schema.values() if node['type'] == 'object'
                  for key, value in node['items'].items()}
        for key in ('nearcade_api_token', 'nearcade_upload_enabled', 'nearcade_location_enabled',
                    'nearcade_radius_km', 'nearcade_result_limit', 'nearcade_shop_bindings', 'nearcade_game_aliases'):
            self.assertIn(key, schema)
        self.assertFalse(schema['nearcade_upload_enabled']['default'])
        self.assertFalse(schema['nearcade_location_enabled']['default'])

    async def test_count_and_distance_use_sliders_matching_runtime_bounds(self):
        schema = json.loads((Root / '_conf_schema.json').read_text(encoding='utf-8'))
        schema = schema['location']['items']
        for key, expected in (('nearcade_radius_km', {'min': 1, 'max': 30, 'step': 0.5}),
                              ('nearcade_result_limit', {'min': 1, 'max': 20, 'step': 1})):
            self.assertEqual(schema[key]['slider'], expected)
            self.assertGreaterEqual(schema[key]['default'], expected['min'])
            self.assertLessEqual(schema[key]['default'], expected['max'])


class LocalAttendanceSyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = Arcade(name='测试机厅', location='地址', province='', mall='', num=2, id='123',
                             alias=['别名'], group=[200], person=2, by='', time='')
        self.arcades = ArcadeList([self.record])
        self.api = client()
        self.api.upload_attendance = AsyncMock(return_value='已同步 nearcade：5 人')

    async def run_command(self, text):
        with (patch.object(mai_arcade.arcade, 'total', self.arcades, create=True),
              patch.object(ArcadeList, 'save_arcade', AsyncMock())):
            return await collect(mai_arcade.arcade_person_handler, event(text=text), self.api)

    async def test_success_saves_local_and_uploads_final_count(self):
        result = await self.run_command('/别名=5')
        self.assertEqual(self.record.person, 5)
        self.api.upload_attendance.assert_awaited_once_with('123', 5)
        self.assertIn('已同步 nearcade', result[0])

    async def test_cloud_failure_does_not_undo_local_update(self):
        self.api.upload_attendance.side_effect = n.NearcadeError('Token 已过期')
        result = await self.run_command('测试机厅+1')
        self.assertEqual(self.record.person, 3)
        self.assertIn('本地更新已保存', result[0])
        self.assertIn('Token 已过期', result[0])

    async def test_invalid_noop_and_closed_updates_never_upload(self):
        for command in ('测试机厅=99', '测试机厅+31', '测试机厅-9', '测试机厅=-2', '测试机厅=2'):
            await self.run_command(command)
            self.assertEqual(self.record.person, 2)
        await self.run_command('测试机厅=-1')
        self.assertEqual((self.record.person, self.record.status), (0, '闭店'))
        await self.run_command('测试机厅=0')
        self.assertEqual(self.record.status, '闭店')
        self.api.upload_attendance.assert_not_awaited()
        await self.run_command('测试机厅=1')
        self.assertEqual(self.record.status, '营业')
        self.api.upload_attendance.assert_awaited_once_with('123', 1)

    async def test_updates_for_same_arcade_are_serialized_including_upload(self):
        calls = []

        async def upload(local_id, count):
            calls.append(count)
            await asyncio.sleep(0.01)
            return '已同步'

        self.api.upload_attendance.side_effect = upload
        with (patch.object(mai_arcade.arcade, 'total', self.arcades, create=True),
              patch.object(ArcadeList, 'save_arcade', AsyncMock())):
            await asyncio.gather(*(collect(mai_arcade.arcade_person_handler, event(text='测试机厅+1'), self.api)
                                   for _ in range(2)))
        self.assertEqual(calls, [3, 4])
        self.assertEqual(self.record.person, 4)

    async def test_main_passes_client_and_disabled_arcade_never_uploads(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.disabled_groups = set()
        plugin.arcade_enabled_groups = set()
        plugin.nearcade = self.api
        request = event(text='测试机厅+1')
        self.assertEqual(await collect(plugin.arcade_person, request), [])
        self.api.upload_attendance.assert_not_awaited()
        plugin.arcade_enabled_groups.add('200')

        async def respond(*args):
            yield 'ok'

        with patch.object(mai_arcade, 'arcade_person_handler', side_effect=respond) as handle:
            self.assertEqual(await collect(plugin.arcade_person, request), ['ok'])
            handle.assert_called_once_with(request, self.api)
