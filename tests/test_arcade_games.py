import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_games import DEFAULT_ARCADE_GAME, resolve_arcade_game, split_arcade_game, parse_arcade_reports
from ..libraries.maimaidx_nearcade import NearcadeError
from .test_nearcade import client, collect, event, game, shop
from .test_help_image import command_decorators


def arcade(name='测试机厅', id='123'):
    return a.Arcade(name=name, location='地址', province='', mall='', num=2, id=id,
                    alias=['别名'], group=[200], person=2, by='原用户', time='原时间')


class ArcadeGameParserTest(unittest.TestCase):
    def test_aliases_resolve_to_the_right_cloud_game_families(self):
        for alias, key, title_ids in (
            ('舞萌', 'mai', (1,)), ('mai', 'mai', (1,)), ('MAI', 'mai', (1,)),
            ('中二', 'chu', (3,)), ('CHU', 'chu', (3,)), ('chunithm', 'chu', (3,)),
            ('太鼓', 'taiko', (31, 15)), ('Taiko', 'taiko', (31, 15)),
            ('sdvx', 'sdvx', (4,)), ('音击', 'ongeki', (27,)), ('华卡', 'wacca', (17,)),
        ):
            with self.subTest(alias=alias):
                result = resolve_arcade_game(alias)
                self.assertEqual((result.key, result.title_ids), (key, title_ids))
        self.assertIsNone(resolve_arcade_game('unknown-game'))

    def test_game_suffix_accepts_optional_space_and_defaults_to_maimai(self):
        for target, name, key in (
            ('测试机厅', '测试机厅', 'mai'), ('测试机厅 mai', '测试机厅', 'mai'),
            ('测试机厅中二', '测试机厅', 'chu'), ('测试机厅 CHU', '测试机厅', 'chu'),
            ('测试机厅 太鼓', '测试机厅', 'taiko'), ('太鼓', '', 'taiko'),
        ):
            self.assertEqual((split_arcade_game(target)[0], split_arcade_game(target)[1].key), (name, key))

    def test_exact_shop_names_take_precedence_and_ids_are_supported(self):
        record = arcade(name='某店chu')
        records = a.ArcadeList([record])
        matches, selected = mai_arcade._arcade_target(records, '某店chu')
        self.assertEqual(matches, [record])
        self.assertEqual(selected, DEFAULT_ARCADE_GAME)
        matches, selected = mai_arcade._arcade_target(records, '123 chu')
        self.assertEqual(matches, [record])
        self.assertEqual(selected.key, 'chu')
        matches, selected = mai_arcade._arcade_target(records, '别名中二人数')
        self.assertEqual(matches, [record])
        self.assertEqual(selected.key, 'chu')


class GameAttendanceTest(unittest.IsolatedAsyncioTestCase):
    async def test_old_saved_records_keep_maimai_and_start_other_games_at_zero(self):
        record = arcade()
        legacy = record.model_dump(exclude={'game_attendance'})
        reloaded = a.Arcade.model_validate(legacy)
        self.assertEqual(reloaded.attendance_for().person, 2)
        self.assertEqual(reloaded.attendance_for(resolve_arcade_game('chu')).person, 0)
        self.assertEqual(reloaded.game_attendance, {})

    async def test_games_store_independent_counts_reporters_and_closure_state(self):
        record = arcade()
        records = a.ArcadeList([record])
        chu, taiko = resolve_arcade_game('chu'), resolve_arcade_game('太鼓')
        with (patch.object(a.arcade, 'total', records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            await a.update_person([record], '中二玩家', '=', 5, chu)
            await a.update_person([record], '鼓人', '=', 3, taiko)
            self.assertEqual((record.person, record.by), (2, '原用户'))
            self.assertEqual((record.attendance_for(chu).person, record.attendance_for(chu).by), (5, '中二玩家'))
            self.assertEqual(record.attendance_for(taiko).person, 3)
            await a.update_person([record], '中二玩家', '=', -1, chu)
            self.assertEqual((record.attendance_for(chu).status, record.attendance_for(chu).person), ('闭店', 0))
            self.assertEqual(record.status, '营业')
            self.assertEqual(record.attendance_for(taiko).status, '营业')
            await a.update_person([record], '中二玩家', '=', 0, chu)
            self.assertEqual(record.attendance_for(chu).status, '闭店')
            await a.update_person([record], '中二玩家', '+', 1, chu)
            self.assertEqual(record.attendance_for(chu).status, '营业')
        reloaded = a.Arcade.model_validate(record.model_dump())
        self.assertEqual(reloaded.attendance_for(chu).person, 1)
        self.assertEqual(reloaded.attendance_for(taiko).person, 3)

    async def test_queries_use_selected_game_without_uploading_or_mixing_averages(self):
        record = arcade()
        records = a.ArcadeList([record])
        chu = resolve_arcade_game('chu')
        record.game_attendance['chu'] = a.ArcadeAttendance(person=7, by='中二玩家')
        with patch.object(mai_arcade.arcade, 'total', records, create=True):
            for text in ('测试机厅 chu几人', '测试机厅中二几人', '机厅 chu几人', 'chu几人'):
                result = await collect(mai_arcade.arcade_query_person_handler, event(text=text))
                self.assertIn('当前 7 人', result[0])
                self.assertIn('中二节奏', result[0])
                self.assertNotIn('平均', result[0])
            result = await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'))
            self.assertIn('舞萌：2人', result[0])
            self.assertNotIn('中二玩家', result[0])
            self.assertIn('中二：7人', result[0])

    async def test_save_refreshes_in_memory_download_source_and_persists_game_records(self):
        record = arcade()
        record.game_attendance['chu'] = a.ArcadeAttendance(person=4)
        records = a.ArcadeList([record])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'arcades.json'
            with (patch.object(a, 'arcades_json', path), patch.object(a.arcade, 'arcades', [])):
                self.assertTrue(await records.save_arcade())
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved[0]['game_attendance']['chu']['person'], 4)
                self.assertEqual(a.arcade.arcades, saved)


class MultiGameUploadTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_shop_mapping_selects_requested_game_only(self):
        for alias, title_id, game_id in (('mai', 1, 456000), ('chu', 3, 456001), ('太鼓', 31, 456002)):
            selected = resolve_arcade_game(alias)
            api = client()
            api._request = AsyncMock(side_effect=[{'shop': shop(games=[
                game(gameId=456000, titleId=1), game(gameId=456001, titleId=3), game(gameId=456002, titleId=31),
            ])}, {'success': True}])
            await api.upload_attendance('123', 5, selected)
            self.assertEqual(api._request.await_args.kwargs['payload'], {
                'games': [{'id': game_id, 'currentAttendances': 5}],
            })

    async def test_maimai_legacy_explicit_id_is_not_inherited_by_chunithm(self):
        api = client(nearcade_shop_bindings=['123=456:456000'])
        chu = resolve_arcade_game('chu')
        api._request = AsyncMock(side_effect=[{'shop': shop(games=[game(), game(gameId=456001, titleId=3)])},
                                             {'success': True}])
        await api.upload_attendance('123', 5, chu)
        self.assertEqual(api._request.await_args.kwargs['payload']['games'][0]['id'], 456001)

    async def test_game_specific_mapping_overrides_general_and_selects_explicit_group(self):
        api = client(nearcade_shop_bindings=['123=456:456000', '123[中二]=789:789002'])
        api._request = AsyncMock(side_effect=[{'shop': shop(id=789, games=[
            game(gameId=789001, titleId=3), game(gameId=789002, titleId=3),
        ])}, {'success': True}])
        await api.upload_attendance('123', 5, resolve_arcade_game('chu'))
        self.assertEqual(api._request.await_args_list[0].args, ('GET', '/shops/789'))
        self.assertEqual(api._request.await_args.kwargs['payload']['games'][0]['id'], 789002)

    async def test_old_and_new_taiko_share_one_family_total(self):
        api = client()
        games = [game(titleId=31), game(gameId=456001, titleId=15)]
        api._request = AsyncMock(side_effect=[{'shop': shop(games=games)}, {
            'success': True, 'games': [dict(games[0], total=2), dict(games[1], total=1)],
            'reported': [], 'registered': [],
        }, {'success': True}])
        await api.upload_attendance('123', 5, resolve_arcade_game('太鼓'))
        self.assertEqual(api._request.await_args.kwargs['payload']['games'],
                         [{'id': 456000, 'currentAttendances': 4}])


class MultiGameCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_plain_headcounts_set_the_selected_game_and_default_to_maimai(self):
        record = arcade()
        api = client()
        api.upload_attendance = AsyncMock(return_value='已同步')
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            for text, selected, count in (
                ('测试机厅3人', DEFAULT_ARCADE_GAME, 3),
                ('测试机厅 mai 4人', DEFAULT_ARCADE_GAME, 4),
                ('测试机厅中二3人', resolve_arcade_game('chu'), 3),
                ('/别名 CHU 5人', resolve_arcade_game('chu'), 5),
                ('123 太鼓2人', resolve_arcade_game('太鼓'), 2),
            ):
                with self.subTest(text=text):
                    pattern = command_decorators()['arcade_person'][1]
                    self.assertIsNotNone(re.fullmatch(pattern, text))
                    result = await collect(mai_arcade.arcade_person_handler, event(text=text), api)
                    self.assertIn('已同步', result[0])
                    self.assertEqual(record.attendance_for(selected).person, count)
                    if selected == DEFAULT_ARCADE_GAME:
                        api.upload_attendance.assert_awaited_with('123', count)
                    else:
                        api.upload_attendance.assert_awaited_with('123', count, selected)
            self.assertEqual(record.person, 4)

    async def test_headcounts_without_unit_set_the_selected_game(self):
        record = arcade()
        api = client()
        api.upload_attendance = AsyncMock(return_value='已同步')
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            for text, key, count in (
                ('测试机厅3', 'mai', 3), ('测试机厅mai4', 'mai', 4),
                ('/别名 CHU 5', 'chu', 5), ('测试机厅中二0', 'chu', 0),
                ('123 太鼓2', 'taiko', 2), ('123 6', 'mai', 6),
                ('123mai7', 'mai', 7),
            ):
                with self.subTest(text=text):
                    self.assertIsNotNone(re.fullmatch(command_decorators()['arcade_person'][1], text))
                    result = await collect(mai_arcade.arcade_person_handler, event(text=text), api)
                    self.assertIn('已同步', result[0])
                    selected = resolve_arcade_game(key)
                    self.assertEqual(record.attendance_for(selected).person, count)

    async def test_plain_headcounts_ignore_unsubscribed_shops_and_normal_chat(self):
        record = arcade()
        api = client()
        api.upload_attendance = AsyncMock()
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock()) as save):
            for text in ('13人', '今天吃饭3人', '未订阅机厅4人', '测试机厅几人', '测试机厅' + '9' * 5000 + '人',
                         '1233', '13', '今天吃饭3', '未订阅机厅4'):
                result = await collect(mai_arcade.arcade_person_handler, event(text=text), api)
                self.assertEqual(result, [])
            self.assertEqual(record.person, 2)
            save.assert_not_awaited()
            api.upload_attendance.assert_not_awaited()

    async def test_digit_ending_names_use_the_longest_shop_label(self):
        records = a.ArcadeList([arcade(name='天街'), arcade(name='天街2', id='124')])
        api = client(nearcade_upload_enabled=False, nearcade_shop_bindings=[])
        with (patch.object(mai_arcade.arcade, 'total', records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            for text, count in (('天街23', 3), ('天街2 4', 4), ('天街2mai5', 5)):
                with self.subTest(text=text):
                    result = await collect(mai_arcade.arcade_person_handler, event(text=text), api)
                    self.assertTrue(result)
                    self.assertEqual(records[1].person, count)
                    self.assertEqual(records[0].person, 2)

    async def test_invalid_new_game_report_does_not_create_state(self):
        record = arcade()
        with (patch.object(a.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock()) as save):
            await a.update_person([record], '用户', '=', 99, resolve_arcade_game('chu'))
            self.assertEqual(record.game_attendance, {})
            save.assert_not_awaited()

    async def test_updates_route_to_the_game_without_changing_maimai(self):
        record = arcade()
        records = a.ArcadeList([record])
        api = client()
        api.upload_attendance = AsyncMock(return_value='已同步')
        with (patch.object(mai_arcade.arcade, 'total', records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            for text, key, count in (('测试机厅中二=5', 'chu', 5), ('测试机厅 chu+1', 'chu', 6),
                                     ('别名太鼓=4', 'taiko', 4), ('123 taiko-1', 'taiko', 3)):
                result = await collect(mai_arcade.arcade_person_handler, event(text=text), api)
                self.assertIn('已同步', result[0])
                self.assertEqual(record.person, 2)
                selected = resolve_arcade_game(key)
                self.assertEqual(record.attendance_for(selected).person, count)
                api.upload_attendance.assert_awaited_with('123', count, selected)

    async def test_no_game_defaults_to_maimai_and_single_subscription_allows_bare_game(self):
        record = arcade()
        api = client()
        api.upload_attendance = AsyncMock(return_value='已同步')
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            await collect(mai_arcade.arcade_person_handler, event(text='测试机厅+1'), api)
            api.upload_attendance.assert_awaited_with('123', 3)
            await collect(mai_arcade.arcade_person_handler, event(text='chu=5'), api)
            api.upload_attendance.assert_awaited_with('123', 5, resolve_arcade_game('chu'))
            self.assertEqual(record.person, 3)

    async def test_multiple_subscribed_shops_require_a_name_before_updating(self):
        records = a.ArcadeList([arcade(), arcade(name='另一店', id='124')])
        api = client()
        api.upload_attendance = AsyncMock()
        with patch.object(mai_arcade.arcade, 'total', records, create=True):
            result = await collect(mai_arcade.arcade_person_handler, event(text='chu+1'), api)
        self.assertIn('指定机厅', result[0])
        api.upload_attendance.assert_not_awaited()

    async def test_cloud_failure_preserves_only_the_selected_local_game(self):
        record = arcade()
        api = client()
        api.upload_attendance = AsyncMock(side_effect=RuntimeError('network failure'))
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            result = await collect(mai_arcade.arcade_person_handler, event(text='测试机厅chu=5'), api)
        self.assertEqual(record.person, 2)
        self.assertEqual(record.attendance_for(resolve_arcade_game('chu')).person, 5)
        self.assertIn('本地更新已保存', result[0])


class NamedArcadePartitionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = arcade()
        self.records = a.ArcadeList([self.record])
        self.api = client(nearcade_shop_bindings=[
            '123[maiA区]=456:456000', '123[maiB区]=456:456001', '123[chu]=456:456002',
        ])
        self.zone_a, self.zone_b, self.chu = self.api.games_for('123')
        self.api.read_attendance = AsyncMock(return_value={})

    async def report(self, text):
        with (patch.object(mai_arcade.arcade, 'total', self.records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock())):
            return await collect(mai_arcade.arcade_person_handler, event(text=text), self.api)

    async def test_partition_names_preserve_family_and_only_match_known_suffixes(self):
        self.assertEqual((self.zone_a.key, self.zone_a.family_key, self.zone_a.title_ids), ('mai:a区', 'mai', (1,)))
        self.assertEqual(self.zone_a, resolve_arcade_game('maiA区'))
        self.assertEqual(resolve_arcade_game('舞萌A区').key, self.zone_a.key)
        for target in ('测试机厅maiA区', '测试机厅 maiA区', '测试机厅 MAIa区'):
            matched, selected = mai_arcade._arcade_target(self.records, target, self.api)
            self.assertEqual(matched, [self.record])
            self.assertEqual(selected.key, self.zone_a.key)
        self.assertEqual(mai_arcade._arcade_target(self.records, '测试机厅maiC区', self.api)[0], [])
        self.assertEqual(split_arcade_game('测试机厅maiA区')[0], '测试机厅maiA区')

    async def test_reports_update_only_selected_partition_and_upload_its_game_id(self):
        self.api._request = AsyncMock(side_effect=[
            {'shop': shop(games=[game(), game(gameId=456001)])}, {'success': True},
            {'shop': shop(games=[game(), game(gameId=456001)])}, {'success': True},
        ])
        for text, selected, count, game_id in (
            ('测试机厅maiA区3人', self.zone_a, 3, 456000),
            ('123 maiB区+1', self.zone_b, 1, 456001),
        ):
            result = await self.report(text)
            self.assertIn(f'已同步 nearcade：{selected.name} {count} 人', result[0])
            self.assertEqual(self.record.attendance_for(selected).person, count)
            self.assertEqual(self.api._request.await_args.kwargs['payload']['games'],
                             [{'id': game_id, 'currentAttendances': count}])
        self.assertEqual(self.record.person, 2)
        self.assertNotIn('chu', self.record.game_attendance)

    async def test_unqualified_reports_request_partition_without_changing_any_counts(self):
        self.api.upload_attendance = AsyncMock()
        for text in ('测试机厅3人', '测试机厅mai=5', '测试机厅舞萌+1', 'mai=4'):
            result = await self.report(text)
            self.assertIn('请指定映射名', result[0])
            self.assertIn('maiA区', result[0])
            self.assertIn('maiB区', result[0])
        self.assertEqual(self.record.person, 2)
        self.assertEqual(self.record.game_attendance, {})
        self.api.upload_attendance.assert_not_awaited()

    async def test_queries_show_partitions_and_other_games_without_uploading(self):
        self.record.game_attendance[self.zone_a.key] = a.ArcadeAttendance(
            person=5, by='甲', time='2026-10-19 15:00:00', label=self.zone_a.name)
        self.record.game_attendance[self.zone_b.key] = a.ArcadeAttendance(
            person=3, by='乙', time='2026-10-19 15:01:00', label=self.zone_b.name)
        self.record.game_attendance['chu'] = a.ArcadeAttendance(person=2, by='丙')
        self.api._request = AsyncMock()
        with patch.object(mai_arcade.arcade, 'total', self.records, create=True):
            for handler, text in ((mai_arcade.arcade_query_multiple_handler, '机厅几人'),
                                  (mai_arcade.arcade_query_person_handler, '测试机厅几人'),
                                  (mai_arcade.arcade_query_person_handler, '123几人')):
                result = (await collect(handler, event(text=text), self.api))[0]
                if handler is mai_arcade.arcade_query_multiple_handler:
                    self.assertIn('maiA区：5人', result)
                    self.assertIn('maiB区：3人', result)
                    self.assertIn('中二：2人', result)
                    self.assertNotIn('更新于', result)
                    continue
                self.assertIn('maiA区：当前 5 人', result)
                self.assertIn('maiB区：当前 3 人', result)
                self.assertIn('中二节奏：当前 2 人', result)
                self.assertIn('未分区旧记录', result)
                self.assertIn('（甲 · 2026-10-19 15:00:00）', result)
                self.assertNotIn('平均', result)
            result = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅mai几人'), self.api))[0]
            self.assertIn('maiA区', result)
            self.assertIn('maiB区', result)
            self.assertNotIn('中二节奏', result)
            result = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅maiA区几人'), self.api))[0]
            self.assertIn('maiA区', result)
            self.assertIn('当前 5 人', result)
            self.assertNotIn('maiB区', result)
            self.assertNotIn('中二节奏', result)
        self.api._request.assert_not_awaited()

    async def test_unreported_partitions_are_not_shown_as_confirmed_zero(self):
        self.record.person, self.record.by, self.record.time = 0, '', ''
        before = self.record.model_dump()
        with patch.object(mai_arcade.arcade, 'total', self.records, create=True):
            result = (await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api))[0]
            self.assertIn('maiA区：未上报', result)
            self.assertIn('maiB区：未上报', result)
            self.assertNotIn('未分区旧记录', result)
            self.assertNotIn('当前 0 人', result)
            result = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅maiB区几人'), self.api))[0]
            self.assertIn('maiB区：尚未上报', result)
        self.assertEqual(self.record.model_dump(), before)

    async def test_partition_reports_work_locally_with_cloud_upload_disabled(self):
        self.api.config['nearcade_upload_enabled'] = False
        self.api._request = AsyncMock()
        result = await self.report('测试机厅maiA区3人')
        self.assertIn('人数已更新为 3 人', result[0])
        self.assertEqual(self.record.attendance_for(self.zone_a).person, 3)
        self.api._request.assert_not_awaited()

    async def test_partition_closure_and_saved_labels_survive_reload(self):
        self.api.config['nearcade_upload_enabled'] = False
        await self.report('测试机厅maiA区3人')
        await self.report('测试机厅maiB区2人')
        await self.report('测试机厅maiA区=-1')
        self.assertEqual(self.record.attendance_for(self.zone_a).status, '闭店')
        self.assertEqual(self.record.attendance_for(self.zone_b).person, 2)
        await self.report('测试机厅maiA区=0')
        self.assertEqual(self.record.attendance_for(self.zone_a).status, '闭店')
        await self.report('测试机厅maiA区+1')
        reloaded = a.Arcade.model_validate_json(self.record.model_dump_json())
        self.assertEqual(reloaded.attendance_for(self.zone_a).person, 1)
        self.assertEqual(reloaded.attendance_for(self.zone_a).status, '营业')
        self.assertEqual(reloaded.attendance_for(self.zone_b).person, 2)
        self.assertIn('maiA区', [item.name for item in reloaded.known_games()])
        message = a.ArcadeList.arcade_to_msg([reloaded], None)[0]
        self.assertIn('maiA区：当前 1 人', message)
        self.assertIn('maiB区：当前 2 人', message)

    async def test_missing_or_conflicting_partition_mapping_never_posts(self):
        for bindings, selected in (
            (['123=456'], self.zone_a),
            (['123[maiA区]=456'], self.zone_a),
            (['123[maiA区]=456:456000', '123[maiB区]=456:456000'], self.zone_a),
            (['123[maiA区]=456:456000', '123[maiA区]=456:456001'], self.zone_a),
            (self.api.config['nearcade_shop_bindings'], DEFAULT_ARCADE_GAME),
        ):
            api = client(nearcade_shop_bindings=bindings)
            api._request = AsyncMock()
            with self.assertRaises(NearcadeError):
                await api.upload_attendance('123', 5, selected)
            api._request.assert_not_awaited()

    async def test_game_id_with_wrong_family_is_not_uploaded(self):
        self.api._request = AsyncMock(return_value={'shop': shop(games=[game(titleId=3)])})
        with self.assertRaises(NearcadeError):
            await self.api.upload_attendance('123', 5, self.zone_a)
        self.assertEqual(self.api._request.await_count, 1)

    async def test_multiple_shop_queries_keep_subscription_scope_and_own_partitions(self):
        other = arcade(name='另一机厅', id='124')
        other.alias = []
        other.game_attendance['taiko'] = a.ArcadeAttendance(person=7)
        private = arcade(name='未订阅机厅', id='125')
        private.group = []
        private.game_attendance[self.zone_a.key] = a.ArcadeAttendance(person=20, label=self.zone_a.name)
        records = a.ArcadeList([self.record, other, private])
        with patch.object(mai_arcade.arcade, 'total', records, create=True):
            result = (await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), self.api))[0]
            first, second = result.split('\n\n')
            self.assertIn('maiA区', first)
            self.assertIn('另一机厅', second)
            self.assertIn('太鼓达人：7人', second)
            self.assertNotIn('maiA区', second)
            self.assertNotIn('未订阅机厅', '\n'.join((first, second)))
            result = (await collect(mai_arcade.arcade_query_person_handler, event(text='maiA区几人'), self.api))[0]
            self.assertNotIn('未订阅机厅', result)
            self.assertNotIn('另一机厅', result)


class BatchArcadeReportTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = arcade()
        self.records = a.ArcadeList([self.record])
        self.api = client()
        self.api.upload_attendance = AsyncMock(return_value='已同步')
        self.chu = resolve_arcade_game('chu')

    async def report(self, text, save=None):
        with (patch.object(mai_arcade.arcade, 'total', self.records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', save or AsyncMock(return_value=True))):
            return await collect(mai_arcade.arcade_person_handler, event(text=text), self.api)

    async def test_parser_understands_compact_spaced_chinese_and_relative_forms(self):
        for text, expected in (
            ('mai3chu2', [('mai', '=', 3), ('chu', '=', 2)]),
            ('舞萌 3人 中二 2人 太鼓0', [('mai', '=', 3), ('chu', '=', 2), ('taiko', '=', 0)]),
            ('MAI=3 CHU+2', [('mai', '=', 3), ('chu', '+', 2)]),
            ('mai=-1chu-2', [('mai', '=', -1), ('chu', '-', 2)]),
        ):
            with self.subTest(text=text):
                self.assertEqual([(item.game.key, item.operation, item.person) for item in parse_arcade_reports(text)], expected)

    async def test_main_routes_compact_batch_commands(self):
        pattern = command_decorators()['arcade_person'][1]
        for text in ('测试机厅mai3chu2', '/别名 MAI3 CHU2', '123舞萌3人中二2人',
                     '测试机厅maiA区3maiB区2', 'mai3chu2太鼓1', '测试机厅mai+1chu-1'):
            self.assertIsNotNone(re.fullmatch(pattern, text), text)

    async def test_batch_sets_absolute_counts_and_returns_each_sync_result(self):
        result = (await self.report('测试机厅mai3chu2'))[0]
        self.assertEqual(self.record.person, 3)
        self.assertEqual(self.record.attendance_for(self.chu).person, 2)
        self.assertIn('舞萌 DX：本地更新成功，当前 3 人；云端同步成功', result)
        self.assertIn('中二节奏：本地更新成功，当前 2 人；云端同步成功', result)
        self.assertEqual([call.args for call in self.api.upload_attendance.await_args_list],
                         [('123', 3), ('123', 2, self.chu)])

    async def test_alias_id_and_single_subscription_can_target_the_same_shop(self):
        for text in ('/别名 MAI3 CHU2', '123 mai3 chu2', 'mai3chu2'):
            self.record.person = 2
            self.record.game_attendance.clear()
            result = (await self.report(text))[0]
            self.assertIn('机厅：测试机厅', result)
            self.assertEqual(self.record.person, 3)
            self.assertEqual(self.record.attendance_for(self.chu).person, 2)

    async def test_partial_cloud_failure_keeps_both_local_counts(self):
        self.api.upload_attendance.side_effect = ['已同步', NearcadeError('Token 已过期')]
        result = (await self.report('测试机厅mai3chu2'))[0]
        self.assertIn('舞萌 DX：本地更新成功，当前 3 人；云端同步成功', result)
        self.assertIn('中二节奏：本地更新成功，当前 2 人；云端未确认同步：Token 已过期（本地记录保留）', result)
        self.assertEqual(self.record.person, 3)
        self.assertEqual(self.record.attendance_for(self.chu).person, 2)

    async def test_first_cloud_error_does_not_skip_the_next_game_or_expose_exception(self):
        self.api.upload_attendance.side_effect = [RuntimeError('private-secret'), '已同步']
        result = (await self.report('测试机厅mai3chu2'))[0]
        self.assertIn('舞萌 DX：本地更新成功，当前 3 人；云端未确认同步', result)
        self.assertIn('中二节奏：本地更新成功，当前 2 人；云端同步成功', result)
        self.assertNotIn('private-secret', result)
        self.assertEqual(self.api.upload_attendance.await_count, 2)

    async def test_invalid_local_count_does_not_block_valid_game(self):
        result = (await self.report('测试机厅mai99chu2'))[0]
        self.assertIn('舞萌 DX：更新失败，单次人数变化不能超过30人', result)
        self.assertIn('中二节奏：本地更新成功，当前 2 人', result)
        self.assertEqual(self.record.person, 2)
        self.api.upload_attendance.assert_awaited_once_with('123', 2, self.chu)

    async def test_local_save_failure_restores_only_failed_change_and_never_uploads_it(self):
        save = AsyncMock(side_effect=[False, True])
        result = (await self.report('测试机厅mai3chu2', save))[0]
        self.assertIn('舞萌 DX：更新失败，本地保存失败', result)
        self.assertIn('中二节奏：本地更新成功，当前 2 人', result)
        self.assertEqual(self.record.person, 2)
        self.assertEqual((self.record.by, self.record.time), ('原用户', '原时间'))
        self.api.upload_attendance.assert_awaited_once_with('123', 2, self.chu)

    async def test_all_local_save_errors_leave_counts_unchanged_and_report_both_failures(self):
        before = self.record.model_dump()
        save = AsyncMock(side_effect=OSError('disk unavailable'))
        result = (await self.report('测试机厅mai3chu2', save))[0]
        self.assertIn('舞萌 DX：更新失败，本地保存失败', result)
        self.assertIn('中二节奏：更新失败，本地保存失败', result)
        self.assertEqual(self.record.model_dump(), before)
        self.api.upload_attendance.assert_not_awaited()

    async def test_malformed_and_duplicate_items_abort_before_any_save_or_upload(self):
        before = self.record.model_dump()
        save = AsyncMock(return_value=True)
        for text in ('测试机厅mai3chu', '测试机厅mai3chu2额外文字', '测试机厅mai3chu2mai4',
                     '测试机厅mai3舞萌2', '测试机厅mai3unknown2', '测试机厅mai3chu9999999999'):
            result = (await self.report(text, save))[0]
            self.assertIn('批量报数未执行', result)
            self.assertIn('本次未更新任何人数', result)
            self.assertEqual(self.record.model_dump(), before)
        save.assert_not_awaited()
        self.api.upload_attendance.assert_not_awaited()

    async def test_partition_counts_can_be_reported_together_on_one_local_shop(self):
        self.api.config['nearcade_shop_bindings'] = ['123[maiA区]=456:456000', '123[maiB区]=456:456001']
        first, second = self.api.games_for('123')
        result = (await self.report('测试机厅maiA区3maiB区2'))[0]
        self.assertEqual(self.record.person, 2)
        self.assertEqual(self.record.attendance_for(first).person, 3)
        self.assertEqual(self.record.attendance_for(second).person, 2)
        self.assertIn('maiA区：本地更新成功，当前 3 人', result)
        self.assertIn('maiB区：本地更新成功，当前 2 人', result)
        self.assertEqual([call.args for call in self.api.upload_attendance.await_args_list],
                         [('123', 3, first), ('123', 2, second)])

    async def test_partition_game_requires_label_while_other_game_still_updates(self):
        self.api.config['nearcade_shop_bindings'] = ['123[maiA区]=456:456000', '123[maiB区]=456:456001']
        result = (await self.report('测试机厅mai3chu2'))[0]
        self.assertIn('舞萌 DX：更新失败，该游戏按分区记录人数', result)
        self.assertIn('中二节奏：本地更新成功，当前 2 人', result)
        self.assertEqual(self.record.person, 2)
        self.api.upload_attendance.assert_awaited_once_with('123', 2, self.chu)

    async def test_first_explicit_zero_is_saved_and_uploaded_but_repeat_zero_is_not(self):
        result = (await self.report('测试机厅mai3chu0'))[0]
        self.assertIn('中二节奏：本地更新成功，当前 0 人；云端同步成功', result)
        self.assertIn('chu', self.record.game_attendance)
        self.api.upload_attendance.assert_any_await('123', 0, self.chu)
        self.api.upload_attendance.reset_mock()
        result = (await self.report('测试机厅mai3chu0'))[0]
        self.assertIn('舞萌 DX：人数未变化', result)
        self.assertIn('中二节奏：人数未变化', result)
        self.api.upload_attendance.assert_not_awaited()

    async def test_closed_game_is_not_uploaded_and_zero_does_not_reopen_it(self):
        result = (await self.report('测试机厅mai=-1chu2'))[0]
        self.assertIn('舞萌 DX：本地已闭店；未上传云端', result)
        self.assertEqual(self.record.status, '闭店')
        self.api.upload_attendance.assert_awaited_once_with('123', 2, self.chu)
        self.api.upload_attendance.reset_mock()
        await self.report('测试机厅mai0chu2')
        self.assertEqual(self.record.status, '闭店')
        self.api.upload_attendance.assert_not_awaited()
        await self.report('测试机厅mai+1chu2')
        self.assertEqual(self.record.status, '营业')
        self.api.upload_attendance.assert_awaited_once_with('123', 1)

    async def test_disabled_cloud_still_saves_every_local_game_with_clear_reply(self):
        self.api.config['nearcade_upload_enabled'] = False
        result = (await self.report('测试机厅mai3chu2'))[0]
        self.assertEqual(result.count('未开启云端上传'), 2)
        self.assertEqual(self.record.person, 3)
        self.assertEqual(self.record.attendance_for(self.chu).person, 2)
        self.api.upload_attendance.assert_not_awaited()

    async def test_regular_chat_and_unsubscribed_shop_never_changes_attendance(self):
        save = AsyncMock(return_value=True)
        for text in ('今天mai3chu2', '未订阅机厅mai3chu2', '12345', '今天吃饭3人', '测试机厅几人'):
            self.assertEqual(await self.report(text, save), [])
        save.assert_not_awaited()
        self.api.upload_attendance.assert_not_awaited()

    async def test_ambiguous_shop_requires_explicit_name_and_never_updates_both(self):
        self.records.append(arcade(name='另一机厅', id='124'))
        result = (await self.report('mai3chu2'))[0]
        self.assertIn('指定机厅名称或ID', result)
        self.api.upload_attendance.assert_not_awaited()

    async def test_batch_lock_prevents_interleaving_with_another_report_on_same_shop(self):
        uploads = []
        async def upload(local_id, count, selected=DEFAULT_ARCADE_GAME):
            uploads.append((selected.key, count))
            await asyncio.sleep(0.01)
            return '已同步'
        self.api.upload_attendance.side_effect = upload
        with (patch.object(mai_arcade.arcade, 'total', self.records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock(return_value=True))):
            await asyncio.gather(*(collect(mai_arcade.arcade_person_handler, event(text='测试机厅mai+1chu+1'), self.api)
                                   for _ in range(2)))
        self.assertEqual(uploads, [('mai', 3), ('chu', 1), ('mai', 4), ('chu', 2)])
