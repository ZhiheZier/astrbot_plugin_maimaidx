from math import ceil
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from .. import (
    FOTNEWRODIN,
    SIYUAN,
    MessageSegment,
    get_botname,
    maimaidir,
)
from .image import (
    DrawText,
    draw_text_with_font_fallback,
    generate_frosted_card,
    image_to_base64,
    music_picture,
    tricolor_gradient_prism_plus,
)
from .maimaidx_model import Song


SONG_LIST_PAGE_SIZE = 14


def paginate_songs(
    songs: Sequence[Song],
    page: int,
) -> tuple[list[Song], int, int]:
    """返回当前页曲目、校正后的页码和总页数。"""
    total_page = max(1, ceil(len(songs) / SONG_LIST_PAGE_SIZE))
    page = max(1, min(int(page), total_page))
    start = (page - 1) * SONG_LIST_PAGE_SIZE
    return list(songs[start:start + SONG_LIST_PAGE_SIZE]), page, total_page


def _open_rgba(path, size=None) -> Image.Image | None:
    if not path.exists():
        return None
    image = Image.open(path).convert('RGBA')
    return image.resize(size, Image.Resampling.LANCZOS) if size else image


def _truncate(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path,
    size: int,
    max_width: int,
) -> str:
    font = ImageFont.truetype(str(font_path), size)
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = '...'
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return text + suffix


def build_difficulty_strip(
    background: Image.Image,
    level_indices: Sequence[int],
) -> Image.Image:
    """仅保留歌曲实际存在的难度色块。"""
    slot_count = 5
    slot_width = background.width // slot_count
    strip = Image.new('RGBA', background.size, (0, 0, 0, 0))
    for level_index in sorted(set(level_indices)):
        if not 0 <= level_index < slot_count:
            continue
        left = slot_width * level_index
        segment = background.crop((left, 0, left + slot_width, background.height))
        strip.alpha_composite(segment, (left, 0))
    return strip


def draw_song_list(
    songs: Sequence[Song],
    page: int = 1,
    *,
    title: str = '曲目列表',
) -> MessageSegment:
    """绘制带封面、版本、类型和各难度定数的曲目列表。"""
    page_songs, page, total_page = paginate_songs(songs, page)
    rows = max(1, ceil(len(page_songs) / 2))
    height = 400 + rows * 145

    base = tricolor_gradient_prism_plus(1000, height).convert('RGBA')
    aurora = _open_rgba(maimaidir / 'aurora.png', (1000, 174))
    shines = _open_rgba(maimaidir / 'bg_shines.png', (1000, 442))
    pattern = _open_rgba(maimaidir / 'pattern.png', (1000, 256))
    rainbow = _open_rgba(maimaidir / 'rainbow.png', (550, 288))
    rainbow_bottom = _open_rgba(maimaidir / 'rainbow_bottom.png', (786, 164))
    for image, pos in (
        (aurora, (0, 0)),
        (shines, (0, 0)),
        (rainbow, (225, height - 435)),
        (rainbow_bottom, (107, height - 260)),
    ):
        if image:
            base.alpha_composite(image, pos)
    if pattern:
        for y in range(0, height, 262):
            base.alpha_composite(pattern, (0, y))

    panel_bottom = 250 + rows * 145
    image = generate_frosted_card(base, (50, 150, 950, panel_bottom), alpha=0.2)
    draw = ImageDraw.Draw(image)
    cn = DrawText(draw, SIYUAN)
    number = DrawText(draw, FOTNEWRODIN)
    text_color = (124, 129, 255, 255)

    chara = _open_rgba(maimaidir / 'prism_plus' / 'chara_left.png', (156, 187))
    moon = _open_rgba(maimaidir / 'moon.png', (120, 120))
    logo = _open_rgba(maimaidir / 'maimai でらっくす PRiSM PLUS.png', (210, 101))
    for asset, pos in ((chara, (800, 0)), (moon, (60, 20)), (logo, (15, 20))):
        if asset:
            image.alpha_composite(asset, pos)

    card_bg = _open_rgba(maimaidir / 'song_card.png')
    diff_bg = _open_rgba(maimaidir / 'sl_diff.png')
    banquet_diff_bg = _open_rgba(maimaidir / 'sl_diff_utg.png')
    for index, song in enumerate(page_songs):
        row, column = divmod(index, 2)
        x = 70 + column * 450
        y = 200 + row * 145
        if card_bg:
            image.alpha_composite(card_bg, (x, y))

        cover = _open_rgba(music_picture(song.song_id), (80, 80))
        version = _open_rgba(maimaidir / f'{song.version_str}.png', (104, 50))
        song_type = _open_rgba(maimaidir / f'{song.type.upper()}.png', (40, 15))
        for asset, pos in (
            (cover, (x + 10, y + 10)),
            (version, (x + 315, y - 30)),
            (song_type, (x + 50, y + 75)),
        ):
            if asset:
                image.alpha_composite(asset, pos)

        is_banquet = song.song_id >= 100000
        strip = banquet_diff_bg
        if not is_banquet and diff_bg:
            strip = build_difficulty_strip(
                diff_bg,
                [difficulty.level_index for difficulty in song.difficulties],
            )
        if strip:
            image.alpha_composite(strip, (x + 100, y + 95))

        number.draw(x + 50, y + 105, 15, song.song_id, text_color, 'mm')
        song_name = _truncate(draw, song.song_name, SIYUAN, 20, 285)
        artist = _truncate(draw, song.artist, SIYUAN, 12, 280)
        cn.draw(x + 100, y + 25, 20, song_name, text_color, 'lm')
        cn.draw(x + 100, y + 50, 12, artist, text_color, 'lm')
        number.draw(x + 100, y + 80, 15, f'BPM: {song.bpm:g}', text_color, 'lm')
        cn.draw(x + 230, y + 80, 12, song.genre, text_color, 'lm')

        difficulties = song.difficulties[:1] if is_banquet else song.difficulties
        for difficulty in difficulties:
            color = (138, 0, 226, 255) if difficulty.level_index == 4 else (255, 255, 255, 255)
            number.draw(
                x + 125 + 50 * difficulty.level_index,
                y + 105,
                15,
                difficulty.level_value,
                color,
                'mm',
            )

    draw_text_with_font_fallback(
        draw,
        500,
        70,
        55,
        title,
        text_color,
        FOTNEWRODIN,
        SIYUAN,
        'mm',
        3,
        (255, 255, 255, 255),
    )
    number.draw(500, height - 100, 35, f'Page {page}/{total_page}', text_color, 'mm', 3, (255, 255, 255, 255))
    draw_text_with_font_fallback(
        draw,
        500,
        height - 30,
        18,
        f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT',
        text_color,
        FOTNEWRODIN,
        SIYUAN,
        'mm',
        3,
        (255, 255, 255, 255),
    )
    return MessageSegment.image(image_to_base64(image))
