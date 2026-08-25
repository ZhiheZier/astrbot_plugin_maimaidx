import unittest

from ..command.mai_alias import iter_sse, parse_alias_push


class AliasPushParsingTest(unittest.IsolatedAsyncioTestCase):
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


if __name__ == '__main__':
    unittest.main()
