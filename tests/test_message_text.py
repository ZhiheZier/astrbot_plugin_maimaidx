import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp

from ..command import mai_score
from ..command.mai_base import extract_at_qqid, get_plain_message_text
from ..command.mai_score import best50_handler


class PlainMessageTextTest(unittest.TestCase):
    def test_ignores_at_component_in_alias_query(self):
        event = SimpleNamespace(
            message_str='@舞萌痴bot(3889696027) 11860是什么歌',
            message_obj=SimpleNamespace(
                message=[
                    Comp.At(qq='3889696027', name='舞萌痴bot'),
                    Comp.Plain(' 11860是什么歌'),
                ]
            ),
        )

        self.assertEqual(get_plain_message_text(event), '11860是什么歌')

    def test_preserves_plain_text_around_mentions(self):
        event = SimpleNamespace(
            message_str='foo @user(123) bar是什么歌',
            message_obj=SimpleNamespace(
                message=[
                    Comp.Plain('foo '),
                    Comp.At(qq='123', name='user'),
                    Comp.Plain(' bar是什么歌'),
                ]
            ),
        )

        self.assertEqual(get_plain_message_text(event), 'foo  bar是什么歌')

    def test_falls_back_to_message_str_without_message_object(self):
        event = SimpleNamespace(message_str='11860是什么歌')

        self.assertEqual(get_plain_message_text(event), '11860是什么歌')

    def test_extracts_at_target_separately(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                message=[Comp.Plain('b50 '), Comp.At(qq='123456')]
            )
        )

        self.assertEqual(extract_at_qqid(event), '123456')


class AtTargetCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_b50_keeps_at_target_out_of_command_arguments(self):
        event = SimpleNamespace(
            message_str='b50 @target(123456)',
            message_obj=SimpleNamespace(
                message_id='message-id',
                message=[Comp.Plain('b50 '), Comp.At(qq='123456', name='target')],
            ),
            get_sender_id=lambda: '654321',
            chain_result=lambda chain: chain,
        )
        generate = AsyncMock(return_value='ok')

        with (
            patch.object(mai_score.mai, 'total_list', [object()]),
            patch.object(mai_score, 'generate', generate),
            patch.object(mai_score, 'is_reply_enabled', return_value=False),
        ):
            results = [result async for result in best50_handler(event)]

        self.assertEqual(len(results), 1)
        generate.assert_awaited_once()
        self.assertEqual(generate.await_args.args[:2], ('123456', ''))


if __name__ == '__main__':
    unittest.main()
