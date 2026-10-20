"""Song ranking cards, using the same local achievement icons as B50."""

import asyncio
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import DIFFS, SHANGGUMONO, SIYUAN, MessageSegment, fcl, fsl, get_botname, score_Rank_l
from .image import image_to_base64, music_picture
from .maimai_best_50 import computeRa, themed_path
from .maimaidx_user import Theme


RANKING_PAGE_SIZE = 20
COLORS = ('#67bb55', '#d3a523', '#dc6478', '#9856c9', '#b47acb')
TINTS = ('#d6f7d1', '#fff1bb', '#ffd9df', '#e9d8f7', '#f2e3ff')
EDGES = (16, 138, 416, 608, 750, 870, 1008)


def dx_star_thresholds(difficulty):
    maximum = difficulty.dx_score
    if maximum <= 0:
        notes = difficulty.notes
        maximum = (notes.total or sum((notes.tap, notes.hold, notes.slide, notes.touch, notes.brk))) * 3
    if maximum <= 0:
        return None
    return maximum, tuple((maximum * percent + 99) // 100 for percent in (85, 90, 93, 95, 97))


@lru_cache(maxsize=32)
def _font(size):
    for path in (SHANGGUMONO, SIYUAN):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise FileNotFoundError('排行榜绘图缺少中文字体')


def _lines(text, font, width):
    lines, current = [], ''
    for char in str(text).replace('\r', ''):
        if char == '\n':
            lines.append(current)
            current = ''
        elif current and font.getlength(current + char) > width:
            lines.append(current)
            current = char
        else:
            current += char
    return lines + [current]


def _text(draw, text, box, *, size=24, color='#333341', max_lines=1, align='center'):
    x0, y0, x1, y1 = box
    for chosen in range(size, 16, -1):
        font = _font(chosen)
        lines = _lines(text, font, x1 - x0)
        if len(lines) <= max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + '...') > x1 - x0:
            lines[-1] = lines[-1][:-1]
        lines[-1] += '...'
    height = chosen + 4
    top = (y0 + y1 - height * len(lines)) / 2
    for index, line in enumerate(lines):
        x, anchor = (x0, 'lm') if align == 'left' else ((x0 + x1) / 2, 'mm')
        draw.text((x, top + height * (index + .5)), line, font=font, fill=color, anchor=anchor)


def _icon(image, filename, box):
    if not filename:
        return False
    path = themed_path(Theme.PRISM_PLUS, filename)
    try:
        with Image.open(path) as source:
            icon = ImageOps.contain(source.convert('RGBA'), (box[2] - box[0], box[3] - box[1]),
                                    Image.Resampling.LANCZOS)
        x = box[0] + (box[2] - box[0] - icon.width) // 2
        y = box[1] + (box[3] - box[1] - icon.height) // 2
        image.paste(icon, (x, y), icon)
        return True
    except (OSError, ValueError):
        return False


def render_ranking_page(song, difficulty, result, cover, page=1):
    start = (page - 1) * RANKING_PAGE_SIZE
    rows = result.rows[start:start + RANKING_PAGE_SIZE]
    pages = max(1, (len(result.rows) + RANKING_PAGE_SIZE - 1) // RANKING_PAGE_SIZE)
    height = 300 + max(1, len(rows)) * 70 + 94
    image = Image.new('RGB', (1024, height), '#f8f8fc')
    draw = ImageDraw.Draw(image)
    if cover is not None:
        with Image.open(cover) as source:
            image.paste(ImageOps.fit(source.convert('RGB'), (196, 196), Image.Resampling.LANCZOS), (24, 24))
    else:
        draw.rectangle((24, 24, 220, 220), fill='#e7e7ef')
        _text(draw, str(song.song_id), (24, 24, 220, 220), color='#777786')
    _text(draw, song.song_name, (242, 18, 728, 104), size=30, max_lines=2, align='left')
    _text(draw, f'ID: {song.song_id} | {song.type} | {DIFFS[difficulty.level_index]}',
          (242, 106, 728, 142), size=20, color='#777786', align='left')
    for offset, item in enumerate(sorted(song.difficulties, key=lambda item: item.level_index)):
        left, top = 242 + offset * 86, 158
        index = item.level_index
        selected = index == difficulty.level_index
        draw.rounded_rectangle((left, top, left + 72, top + 64), radius=8,
                               fill=TINTS[index], outline=COLORS[index] if selected else None,
                               width=3 if selected else 1)
        value = f'{item.level_value:.1f}' if item.level_value else item.level
        _text(draw, value, (left + 3, top, left + 69, top + 64), size=24, color=COLORS[index])
    draw.line((748, 24, 748, 222), fill='#d6d6e3')
    _text(draw, 'DX分阈值', (770, 12, 1000, 40), size=19, color='#565669', align='left')
    thresholds = dx_star_thresholds(difficulty)
    if thresholds is None:
        _text(draw, '暂无物量数据', (770, 48, 1000, 90), size=18, color='#888898', align='left')
    else:
        maximum, minimums = thresholds
        _text(draw, f'满分 {maximum}', (770, 40, 1000, 62), size=17, color='#888898', align='left')
        for star, minimum in enumerate(minimums, 1):
            top = 66 + (star - 1) * 31
            if not _icon(image, f'UI_GAM_Gauge_DXScoreIcon_0{star}.png', (770, top, 834, top + 27)):
                _text(draw, f'{star}星', (770, top, 834, top + 27), size=18, align='left')
            _text(draw, f'≥ {minimum}', (850, top, 1000, top + 27), size=18, align='left')
    draw.rectangle((0, 240, 1023, 290), fill='#ededf4')
    for index, label in enumerate(('排名', '玩家', '成绩', 'DX分', 'FC/FS', '评级')):
        _text(draw, label, (EDGES[index], 240, EDGES[index + 1], 290), size=23, color='#565669')
    for offset, row in enumerate(rows):
        top = 296 + offset * 70
        draw.rounded_rectangle((16, top, 1008, top + 60), radius=8,
                               fill='#ffffff' if offset % 2 == 0 else '#f4f4fa', outline='#d6d6e3')
        medal = ('#d4ad00', '#9a9ba4', '#cf7b35')
        _text(draw, str(row.rank), (EDGES[0], top, EDGES[1], top + 60),
              color=medal[row.rank - 1] if 1 <= row.rank <= 3 else '#656578')
        _text(draw, row.name, (EDGES[1] + 8, top + 3, EDGES[2] - 8, top + 57), max_lines=2)
        _text(draw, f'{row.score.achievements:.4f}%', (EDGES[2], top, EDGES[3], top + 60))
        _text(draw, str(row.score.dx_score), (EDGES[3], top, EDGES[4], top + 60))
        for value, mapping, left in ((row.score.fc, fcl, 774), (row.score.fs, fsl, 816)):
            asset = mapping.get(value or '')
            if asset and not _icon(image, f'UI_MSS_MBase_Icon_{asset}.png', (left, top + 12, left + 36, top + 48)):
                _text(draw, asset.replace('p', '+'), (left - 2, top, left + 38, top + 60), size=18)
        rate = (row.score.rate or computeRa(difficulty.level_value, row.score.achievements, israte=True)[1]).lower()
        asset = score_Rank_l.get(rate)
        if not _icon(image, f'UI_TTR_Rank_{asset}.png' if asset else '', (884, top + 10, 978, top + 50)):
            _text(draw, rate.upper().replace('P', '+'), (EDGES[5], top, EDGES[6], top + 60))
    if not rows:
        _text(draw, '本群暂无已加入且有该谱面成绩的玩家', (24, 300, 1000, 370), size=24)
    footer_y = height - 83
    draw.line((50, footer_y, 974, footer_y), fill='#ccccdd')
    summary = f'本群上榜 {len(result.rows)} 人  |  未游玩 {result.unplayed} 人  |  查询失败 {result.failed} 人'
    _text(draw, summary, (24, footer_y + 7, 1000, footer_y + 38), size=18, color='#888898')
    _text(draw, f'Generated by {get_botname()} BOT  |  Pages {page}/{pages}',
          (24, footer_y + 40, 1000, height - 5), size=18, color='#888898')
    return image


async def draw_song_ranking(song, difficulty, result):
    try:
        cover = await music_picture(song.song_id)
    except OSError:
        cover = None

    def render():
        pages = max(1, (len(result.rows) + RANKING_PAGE_SIZE - 1) // RANKING_PAGE_SIZE)
        pictures = []
        for page in range(1, pages + 1):
            image = render_ranking_page(song, difficulty, result, cover, page)
            pictures.append(MessageSegment.image(image_to_base64(image)))
        return pictures

    return await asyncio.to_thread(render)
