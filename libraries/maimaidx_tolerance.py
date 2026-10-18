"""Per-judgement achievement losses and their table image."""

from dataclasses import dataclass
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont

from .. import SIYUAN, TBFONT, MessageSegment, get_botname
from .image import image_to_base64, music_picture
from .maimaidx_model import Difficulties, Notes, Song


NOTE_WEIGHTS = {'Tap': 500, 'Hold': 1000, 'Slide': 1500, 'Touch': 500, 'Break': 2500}
DIFFICULTY_NAMES = ('Basic', 'Advanced', 'Expert', 'Master', 'Re:Master')
DIFFICULTY_COLORS = ('#75bc4f', '#e9b71d', '#ef7085', '#9950c6', '#bd90dd')


@dataclass(frozen=True)
class ToleranceData:
    base_score: int
    break_count: int
    counts: dict[str, int]
    normal_losses: dict[str, tuple[Fraction, ...]]
    break_losses: tuple[Fraction, ...]


def calculate_tolerance(notes: Notes) -> ToleranceData:
    counts = {
        'Tap': notes.tap, 'Hold': notes.hold, 'Slide': notes.slide,
        'Touch': notes.touch, 'Break': notes.brk,
    }
    if any(count < 0 for count in counts.values()):
        raise ValueError('谱面物量不能为负数')
    base_score = sum(counts[kind] * weight for kind, weight in NOTE_WEIGHTS.items())
    if base_score <= 0:
        raise ValueError('该谱面暂无有效物量，无法计算容错')
    normal = {
        kind: tuple(Fraction(weight * ratio * 100, base_score) for ratio in
                    (Fraction(0), Fraction(1, 5), Fraction(1, 2), Fraction(1)))
        for kind, weight in NOTE_WEIGHTS.items() if kind != 'Break'
    }
    breaks = counts['Break']
    # Percentage points: the 100% base and the 1% BREAK bonus are independent.
    break_losses = tuple(
        Fraction(base_loss * 100, base_score) + bonus_loss / breaks
        for base_loss, bonus_loss in (
            (0, Fraction(0)), (0, Fraction(1, 4)), (0, Fraction(1, 2)),
            (500, Fraction(3, 5)), (1000, Fraction(3, 5)),
            (1250, Fraction(3, 5)), (1500, Fraction(7, 10)), (2500, Fraction(1)),
        )
    ) if breaks else ()
    return ToleranceData(base_score, breaks, counts, normal, break_losses)


def tolerance_values(data: ToleranceData, mode: str = '101-'):
    if mode not in ('101-', '100-', '0+'):
        raise ValueError('不支持的分数模式')
    normal = {
        kind: tuple((Fraction(NOTE_WEIGHTS[kind] * 100, data.base_score)
                     if mode == '0+' else 0) - loss for loss in losses)
        for kind, losses in data.normal_losses.items()
    }
    break_start = Fraction(0)
    if data.break_count and mode != '101-':
        break_start = Fraction(1, data.break_count)
        if mode == '0+':
            break_start += Fraction(2500 * 100, data.base_score)
    return normal, tuple(break_start - loss for loss in data.break_losses)


def format_change(value: Fraction) -> str:
    return f'{float(value):+.6f}%' if value else '0.000000%'


def _text(draw, box, text, *, size=24, color='#ffffff', center=False):
    left, top, right, bottom = box
    font_path = SIYUAN if any(not char.isascii() for char in text) else TBFONT
    while True:
        font = ImageFont.truetype(str(font_path), size)
        if draw.textlength(text, font=font) <= right - left or size <= 12:
            break
        size -= 1
    if draw.textlength(text, font=font) > right - left:
        while text and draw.textlength(text + '...', font=font) > right - left:
            text = text[:-1]
        text += '...'
    draw.text(
        ((left + right) / 2 if center else left, (top + bottom) / 2),
        text, font=font, fill=color, anchor='mm' if center else 'lm',
    )


async def _draw_chart_header(image: Image.Image, song: Song, difficulty: Difficulties):
    draw = ImageDraw.Draw(image)
    accent = DIFFICULTY_COLORS[difficulty.level_index]
    draw.rectangle((0, 0, 1119, 5), fill=accent)
    with Image.open(await music_picture(song.song_id)) as cover:
        image.paste(cover.convert('RGB').resize((96, 96), Image.Resampling.LANCZOS), (24, 24))
    _text(draw, (140, 23, 1096, 69), song.song_name, size=30, color='#24242a')
    details = (
        f'ID {song.song_id}  |  {song.type}  |  '
        f'{DIFFICULTY_NAMES[difficulty.level_index]}  '
        f'{difficulty.level} ({difficulty.level_value:.1f})'
    )
    _text(draw, (140, 74, 1096, 113), details, size=20, color=accent)


async def draw_tolerance_table(song: Song, difficulty: Difficulties, mode: str = '101-') -> MessageSegment:
    data = calculate_tolerance(difficulty.notes)
    normal_values, break_values = tolerance_values(data, mode)
    image = Image.new('RGB', (1120, 798), '#ffffff')
    await _draw_chart_header(image, song, difficulty)
    draw = ImageDraw.Draw(image)
    edges = (24, 212, 433, 654, 875, 1096)
    colors = ('#ff9800', '#ec3c78', '#4cae50', '#969696')
    header_y, header_h, row_h = 150, 62, 64
    _text(draw, (38, header_y, 198, header_y + header_h), mode[:-1] + '%' + mode[-1],
          size=28, color='#36363d')
    for column, heading in enumerate(('PERFECT', 'GREAT', 'GOOD', 'MISS')):
        left, right = edges[column + 1:column + 3]
        draw.rectangle((left, header_y, right - 1, header_y + header_h - 1), fill=colors[column])
        _text(draw, (left + 15, header_y, right - 12, header_y + header_h), heading)

    y = header_y + header_h
    for kind, values in normal_values.items():
        draw.rectangle((edges[0], y, edges[-1] - 1, y + row_h - 1), fill='#f7f8fa')
        _text(draw, (38, y, 198, y + 39), kind, size=23, color='#303038')
        _text(draw, (38, y + 36, 198, y + row_h), f'{data.counts[kind]} notes', size=14, color='#73737e')
        for column, change in enumerate(values):
            left, right = edges[column + 1:column + 3]
            draw.rectangle((left, y, right - 1, y + row_h - 1), fill=colors[column])
            value = format_change(change) if data.counts[kind] else '-'
            _text(draw, (left + 15, y, right - 12, y + row_h), value)
        draw.line((edges[0], y + row_h - 1, edges[-1] - 1, y + row_h - 1), fill='#ffffff', width=1)
        y += row_h

    break_h = 70
    draw.rectangle((edges[0], y, edges[1] - 1, y + break_h * 3 - 1), fill='#f7f8fa')
    _text(draw, (38, y + 63, 198, y + 107), 'Break', size=23, color='#303038')
    _text(draw, (38, y + 107, 198, y + 140), f'{data.break_count} notes', size=14, color='#73737e')
    for column, indices in enumerate(((0, 1, 2), (3, 4, 5), (6,), (7,))):
        left, right = edges[column + 1:column + 3]
        height = break_h if len(indices) == 3 else break_h * 3
        for row, index in enumerate(indices):
            top = y + row * height
            fill = '#ffbe00' if index == 0 else colors[column]
            draw.rectangle((left, top, right - 1, top + height - 1), fill=fill)
            value = format_change(break_values[index]) if data.break_count else '-'
            mid = top + height // 2
            _text(draw, (left + 15, mid - 29, right - 12, mid + 6), value)
            caption = ('CP / 2600', 'P / 2550', 'P / 2500', 'G / 2000',
                       'G / 1500', 'G / 1250', 'GOOD / 1000', 'MISS')[index]
            _text(draw, (left + 15, mid + 6, right - 12, mid + 29), caption, size=14)
            if row:
                draw.line((left, top, right - 1, top), fill='#ffffff', width=1)

    _text(draw, (24, 695, 1096, 733),
          f'基础分总分 {data.base_score:,}   |   绝赞数量 {data.break_count}   |   '
          + ('含绝赞奖励损失' if mode == '101-' else '含绝赞奖励得分'),
          size=18, color='#62626f')
    _text(draw, (24, 748, 1096, 785), f'Generated by {get_botname()} BOT',
          size=17, color='#81818d', center=True)
    return MessageSegment.image(image_to_base64(image))


@dataclass(frozen=True)
class ScoreLineData:
    target: Fraction
    maximum: Fraction
    budget: Fraction
    counts: dict[str, int]
    normal_limits: dict[str, tuple[int, ...]]
    break_limits: tuple[int, ...]


def calculate_score_line(notes: Notes, target: Fraction) -> ScoreLineData:
    data = calculate_tolerance(notes)
    maximum = Fraction(101 if data.break_count else 100)
    if not 0 <= target <= maximum:
        raise ValueError(f'该谱面的分数线应在 0% 至 {maximum}% 之间')
    budget = maximum - target
    normal = {
        kind: tuple(min(data.counts[kind], budget // loss) for loss in losses[1:])
        for kind, losses in data.normal_losses.items()
    }
    breaks = tuple(min(data.break_count, budget // loss) for loss in data.break_losses[3:])
    return ScoreLineData(target, maximum, budget, data.counts, normal, breaks)


async def draw_score_line_table(song: Song, difficulty: Difficulties, target: Fraction) -> MessageSegment:
    data = calculate_score_line(difficulty.notes, target)
    image = Image.new('RGB', (1120, 714), '#ffffff')
    await _draw_chart_header(image, song, difficulty)
    draw = ImageDraw.Draw(image)
    _text(draw, (24, 138, 570, 190), f'分数线 {float(target)}%', size=28, color='#303038')
    _text(draw, (570, 138, 1096, 190),
          f'可损失 {float(data.budget):.6f}% / 满分 {data.maximum}%', size=20, color='#62626f')

    edges = (24, 192, 373, 554, 735, 916, 1096)
    header_y, header_h = 212, 62
    draw.rectangle((24, header_y, 1095, header_y + header_h - 1), fill='#444451')
    _text(draw, (32, header_y, 184, header_y + header_h), '判定 / 个数', size=20, center=True)
    for column, kind in enumerate(NOTE_WEIGHTS):
        left, right = edges[column + 1:column + 3]
        _text(draw, (left + 8, header_y + 2, right - 8, header_y + 36), kind.upper(), size=23, center=True)
        _text(draw, (left + 8, header_y + 34, right - 8, header_y + 59),
              f'{data.counts[kind]} notes', size=14, color='#dddde5', center=True)

    y = header_y + header_h
    for row, (label, color, background, height) in enumerate((
        ('GREAT', '#ec3c78', '#fff0f5', 138),
        ('GOOD', '#4cae50', '#eff8ef', 84),
        ('MISS', '#969696', '#f3f3f5', 84),
    )):
        draw.rectangle((24, y, 1095, y + height - 1), fill=background)
        draw.rectangle((24, y, 191, y + height - 1), fill=color)
        _text(draw, (32, y, 184, y + height), label, size=26, center=True)
        for column, kind in enumerate(NOTE_WEIGHTS):
            left, right = edges[column + 1:column + 3]
            if kind == 'Break' and row == 0 and data.counts[kind]:
                for tier, base_score in enumerate((2000, 1500, 1250)):
                    _text(draw, (left + 8, y + tier * 46, right - 8, y + (tier + 1) * 46),
                          f'{base_score}: {data.break_limits[tier]}', size=23, color='#303038', center=True)
            else:
                limit = 0
                if data.counts[kind]:
                    limit = data.break_limits[row + 2] if kind == 'Break' else data.normal_limits[kind][row]
                _text(draw, (left + 8, y, right - 8, y + height),
                      str(limit) if data.counts[kind] else '-', size=32, color='#303038', center=True)
            draw.line((right - 1, y, right - 1, y + height - 1), fill='#ffffff', width=1)
        draw.line((24, y + height - 1, 1095, y + height - 1), fill='#ffffff', width=1)
        y += height

    _text(draw, (24, 596, 1096, 628), '各格为独立容错上限，其余音符均按 Critical Perfect 计算，不可相加。',
          size=18, color='#62626f')
    _text(draw, (24, 628, 1096, 660), 'BREAK GREAT 分别按 2000 / 1500 / 1250 档计算，已计入奖励分损失。',
          size=18, color='#62626f')
    _text(draw, (24, 672, 1096, 706), f'Generated by {get_botname()} BOT',
          size=17, color='#81818d', center=True)
    return MessageSegment.image(image_to_base64(image))
