import unittest

from PIL import Image

from ..libraries.maimaidx_model import Song
from ..libraries.maimaidx_song_list import (
    SONG_LIST_PAGE_SIZE,
    build_difficulty_strip,
    paginate_songs,
)


def make_song(song_id: int) -> Song:
    return Song(song_id=song_id, song_name=f'song-{song_id}')


class SongListImageTest(unittest.TestCase):
    def test_pagination_uses_fourteen_songs_per_page(self):
        songs = [make_song(index) for index in range(30)]

        first, page, total = paginate_songs(songs, 1)
        self.assertEqual(len(first), SONG_LIST_PAGE_SIZE)
        self.assertEqual((page, total), (1, 3))

        last, page, total = paginate_songs(songs, 99)
        self.assertEqual([song.song_id for song in last], [28, 29])
        self.assertEqual((page, total), (3, 3))

    def test_difficulty_strip_hides_missing_remaster_slot(self):
        background = Image.new('RGBA', (250, 20), (255, 255, 255, 255))

        strip = build_difficulty_strip(background, [0, 1, 2, 3])

        self.assertEqual(strip.getpixel((175, 10))[3], 255)
        self.assertEqual(strip.getpixel((225, 10))[3], 0)

    def test_difficulty_strip_keeps_existing_remaster_slot(self):
        background = Image.new('RGBA', (250, 20), (255, 255, 255, 255))

        strip = build_difficulty_strip(background, [0, 1, 2, 3, 4])

        self.assertEqual(strip.getpixel((225, 10))[3], 255)


if __name__ == '__main__':
    unittest.main()
