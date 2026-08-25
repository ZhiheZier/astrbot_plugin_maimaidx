import unittest

from ..command.mai_alias import parse_alias_pushes


class AliasPushParsingTest(unittest.TestCase):
    def test_parses_legacy_single_event(self):
        pushes = parse_alias_pushes({
            'type': 'Apply',
            'status': {
                'SongID': 11772,
                'ApplyUID': 3353863748,
                'ApplyAlias': '人狂热',
                'Tag': 'JU0DE',
                'Name': '人マニア',
                'Time': '2026-08-25 17:07:58',
                'AgreeVotes': 0,
                'Votes': 5,
            },
        })

        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0].Type, 'Apply')
        self.assertEqual(pushes[0].Status.ApplyAlias, '人狂热')

    def test_ignores_new_status_list_message(self):
        pushes = parse_alias_pushes({
            'type': 'Status',
            'status': [
                {
                    'apply_alias': '人狂热',
                    'status': 'ongoing',
                },
            ],
        })

        self.assertEqual(pushes, [])


if __name__ == '__main__':
    unittest.main()
