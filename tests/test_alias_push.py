import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ..command import mai_alias
from ..command.mai_alias import iter_sse, parse_alias_push, push_alias


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


if __name__ == '__main__':
    unittest.main()
