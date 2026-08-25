import unittest
from unittest.mock import patch

from ..command import mai_score
from ..command.mai_score import score_handler
from ..libraries.maimaidx_model import Difficulties, Notes, Song
from ..libraries.maimaidx_music import SongList, mai


class FakeEvent:
    def __init__(self, message: str):
        self.message_str = message

    @staticmethod
    def plain_result(message: str) -> str:
        return message


async def collect_results(message: str) -> list[str]:
    return [result async for result in score_handler(FakeEvent(message))]


def make_song() -> Song:
    difficulties = [
        Difficulties(
            level_index=index,
            level=str(index + 1),
            level_value=float(index + 1),
            notes=Notes(total=5, tap=1, hold=1, slide=1, touch=1, brk=1),
        )
        for index in range(4)
    ]
    return Song(song_id=799, song_name='test', type='DX', difficulties=difficulties)


class ScoreLineTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_format_returns_help_without_logging_exception(self):
        with (
            patch.object(mai, 'total_list', SongList([make_song()])),
            patch.object(mai_score.log, 'exception') as exception_log,
        ):
            results = await collect_results('分数线 13 99')

        self.assertEqual(results, ['格式错误，输入"分数线 帮助"以查看帮助信息'])
        exception_log.assert_not_called()

    async def test_valid_command_still_calculates(self):
        with patch.object(mai, 'total_list', SongList([make_song()])):
            results = await collect_results('分数线 紫799 100')

        self.assertEqual(len(results), 1)
        self.assertIn('test「Master」', results[0])
        self.assertIn('分数线「100.0%」', results[0])


if __name__ == '__main__':
    unittest.main()
