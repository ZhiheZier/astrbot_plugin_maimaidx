from dataclasses import dataclass
from math import ceil
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from .. import (
    FOTNEWRODIN,
    SIYUAN,
    TBFONT,
    MessageSegment,
    diffs,
    get_botname,
    maimaidir,
    normalize_plate_filename,
    plate_version_dir,
)
from .image import (
    DrawText,
    draw_text_with_font_fallback,
    generate_frosted_card,
    image_to_base64,
    music_picture,
    tricolor_gradient_prism_plus,
)
from .maimaidx_model import PlayInfoDefault, Song


DIFFICULTY_COLORS = (
    (129, 217, 85, 255),
    (245, 189, 21, 255),
    (255, 129, 141, 255),
    (159, 81, 220, 255),
    (138, 0, 226, 255),
)
DEFAULT_COLOR = (124, 129, 255, 255)


@dataclass
class PlateChartProgress:
    song_id: int
    level_index: int
    level_value: float
    result: Optional[PlayInfoDefault]
    qualified: bool


@dataclass
class PlateProgressData:
    total_count: int
    remaster_count: int
    chart_results: dict[int, list[PlateChartProgress]]
    slot_counts: list[int]
    completed_count: int


def is_plate_qualified(play: Optional[PlayInfoDefault], plan: str) -> bool:
    if play is None:
        return False
    if plan in ('极', '極'):
        return bool(play.fc)
    if plan == '将':
        return play.achievements >= 100
    if plan == '者':
        return play.achievements >= 80
    if plan == '神':
        return play.fc in ('ap', 'app')
    if plan == '舞舞':
        return play.fs in ('fsd', 'fdx', 'fsdp', 'fdxp')
    return False


def build_plate_progress(
    songs: Sequence[Song],
    play_results: Sequence[PlayInfoDefault],
    plan: str,
    *,
    remaster_ids: Sequence[int] = (),
) -> PlateProgressData:
    """将牌子曲目和玩家成绩整理为可复用的绘图数据。"""
    remaster_set = {int(song_id) for song_id in remaster_ids}
    result_by_song: dict[int, list[Optional[PlayInfoDefault]]] = {}
    song_by_id: dict[int, Song] = {}
    for song in songs:
        requested_slots = 5 if song.song_id in remaster_set else 4
        slot_count = min(requested_slots, len(song.difficulties))
        if slot_count == 0:
            continue
        song_by_id[song.song_id] = song
        result_by_song[song.song_id] = [None] * slot_count

    for play in play_results:
        slots = result_by_song.get(play.song_id)
        if slots is not None and 0 <= play.level_index < len(slots):
            slots[play.level_index] = play

    max_slots = max((len(slots) for slots in result_by_song.values()), default=4)
    chart_results = {index: [] for index in range(max_slots)}
    slot_counts = [0] * max_slots
    completed_count = 0
    for song_id, slots in result_by_song.items():
        song = song_by_id[song_id]
        qualified_slots = [
            is_plate_qualified(play, plan)
            for play in slots
        ]
        if qualified_slots and all(qualified_slots):
            completed_count += 1
        for index, play in enumerate(slots):
            qualified = qualified_slots[index]
            if qualified:
                slot_counts[index] += 1
            chart_results[index].append(
                PlateChartProgress(
                    song_id=song_id,
                    level_index=index,
                    level_value=song.difficulties[index].level_value,
                    result=play,
                    qualified=qualified,
                )
            )

    for charts in chart_results.values():
        charts.sort(key=lambda chart: chart.level_value, reverse=True)

    return PlateProgressData(
        total_count=len(result_by_song),
        remaster_count=sum(song_id in remaster_set for song_id in result_by_song),
        chart_results=chart_results,
        slot_counts=slot_counts,
        completed_count=completed_count,
    )


def _open_rgba(path, size=None) -> Image.Image | None:
    if not path.exists():
        return None
    image = Image.open(path).convert('RGBA')
    return image.resize(size, Image.Resampling.LANCZOS) if size else image


def _display_rows(count: int) -> int:
    return max(1, min(ceil(count / 13), 4))


def draw_plate_progress(
    data: PlateProgressData,
    version: str,
    plan: str,
) -> MessageSegment:
    """绘制牌子整体进度、分难度进度和未完成谱面。"""
    remaining = {
        index: [chart for chart in charts if not chart.qualified]
        for index, charts in reversed(data.chart_results.items())
    }
    total_counts = {
        index: len(data.chart_results[index]) for index in remaining
    }
    content_bottom = 395
    for charts in remaining.values():
        content_bottom += _display_rows(len(charts)) * 96 + 100
    height = content_bottom + 180

    base = tricolor_gradient_prism_plus(1400, height).convert('RGBA')
    assets = (
        (_open_rgba(maimaidir / 'aurora.png'), (0, 0)),
        (_open_rgba(maimaidir / 'bg_shines.png'), (11, 6)),
        (_open_rgba(maimaidir / 'rainbow.png'), (318, height - 545)),
        (_open_rgba(maimaidir / 'rainbow_bottom.png'), (122, height - 305)),
    )
    for asset, pos in assets:
        if asset:
            base.alpha_composite(asset, pos)
    pattern = _open_rgba(maimaidir / 'pattern.png')
    if pattern:
        for y in range(0, height, 365):
            base.alpha_composite(pattern, (0, y))
    separator = _open_rgba(maimaidir / 'separator.png')
    if separator:
        base.alpha_composite(separator, (100, 305))

    image = generate_frosted_card(base, (50, 349, 1350, content_bottom))
    header = _open_rgba(maimaidir / 'plate_progress_2.png')
    progress_bg = _open_rgba(maimaidir / 'progress_bg.png')
    progress_bar = _open_rgba(maimaidir / 'progress_big.png')
    id_border = _open_rgba(maimaidir / 'border_table_base.png')
    remaster_border = _open_rgba(maimaidir / 'border_table_remaster.png')
    if remaster_border is None:
        remaster_border = id_border
    if header:
        image.alpha_composite(header, (175, 20))

    normalized_plan = '極' if plan == '极' else plan
    plate_name = normalize_plate_filename(f'{version}{normalized_plan}')
    title_asset = _open_rgba(plate_version_dir / f'{plate_name}.png', (1000, 161))
    if title_asset:
        image.alpha_composite(title_asset, (200, 35))

    draw = ImageDraw.Draw(image)
    font = DrawText(draw, FOTNEWRODIN)
    number = DrawText(draw, TBFONT)
    if not title_asset:
        draw_text_with_font_fallback(
            draw, 700, 105, 55, f'{version}{plan}进度', DEFAULT_COLOR,
            FOTNEWRODIN, SIYUAN, 'mm', 4, (255, 255, 255, 255),
        )

    start_y = 455
    for level_index, charts in remaining.items():
        total = total_counts[level_index]
        completed = data.slot_counts[level_index]
        progress = completed / total if total else 1.0
        color = DIFFICULTY_COLORS[level_index]
        if progress_bg:
            image.alpha_composite(progress_bg, (198, start_y - 85))
        if progress_bar and progress:
            width = int(progress_bar.width * progress)
            image.alpha_composite(progress_bar.crop((0, 0, width, progress_bar.height)), (204, start_y - 79))

        status = 'COMPLETED!!!' if completed == total else f'{completed}/{total}'
        font.draw(220, start_y - 57, 34, diffs[level_index], color, 'lm', 4, (255, 255, 255, 255))
        font.draw(700, start_y - 57, 36, status, color, 'mm', 4, (255, 255, 255, 255))
        font.draw(1190, start_y - 57, 20, f'{progress * 100:.2f}%', color, 'rm', 2, (255, 255, 255, 255))

        visible = charts[:51]
        for index, chart in enumerate(visible):
            row, column = divmod(index, 13)
            x = 84 + column * 96
            y = start_y + row * 96
            cover = _open_rgba(music_picture(chart.song_id), (80, 80))
            if cover:
                image.alpha_composite(cover, (x, y))
            border = remaster_border if chart.level_index == 4 else id_border
            if border:
                image.alpha_composite(border, (x - 5, y - 5))
            number.draw(x + 56, y + 4, 16, chart.song_id, color, 'mm')
        if len(charts) > 51:
            x = 84 + 12 * 96
            y = start_y + 3 * 96
            font.draw(
                x,
                y + 35,
                20,
                f'余「{len(charts) - 51}」\n个未完成',
                DEFAULT_COLOR,
                'lm',
                multiline=True,
            )
        start_y += _display_rows(len(charts)) * 96 + 100

    overall = data.completed_count / data.total_count if data.total_count else 1.0
    overall_text = 'COMPLETED!!!' if data.completed_count == data.total_count else f'{data.completed_count}/{data.total_count}'
    if progress_bar and overall:
        width = int(progress_bar.width * overall)
        image.alpha_composite(progress_bar.crop((0, 0, width, progress_bar.height)), (204, 219))
    font.draw(700, 240, 30, overall_text, DEFAULT_COLOR, 'mm', 3, (255, 255, 255, 255))
    font.draw(1190, 240, 30, f'{overall * 100:.2f}%', DEFAULT_COLOR, 'rm', 3, (255, 255, 255, 255))
    draw_text_with_font_fallback(
        draw,
        700,
        height - 75,
        30,
        f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT',
        (114, 188, 254, 255),
        FOTNEWRODIN,
        SIYUAN,
        'mm',
    )
    return MessageSegment.image(image_to_base64(image))
