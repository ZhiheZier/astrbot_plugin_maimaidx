import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from ..command import mai_arcade
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_games import ARCADE_GAMES, DEFAULT_ARCADE_GAME, resolve_arcade_game
from ..libraries.maimaidx_arcade_messages import (
    attendance_machine_count, estimate_arcade_wait, format_attendance_lines,
)
from .test_arcade_games import arcade
from .test_nearcade import client, collect, event


class ArcadeWaitEstimateTest(unittest.TestCase):
    def test_all_other_games_use_twelve_minutes_and_one_player_per_machine(self):
        for game in ARCADE_GAMES:
            result = estimate_arcade_wait(5, 2, game)
            with self.subTest(game=game.key):
                if game.family_key == 'mai':
                    self.assertEqual((result.capacity, result.average, result.minimum, result.maximum), (4, 4, 0, 16))
                else:
                    self.assertEqual((result.capacity, result.average, result.minimum, result.maximum), (2, 18, 12, 24))

    def test_named_partitions_use_their_own_game_round_settings(self):
        mai = estimate_arcade_wait(5, 2, resolve_arcade_game('maiA区'))
        chu = estimate_arcade_wait(5, 2, resolve_arcade_game('chu一楼'))
        self.assertEqual((mai.capacity, mai.average), (4, 4))
        self.assertEqual((chu.capacity, chu.average), (2, 18))
    def test_wait_counts_only_players_after_one_round_and_rounds_range_outwards(self):
        for person, machines, expected in (
            (0, 2, (4, 0, 0, 0, 0, 0)),
            (4, 2, (4, 0, 0, 0, 0, 0)),
            (5, 2, (4, 4, 0, 16, 0, 1)),
            (8, 2, (4, 16, 16, 16, 1, 1)),
            (9, 2, (4, 20, 16, 32, 1, 2)),
            (3, 1, (2, 8, 0, 16, 0, 1)),
        ):
            with self.subTest(person=person, machines=machines):
                result = estimate_arcade_wait(person, machines)
                self.assertEqual((result.capacity, result.average, result.minimum, result.maximum,
                                  result.min_rounds, result.max_rounds), expected)

    def test_unknown_or_invalid_machine_count_has_no_estimate(self):
        for person, machines in ((3, 0), (3, -1), (-1, 2), (3, None), (3, True), (True, 2), (3.5, 2)):
            self.assertIsNone(estimate_arcade_wait(person, machines))


class ArcadeMessageFormatTest(unittest.TestCase):
    def setUp(self):
        self.record = arcade()
        self.record.person = 5
        self.record.by = '测试玩家'
        self.record.time = '2026-10-19 15:00:00'
        self.today = date(2026, 10, 19)

    def format(self, game=DEFAULT_ARCADE_GAME, **kwargs):
        return '\n'.join(format_attendance_lines(self.record, game, today=self.today, **kwargs))

    def test_current_maimai_reply_includes_machine_count_wait_tip_and_updater(self):
        result = self.format(machine_count=2)
        self.assertIn('🎮 舞萌 DX：当前 5 人', result)
        self.assertIn('🕹️ 机台数量：2 台（每轮最多 4 人）', result)
        self.assertIn('预计等待：约 4 分钟（仅供参考）', result)
        self.assertIn('范围：0~16 分钟（0~1 轮）', result)
        self.assertIn('💡', result)
        self.assertIn('（测试玩家 · 2026-10-19 15:00:00）', result)

    def test_zero_is_reported_but_not_a_guarantee_of_no_wait(self):
        self.record.person = 0
        result = self.format(machine_count=2)
        self.assertIn('当前 0 人', result)
        self.assertIn('约 0 分钟', result)
        self.assertIn('现场情况', result)
        self.assertNotIn('尚未上报', result)

    def test_stale_and_unknown_dates_do_not_generate_current_wait_advice(self):
        for stamp in ('2026-10-18 23:00:00', '2026-10-20 00:00:00', '', '旧时间'):
            self.record.time = stamp
            result = self.format(machine_count=2)
            self.assertIn('暂不估算等待时间', result)
            self.assertNotIn('预计等待：约', result)
            self.assertNotIn('💡', result)

    def test_closed_status_never_promises_that_zero_means_go_play(self):
        self.record.person, self.record.status = 0, '闭店'
        result = self.format(machine_count=2)
        self.assertIn('当前 0 人（闭店）', result)
        self.assertNotIn('预计等待', result)
        self.assertNotIn('💡', result)

    def test_never_reported_zero_does_not_look_like_a_real_empty_arcade(self):
        self.record.person, self.record.by, self.record.time = 0, '', ''
        result = self.format(machine_count=2)
        self.assertIn('尚未上报', result)
        self.assertNotIn('当前 0 人', result)
        self.assertNotIn('预计等待：约', result)

    def test_other_games_and_partitions_do_not_inherit_maimai_cabinets(self):
        chu = resolve_arcade_game('chu')
        zone = resolve_arcade_game('maiA区')
        self.assertEqual(attendance_machine_count(self.record, DEFAULT_ARCADE_GAME), 2)
        self.assertIsNone(attendance_machine_count(self.record, chu))
        self.assertIsNone(attendance_machine_count(self.record, zone))
        self.assertIsNone(attendance_machine_count(self.record, DEFAULT_ARCADE_GAME, [zone]))
        self.record.game_attendance[chu.key] = a.ArcadeAttendance(person=2, by='中二玩家', time=self.record.time)
        result = self.format(chu)
        self.assertIn('中二节奏：当前 2 人', result)
        self.assertIn('机台数量未知', result)
        self.assertNotIn('每轮最多 4 人', result)
        self.assertNotIn('预计等待：约', result)
        result = self.format(chu, machine_count=2)
        self.assertIn('机台数量：2 台', result)
        self.assertIn('每轮最多 2 人', result)
        self.assertIn('每轮时间：12 分钟', result)
        self.assertIn('预计等待：约 0 分钟', result)

    def test_explicit_counts_are_independent_and_zero_disables_wait_estimation(self):
        chu, zone = resolve_arcade_game('chu'), resolve_arcade_game('maiA区')
        self.record.game_machine_counts = {'chu': 2, zone.key: 1}
        self.assertEqual(attendance_machine_count(self.record, DEFAULT_ARCADE_GAME), 2)
        self.assertEqual(attendance_machine_count(self.record, chu), 2)
        self.assertEqual(attendance_machine_count(self.record, zone), 1)
        self.record.game_machine_counts['chu'] = 0
        self.record.game_attendance['chu'] = a.ArcadeAttendance(person=3, time=self.record.time)
        result = self.format(chu, machine_count=attendance_machine_count(self.record, chu))
        self.assertIn('0 台，暂无可用机台', result)
        self.assertNotIn('预计等待：约', result)

    def test_unpartitioned_old_record_does_not_get_a_partition_total(self):
        zone = resolve_arcade_game('maiA区')
        result = a.ArcadeList.arcade_to_msg([self.record], None, {'123': [zone]})[0]
        self.assertIn('未分区旧记录', result)
        self.assertIn('maiA区：尚未上报', result)
        self.assertNotIn('机台数量：2 台', result)
        self.assertNotIn('预计等待：约', result)


class ArcadeMessageHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_group_query_lists_subscribed_shops_with_both_round_models(self):
        record, other = arcade(), arcade(name='另一机厅', id='124')
        record.game_machine_counts['chu'] = 2
        records = a.ArcadeList([record, other])
        api = client(nearcade_upload_enabled=False)
        api._request = AsyncMock()
        api.read_attendance = AsyncMock(return_value={})
        with (patch.object(mai_arcade.arcade, 'total', records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock(return_value=True))):
            await collect(mai_arcade.arcade_person_handler, event(text='测试机厅mai5chu3'), api)
            result = (await collect(mai_arcade.arcade_query_multiple_handler, event(text='机厅几人'), api))[0]
            details = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅几人'), api))[0]
        first, second = result.splitlines()
        self.assertIn('舞萌5人', first)
        self.assertIn('中二3人', first)
        self.assertNotIn('预计等待', result)
        self.assertNotIn('每轮时间', result)
        first = details
        self.assertIn('舞萌 DX：当前 5 人', first)
        self.assertIn('每轮时间：16 分钟', first)
        self.assertIn('预计等待：约 4 分钟', first)
        self.assertIn('中二节奏：当前 3 人', first)
        self.assertIn('每轮时间：12 分钟', first)
        self.assertIn('预计等待：约 6 分钟', first)
        self.assertIn('范围：0~12 分钟', first)
        self.assertNotIn('中二节奏', second)
        self.assertIn('另一机厅', second)
        api._request.assert_not_awaited()

    async def test_update_query_and_batch_share_layout_and_preserve_cloud_results(self):
        record = arcade()
        records = a.ArcadeList([record])
        api = client()
        api.upload_attendance = AsyncMock(return_value='已同步 nearcade：舞萌 DX 5 人')
        api._request = AsyncMock()
        api.read_attendance = AsyncMock(return_value={})
        with (patch.object(mai_arcade.arcade, 'total', records, create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock(return_value=True))):
            updated = (await collect(mai_arcade.arcade_person_handler, event(text='测试机厅mai5人'), api))[0]
            self.assertIn('📍 机厅：测试机厅', updated)
            self.assertIn('人数已更新为 5 人', updated)
            self.assertIn('机台数量：2 台', updated)
            self.assertIn('预计等待', updated)
            self.assertIn('（测试用户 · ', updated)
            self.assertIn('☁️ 已同步 nearcade', updated)
            queried = (await collect(mai_arcade.arcade_query_person_handler, event(text='测试机厅几人'), api))[0]
            self.assertIn('📍 机厅：测试机厅', queried)
            self.assertIn('舞萌 DX：当前 5 人', queried)
            self.assertIn('预计等待', queried)
            api._request.assert_not_awaited()
            api.upload_attendance.reset_mock()
            api.upload_attendance.side_effect = ['已同步', RuntimeError('private-token')]
            batch = (await collect(mai_arcade.arcade_person_handler, event(text='测试机厅mai6chu2'), api))[0]
            self.assertIn('舞萌 DX：本地更新成功，当前 6 人；云端同步成功', batch)
            self.assertIn('中二节奏：本地更新成功，当前 2 人；云端未确认同步', batch)
            self.assertIn('本地记录保留', batch)
            self.assertNotIn('private-token', batch)
            self.assertEqual(batch.count('预计等待：约'), 1)

    async def test_local_save_failure_has_clear_failure_marker_without_wait_or_cloud_success(self):
        record = arcade()
        api = client()
        api.upload_attendance = AsyncMock()
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock(return_value=False))):
            result = (await collect(mai_arcade.arcade_person_handler, event(text='测试机厅mai5人'), api))[0]
        self.assertIn('❌ 舞萌 DX：更新失败，本地保存失败', result)
        self.assertNotIn('预计等待', result)
        self.assertNotIn('已同步', result)
        self.assertEqual(record.person, 2)
        api.upload_attendance.assert_not_awaited()


class ArcadeMachineCountTest(unittest.IsolatedAsyncioTestCase):
    async def test_setting_game_count_does_not_create_a_fake_attendance_report(self):
        record = arcade()
        records = a.ArcadeList([record])
        with (patch.object(mai_arcade.arcade, 'total', records, create=True),
              patch.object(mai_arcade, 'is_admin', AsyncMock(return_value=True)),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock(return_value=True))):
            for text in ('修改机厅 123 chu 数量 2', '修改机厅 测试机厅 maiA区 数量 1'):
                result = (await collect(mai_arcade.modify_arcade_handler, event(text=text), []))[0]
                self.assertIn('已修改机厅', result)
        self.assertEqual(record.num, 2)
        self.assertEqual(record.game_machine_counts, {'chu': 2, 'mai:a区': 1})
        self.assertEqual(record.game_attendance, {})
        reloaded = a.Arcade.model_validate_json(record.model_dump_json())
        self.assertEqual(reloaded.game_machine_counts, record.game_machine_counts)
        self.assertIn('chu', [item.key for item in reloaded.known_games()])
        result = a.ArcadeList.arcade_to_msg([reloaded], None)[0]
        self.assertIn('中二节奏：尚未上报', result)

    async def test_legacy_records_load_and_failed_machine_count_save_restores_old_values(self):
        record = a.Arcade.model_validate(arcade().model_dump(exclude={'game_machine_counts'}))
        self.assertEqual(record.game_machine_counts, {})
        with (patch.object(a.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock(return_value=False))):
            result = await a.updata_arcade('123', '3', resolve_arcade_game('chu'))
            self.assertIn('本地保存失败', result)
            self.assertEqual(record.game_machine_counts, {})
            result = await a.updata_arcade('123', '3')
            self.assertIn('本地保存失败', result)
            self.assertEqual(record.num, 2)

    async def test_permission_and_invalid_inputs_never_modify_counts(self):
        record = arcade()
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(mai_arcade, 'is_admin', AsyncMock(return_value=False)),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock()) as save):
            result = (await collect(mai_arcade.modify_arcade_handler, event(text='修改机厅 123 chu 数量 2'), []))[0]
            self.assertIn('仅允许管理员或群管理员', result)
            save.assert_not_awaited()
        with (patch.object(mai_arcade.arcade, 'total', a.ArcadeList([record]), create=True),
              patch.object(mai_arcade, 'is_admin', AsyncMock(return_value=True)),
              patch.object(a.ArcadeList, 'save_arcade', AsyncMock()) as save):
            for text in ('修改机厅', '修改机厅 123', '修改机厅 123 chu 数量 -1',
                         '修改机厅 123 chu 数量 99999', '修改机厅 123 unknown 数量 2'):
                result = (await collect(mai_arcade.modify_arcade_handler, event(text=text), []))[0]
                self.assertIn('格式错误', result)
            save.assert_not_awaited()
