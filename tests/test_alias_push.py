import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ..command import mai_alias
from ..command.mai_alias import iter_sse, parse_alias_push, push_alias
from ..libraries import maimaidx_music
from ..libraries.maimaidx_model import AliasesPush


class AliasPushParsingTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _apply_event():
        return parse_alias_push({
            'type': 'Apply',
            'status': [
                {
                    'song_id': 11772,
                    'apply_alias': '人狂热',
                    'tag': 'JU0DE',
                    'name': '人マニア',
                    'created_at': '2026-08-25 17:07:58',
                    'agree_votes': 0,
                    'votes': 5,
                    'status': 'ongoing',
                },
            ],
        })

    def test_parses_batched_apply_event(self):
        push = parse_alias_push({
            'type': 'Apply',
            'status': [
                {
                    'song_id': 11772,
                    'apply_alias': '人狂热',
                    'tag': 'JU0DE',
                    'name': '人マニア',
                    'created_at': '2026-08-25 17:07:58',
                    'agree_votes': 0,
                    'votes': 5,
                    'status': 'ongoing',
                },
            ],
        })

        self.assertIsNotNone(push)
        self.assertEqual(push.Type, 'Apply')
        self.assertEqual(len(push.Status), 1)
        self.assertEqual(push.Status[0].ApplyAlias, '人狂热')

    def test_ignores_non_apply_event(self):
        self.assertIsNone(parse_alias_push({'type': 'End', 'status': []}))

    async def test_parses_sse_event_metadata_and_multiline_data(self):
        async def lines():
            for line in (
                b'id: event-12\n',
                b'retry: 5000\n',
                b'event: alias\n',
                b'data: {"type":"Apply",\n',
                b'data: "status":[]}\n',
                b'\n',
            ):
                yield line

        messages = [message async for message in iter_sse(lines())]

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].event, 'alias')
        self.assertEqual(messages[0].event_id, 'event-12')
        self.assertEqual(messages[0].retry, 5000)
        self.assertEqual(
            messages[0].data,
            '{"type":"Apply",\n"status":[]}',
        )

    async def test_pushes_alias_as_group_forward_message_with_guidance(self):
        bot = SimpleNamespace(
            get_group_list=AsyncMock(return_value=[{'group_id': 10001}]),
            get_login_info=AsyncMock(
                return_value={'user_id': 3889696027, 'nickname': '舞萌痴bot'}
            ),
            send_group_forward_msg=AsyncMock(),
        )
        context = SimpleNamespace(
            get_platform=lambda _: SimpleNamespace(get_client=lambda: bot)
        )
        song = SimpleNamespace(song_name='人マニア')

        with (
            patch.object(
                mai_alias.mai,
                'total_list',
                SimpleNamespace(by_id=lambda _: song),
            ),
            patch.object(
                mai_alias,
                'draw_music_info',
                AsyncMock(return_value=object()),
            ),
            patch.object(
                mai_alias,
                'convert_message_segment_to_chain',
                return_value=['chart-image'],
            ),
            patch.object(
                mai_alias,
                'convert_chain_to_onebot_format',
                AsyncMock(
                    return_value=[
                        {'type': 'image', 'data': {'file': 'base64://image'}},
                    ]
                ),
            ),
            patch.object(mai_alias.asyncio, 'sleep', AsyncMock()),
            patch.object(mai_alias.maiApi.config, 'maimaidxaliaspush', True),
            patch.object(mai_alias.maiApi.config, 'maimaidxaliaswhitelist', False),
            patch.object(mai_alias.alias.push, 'disable', []),
        ):
            await push_alias(self._apply_event(), context)

        bot.send_group_forward_msg.assert_awaited_once()
        kwargs = bot.send_group_forward_msg.await_args.kwargs
        self.assertEqual(kwargs['group_id'], 10001)
        self.assertEqual(len(kwargs['messages']), 2)
        intro = kwargs['messages'][0]['data']['content'][0]['data']['text']
        self.assertIn('同意别名 <Tag>', intro)
        self.assertIn('关闭别名推送', intro)
        self.assertEqual(
            kwargs['messages'][1]['data']['content'][0]['type'],
            'image',
        )

    async def test_whitelist_mode_only_pushes_to_enabled_groups(self):
        bot = SimpleNamespace(
            get_group_list=AsyncMock(
                return_value=[{'group_id': 10001}, {'group_id': 10002}]
            ),
            get_login_info=AsyncMock(return_value={'user_id': 3889696027}),
            send_group_forward_msg=AsyncMock(),
        )
        context = SimpleNamespace(
            get_platform=lambda _: SimpleNamespace(get_client=lambda: bot)
        )

        with (
            patch.object(
                mai_alias.mai,
                'total_list',
                SimpleNamespace(by_id=lambda _: SimpleNamespace(song_name='人マニア')),
            ),
            patch.object(
                mai_alias,
                'draw_music_info',
                AsyncMock(return_value=object()),
            ),
            patch.object(
                mai_alias,
                'convert_message_segment_to_chain',
                return_value=[],
            ),
            patch.object(
                mai_alias,
                'convert_chain_to_onebot_format',
                AsyncMock(return_value=[]),
            ),
            patch.object(mai_alias.asyncio, 'sleep', AsyncMock()),
            patch.object(mai_alias.maiApi.config, 'maimaidxaliaspush', True),
            patch.object(mai_alias.maiApi.config, 'maimaidxaliaswhitelist', True),
            patch.object(mai_alias.alias.push, 'enable', ['10002']),
        ):
            await push_alias(self._apply_event(), context)

        bot.send_group_forward_msg.assert_awaited_once()
        self.assertEqual(
            bot.send_group_forward_msg.await_args.kwargs['group_id'],
            10002,
        )

    async def test_global_disable_skips_delivery_even_for_whitelisted_group(self):
        context = SimpleNamespace(get_platform=unittest.mock.Mock())
        with patch.object(
            mai_alias.alias,
            'push',
            AliasesPush(global_enabled=False, enable=['10002']),
        ):
            await push_alias(self._apply_event(), context)
        context.get_platform.assert_not_called()

    async def test_global_disable_stops_remaining_groups_in_active_broadcast(self):
        state = AliasesPush()
        bot = SimpleNamespace(
            get_group_list=AsyncMock(return_value=[
                {'group_id': 10001}, {'group_id': 10002},
            ]),
            get_login_info=AsyncMock(return_value={'user_id': 3889696027}),
            send_group_forward_msg=AsyncMock(),
        )
        context = SimpleNamespace(
            get_platform=lambda _: SimpleNamespace(get_client=lambda: bot)
        )

        async def disable_during_interval(_):
            state.global_enabled = False

        with (
            patch.object(mai_alias.alias, 'push', state),
            patch.object(mai_alias.mai, 'total_list', SimpleNamespace(
                by_id=lambda _: SimpleNamespace(song_name='人マニア'),
            )),
            patch.object(mai_alias, 'draw_music_info', AsyncMock(return_value='chart')),
            patch.object(mai_alias.asyncio, 'sleep', disable_during_interval),
            patch.object(mai_alias.maiApi.config, 'maimaidxaliaspush', True),
            patch.object(mai_alias.maiApi.config, 'maimaidxaliaswhitelist', False),
        ):
            await push_alias(self._apply_event(), context)

        bot.send_group_forward_msg.assert_awaited_once()


class AliasGlobalSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_commands_work_without_bot_attribute_with_or_without_slash(self):
        for prefix in ('', '/'):
            for action, enabled in (('关闭', False), ('开启', True)):
                with self.subTest(prefix=prefix, action=action):
                    event = SimpleNamespace(
                        get_sender_id=lambda: '123',
                        message_str=f'{prefix}全局{action}别名推送',
                        plain_result=lambda text: text,
                    )
                    with patch.object(
                        mai_alias.alias, 'alias_global_change', AsyncMock(),
                    ) as change:
                        replies = [reply async for reply in
                                   mai_alias.alias_switch_on_off_handler(event, ['123'])]
                    change.assert_awaited_once_with(enabled)
                    self.assertIn(f'已全局{action}', replies[0])

    async def test_global_switch_requires_superuser_including_empty_admin_list(self):
        event = SimpleNamespace(
            get_sender_id=lambda: '123',
            message_str='全局关闭别名推送',
            plain_result=lambda text: text,
        )
        for admins in (None, [], ['456']):
            with self.subTest(admins=admins):
                with patch.object(
                    mai_alias.alias, 'alias_global_change', AsyncMock(),
                ) as change:
                    replies = [reply async for reply in
                               mai_alias.alias_switch_on_off_handler(event, admins)]
                change.assert_not_awaited()
                self.assertEqual(replies, ['仅允许管理员执行此操作'])

    async def test_global_state_survives_reload_and_preserves_group_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'group_alias_switch.json'
            with patch.object(maimaidx_music, 'group_alias_file', path):
                settings = maimaidx_music.GroupAlias()
                await settings.on('10001')
                await settings.off('10002')
                await settings.alias_global_change(False)
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertFalse(saved['global_enabled'])
                reloaded = maimaidx_music.GroupAlias()
                self.assertFalse(reloaded.push.global_enabled)
                self.assertEqual(reloaded.push.enable, ['10001'])
                self.assertEqual(reloaded.push.disable, ['10002'])

                # A per-group command cannot override the global shutdown.
                await reloaded.on('10003')
                self.assertFalse(reloaded.push.global_enabled)
                await reloaded.alias_global_change(True)
                settings.reload()
                self.assertTrue(settings.push.global_enabled)
                self.assertEqual(settings.push.enable, ['10001', '10003'])
                self.assertEqual(settings.push.disable, ['10002'])

    def test_legacy_group_settings_default_to_global_enabled(self):
        settings = AliasesPush.model_validate({'enable': ['10001'], 'disable': []})
        self.assertTrue(settings.global_enabled)


if __name__ == '__main__':
    unittest.main()
