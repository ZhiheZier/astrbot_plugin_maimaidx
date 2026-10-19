import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .. import Root, __version__
from ..libraries.maimaidx_update_notice import CHANGELOG_URL, UpdateNotifier, update_message
from ..main import MaimaiDXPlugin


CHANGELOG = ('## v1.5.2\n\n### 新功能\n- 最新功能\n\n### 修复\n- 最新修复\n\n'
             '## v1.5.1\n\n### 新功能\n- 容错计算\n\n### 优化\n- 旧版优化\n\n'
             '## v1.5.0\n\n### 新功能\n- 旧功能\n')


def platform(bot, identity='qq-1'):
    return SimpleNamespace(meta=lambda: SimpleNamespace(name='aiocqhttp', id=identity), get_client=lambda: bot)


def context(*platforms, configs=None):
    return SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=list(platforms), platforms_config=configs or []))


def client(groups=(100, 200)):
    return SimpleNamespace(get_group_list=AsyncMock(return_value=[{'group_id': group} for group in groups]),
                           send_group_msg=AsyncMock())


class UpdateMessageTest(unittest.TestCase):
    def test_only_new_feature_category_is_included_and_emphasized(self):
        changelog = ('## v1.5.1\n\n### 新功能\n\n- 新功能内容\n\n'
                     '### 优化\n\n- 优化内容\n\n### 修复\n- 修复内容\n\n### 其他\n\n- 其他内容\n')
        message = update_message(changelog, None, '1.5.1')
        self.assertIn('\n\n━━ 新功能 ━━\n\n- 新功能内容', message)
        for title in ('优化', '修复', '其他'):
            self.assertNotIn(f'━━ {title} ━━', message)
            self.assertNotIn(f'- {title}内容', message)
        self.assertNotIn('###', message)

    def test_no_new_features_keeps_notice_short_without_other_categories(self):
        for section in ('### 修复\n- 修复内容', '### 新功能\n\n### 优化\n- 优化内容', '- 无分类内容'):
            with self.subTest(section=section):
                message = update_message(f'## v1.5.1\n{section}\n', '1.5.0', '1.5.1')
                self.assertIn('本次未列出新功能', message)
                self.assertNotIn('━━ 新功能 ━━', message)
                self.assertNotIn('- 修复内容', message)
                self.assertNotIn('- 优化内容', message)
                self.assertNotIn('- 无分类内容', message)

    def test_new_feature_selection_resets_between_releases_and_keeps_subheadings(self):
        changelog = ('## v1.5.3\n### 新功能\n- 未来功能\n'
                     '## v1.5.2\n### 新功能\n#### 查询\n- 查询内容\n'
                     '## v1.5.1\n- 不应继承分类\n### 修复\n- 修复内容\n'
                     '## v1.5.0\n### 新功能\n- 历史功能\n')
        message = update_message(changelog, '1.5.0', '1.5.2')
        self.assertIn('━━ 查询 ━━', message)
        self.assertIn('- 查询内容', message)
        for excluded in ('未来功能', '不应继承分类', '修复内容', '历史功能', '【v1.5.1】'):
            self.assertNotIn(excluded, message)

    def test_full_changelog_link_uses_master_branch(self):
        expected = 'https://github.com/ZhiheZier/astrbot_plugin_maimaidx/blob/master/CHANGELOG.md'
        self.assertEqual(CHANGELOG_URL, expected)
        self.assertIn(f'完整更新日志：{expected}', update_message(CHANGELOG, None, '1.5.1'))

    def test_only_updated_versions_are_included(self):
        message = update_message(CHANGELOG, '1.5.0', '1.5.2')
        self.assertIn('最新功能', message)
        self.assertIn('容错计算', message)
        self.assertNotIn('最新修复', message)
        self.assertNotIn('旧版优化', message)
        self.assertNotIn('旧功能', message)
        self.assertNotIn('最新功能', update_message(CHANGELOG, '1.5.0', '1.5.1'))
        self.assertIn('\n\n【v1.5.1】', message)

    def test_long_changelog_is_bounded_and_has_help_and_full_log(self):
        text = '## v1.5.1\n### 新功能\n' + '- 更新功能内容\n' * 600
        message = update_message(text, '1.5.0', '1.5.1')
        self.assertLessEqual(len(message), 1800)
        self.assertIn('maihelp', message)
        self.assertIn('完整更新日志', message)
        self.assertIn('更多内容', message)
        self.assertIn('\n\n功能使用说明请通过 /maihelp 查看。\n功能建议或问题反馈', message)
        self.assertIn('contact@zhihezier.com', message)
        self.assertTrue(message.endswith('https://github.com/ZhiheZier/astrbot_plugin_maimaidx/issues'))

    def test_feedback_is_last_line_for_initial_upgrade_and_missing_changelog(self):
        footer = ('功能建议或问题反馈，请发送邮件至 contact@zhihezier.com，或提交 Issue：'
                  'https://github.com/ZhiheZier/astrbot_plugin_maimaidx/issues')
        for changelog, previous in ((CHANGELOG, None), (CHANGELOG, '1.5.0'), ('', None)):
            with self.subTest(previous=previous, changelog=bool(changelog)):
                message = update_message(changelog, previous, '1.5.1')
                self.assertEqual(message.splitlines()[-1], footer)
                self.assertIn('\n\n功能使用说明请通过 /maihelp 查看。\n', message)

    def test_initial_message_contains_only_current_release(self):
        message = update_message(CHANGELOG, None, '1.5.1')
        self.assertIn('当前版本：v1.5.1', message)
        self.assertIn('容错计算', message)
        self.assertNotIn('旧功能', message)
        self.assertNotIn('最新功能', message)

    def test_missing_changelog_has_safe_fallback(self):
        self.assertIn('本次更新详情', update_message('', '1.5.0', '1.5.1'))


class UpdateNotifierTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'version.json'
        self.changelog = self.root / 'CHANGELOG.md'
        self.changelog.write_text(CHANGELOG, encoding='utf-8')

    def notifier(self, version='1.5.1'):
        return UpdateNotifier(self.path, version, self.changelog, send_delay=0)

    def seed(self, version='1.5.0', pending=None):
        self.path.write_text(json.dumps({'version': version, 'pending': pending}), encoding='utf-8')

    def state(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    async def test_first_load_records_version_and_broadcasts_current_release(self):
        bot = client()
        await self.notifier().run(context(platform(bot)), lambda group: True)
        self.assertEqual(self.state(), {'version': '1.5.1', 'pending': None})
        self.assertEqual(bot.send_group_msg.await_count, 2)
        message = bot.send_group_msg.await_args.kwargs['message'][0]['data']['text']
        self.assertIn('当前版本：v1.5.1', message)
        self.assertIn('容错计算', message)
        self.assertNotIn('旧功能', message)
        await self.notifier().run(context(platform(bot)), lambda group: True)
        self.assertEqual(bot.send_group_msg.await_count, 2)

    async def test_first_broadcast_resumes_failed_groups_after_restart(self):
        notice = self.notifier()
        self.assertTrue(notice._prepare())
        bot = client()
        bot.send_group_msg.side_effect = [None, RuntimeError('offline')]
        self.assertFalse(await notice.send_pending(context(platform(bot)), lambda group: True))
        self.assertTrue(self.state()['pending']['initial'])
        self.assertEqual(self.state()['pending']['delivered'], ['qq-1:100'])
        resumed = self.notifier()
        self.assertTrue(resumed._prepare())
        bot.send_group_msg.reset_mock(side_effect=True)
        self.assertTrue(await resumed.send_pending(context(platform(bot)), lambda group: True))
        bot.send_group_msg.assert_awaited_once()
        self.assertEqual(bot.send_group_msg.await_args.kwargs['group_id'], 200)
        self.assertIn('当前版本：', bot.send_group_msg.await_args.kwargs['message'][0]['data']['text'])
        self.assertIsNone(self.state()['pending'])

    async def test_broadcast_respects_feature_whitelist_and_disabled_groups(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.config = {'maimaidxwhitelist': True}
        plugin.enabled_groups = {'100', '200'}
        plugin.disabled_groups = {'200'}
        bot = client((100, 200, 300))
        await self.notifier().run(context(platform(bot)), plugin._is_group_enabled)
        bot.send_group_msg.assert_awaited_once()
        self.assertEqual(bot.send_group_msg.await_args.kwargs['group_id'], 100)

    async def test_equal_version_and_downgrade_never_broadcast_or_overwrite(self):
        self.seed('1.5.2')
        for version in ('1.5.2', '1.5.1'):
            bot = client()
            await self.notifier(version).run(context(platform(bot)), lambda group: True)
            bot.get_group_list.assert_not_awaited()
            self.assertEqual(self.state()['version'], '1.5.2')

    async def test_semantic_version_comparison(self):
        self.seed('1.9.0')
        notice = self.notifier('1.10.0')
        self.assertTrue(notice._prepare())
        self.assertEqual(self.state()['pending']['version'], '1.10.0')

    async def test_upgrade_excludes_disabled_groups_and_deduplicates_groups(self):
        self.seed()
        notice = self.notifier()
        self.assertTrue(notice._prepare())
        bot = client((100, 200, 100))
        self.assertTrue(await notice.send_pending(context(platform(bot)), lambda group: group != '200'))
        self.assertEqual(bot.send_group_msg.await_count, 1)
        arguments = bot.send_group_msg.await_args.kwargs
        self.assertEqual(arguments['group_id'], 100)
        self.assertEqual(arguments['message'][0]['type'], 'text')
        self.assertIn('容错计算', arguments['message'][0]['data']['text'])
        self.assertEqual(self.state()['version'], '1.5.1')

    async def test_restart_retries_only_failed_groups(self):
        self.seed()
        notice = self.notifier()
        notice._prepare()
        bot = client()
        bot.send_group_msg.side_effect = [None, RuntimeError('offline')]
        self.assertFalse(await notice.send_pending(context(platform(bot)), lambda group: True))
        self.assertEqual(self.state()['version'], '1.5.0')
        self.assertEqual(self.state()['pending']['delivered'], ['qq-1:100'])
        resumed = self.notifier()
        self.assertTrue(resumed._prepare())
        bot.send_group_msg.reset_mock(side_effect=True)
        self.assertTrue(await resumed.send_pending(context(platform(bot)), lambda group: True))
        bot.send_group_msg.assert_awaited_once()
        self.assertEqual(bot.send_group_msg.await_args.kwargs['group_id'], 200)
        self.assertIsNone(self.state()['pending'])

    async def test_platform_is_not_ready_then_recovers(self):
        self.seed()
        notice = self.notifier()
        notice._prepare()
        self.assertFalse(await notice.send_pending(context(), lambda group: True))
        bot = client((100,))
        bot.get_group_list.side_effect = RuntimeError('not connected')
        self.assertFalse(await notice.send_pending(context(platform(bot)), lambda group: True))
        self.assertEqual(self.state()['version'], '1.5.0')
        bot.get_group_list.side_effect = None
        self.assertTrue(await notice.send_pending(context(platform(bot)), lambda group: True))

    async def test_all_configured_adapters_must_load_before_completing(self):
        self.seed()
        notice = self.notifier()
        notice._prepare()
        bot1, bot2 = client((100,)), client((100,))
        configs = [{'id': name, 'type': 'aiocqhttp', 'enable': True} for name in ('qq-1', 'qq-2')]
        ctx = context(platform(bot1), configs=configs)
        self.assertFalse(await notice.send_pending(ctx, lambda group: True))
        ctx.platform_manager.platform_insts.append(platform(bot2, 'qq-2'))
        self.assertTrue(await notice.send_pending(ctx, lambda group: True))
        self.assertEqual(bot1.send_group_msg.await_count, 1)
        self.assertEqual(bot2.send_group_msg.await_count, 1)

    async def test_closed_group_is_rechecked_during_delivery(self):
        self.seed()
        notice = self.notifier()
        notice._prepare()
        disabled = set()
        bot = client()

        async def sent(**kwargs):
            disabled.add('200')

        bot.send_group_msg.side_effect = sent
        self.assertTrue(await notice.send_pending(context(platform(bot)), lambda group: group not in disabled))
        self.assertEqual(bot.send_group_msg.await_count, 1)

    async def test_concurrent_delivery_does_not_duplicate(self):
        self.seed()
        notice = self.notifier()
        notice._prepare()
        bot = client((100,))
        ctx = context(platform(bot))
        self.assertEqual(await asyncio.gather(*(notice.send_pending(ctx, lambda group: True) for _ in range(2))), [True, True])
        self.assertEqual(bot.send_group_msg.await_count, 1)

    async def test_malformed_record_is_preserved_and_does_not_broadcast(self):
        for data in ('bad json', '[]', '{"version":"invalid"}',
                     '{"version":"1.5.0","pending":{"version":"1.5.1","delivered":true}}'):
            self.path.write_text(data, encoding='utf-8')
            bot = client()
            with patch('astrbot.api.logger.error'):
                await self.notifier().run(context(platform(bot)), lambda group: True)
            self.assertEqual(self.path.read_text(encoding='utf-8'), data)
            bot.send_group_msg.assert_not_awaited()

    async def test_downgrade_during_pending_higher_release_keeps_checkpoint(self):
        pending = {'version': '1.5.2', 'delivered': ['qq-1:100']}
        self.seed(pending=pending)
        self.assertFalse(self.notifier('1.5.1')._prepare())
        self.assertEqual(self.state()['pending'], pending)

    async def test_retry_loop_and_cancellation_are_managed(self):
        self.seed()
        notice = self.notifier()
        with (patch.object(notice, 'send_pending', AsyncMock(side_effect=[False, True])) as send,
              patch('data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_update_notice.asyncio.sleep', AsyncMock()) as sleep):
            await notice.run(context(), lambda group: True)
            self.assertEqual(send.await_count, 2)
            sleep.assert_awaited_once_with(60)
        with patch.object(notice, 'send_pending', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await notice.run(context(), lambda group: True)


class UpdateLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_dependency_is_declared_and_keeps_v3_api(self):
        from packaging.requirements import Requirement

        dependencies = [Requirement(line) for line in (Root / 'requirements.txt').read_text(encoding='utf-8').splitlines()
                        if line.strip() and not line.startswith('#')]
        scheduler = next(requirement for requirement in dependencies if requirement.name.lower() == 'apscheduler')
        self.assertIn('3.10', scheduler.specifier)
        self.assertNotIn('4.0', scheduler.specifier)

    async def test_hooks_share_single_task_and_terminate_cancels_it(self):
        started = asyncio.Event()

        async def run(*args):
            started.set()
            await asyncio.Event().wait()

        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        plugin.config = {}
        plugin._group_settings_valid = True
        plugin.update_notice_task = None
        plugin.update_notifier = SimpleNamespace(run=AsyncMock(side_effect=run))
        plugin.context = context()
        plugin.disabled_groups = set()
        plugin.alias_push_task = None
        plugin.scheduler = SimpleNamespace(running=False)
        await plugin.update_notice_on_startup()
        await started.wait()
        task = plugin.update_notice_task
        await plugin.update_notice_on_platform()
        self.assertIs(plugin.update_notice_task, task)
        self.assertEqual(plugin.update_notifier.run.call_count, 1)
        await plugin.terminate()
        self.assertTrue(task.cancelled())

    async def test_disabled_option_or_invalid_group_settings_never_start_task(self):
        for config, valid in (({'update_notice': False}, True), ({}, False)):
            plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
            plugin.config = config
            plugin._group_settings_valid = valid
            plugin.update_notice_task = None
            plugin._start_update_notice()
            self.assertIsNone(plugin.update_notice_task)

    async def test_disabled_groups_normalize_ids_and_invalid_data_blocks_broadcast(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
            plugin.data_file = Path(directory) / 'disabled_groups.json'
            plugin.data_file.write_text('{"disabled_groups":[200,"300"]}', encoding='utf-8')
            plugin._load_disabled_groups()
            self.assertFalse(plugin._is_group_enabled('200'))
            self.assertFalse(plugin._is_group_enabled('300'))
            self.assertTrue(plugin._group_settings_valid)
            plugin.data_file.write_text('[]', encoding='utf-8')
            plugin._load_disabled_groups()
            self.assertFalse(plugin._group_settings_valid)

    async def test_release_version_matches_metadata(self):
        import yaml

        metadata = yaml.safe_load((Root / 'metadata.yaml').read_text(encoding='utf-8'))
        self.assertEqual(metadata['version'].removeprefix('v'), __version__)
        self.assertEqual((Root / 'CHANGELOG.md').read_text(encoding='utf-8').splitlines()[0],
                         f'## v{__version__}')
