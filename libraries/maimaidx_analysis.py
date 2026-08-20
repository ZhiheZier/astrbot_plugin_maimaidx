"""玩家 B50 含金量 / 水分分析与横向分析图生成。"""

from __future__ import annotations

import statistics
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import (
    MessageSegment,
    Root,
    SIYUAN,
    TBFONT,
    get_botname,
    log,
    maimaidir,
)
from .image import image_to_base64, music_picture
from .maimai_best_50 import (
    rating_asset_name,
    themed_path,
)
from .maimaidx_error import (
    TokenDisableError,
    TokenError,
    TokenNotFoundError,
    UserDisabledQueryError,
    UserNotExistsError,
    UserNotFoundError,
)
from .maimaidx_music import mai
from .maimaidx_play_result import Best50, PlayedResult, Player
from .maimaidx_user import Theme


ANALYSIS_ASSET_DIR = Root / 'assets' / 'analysis'
ANALYSIS_CARD_ASSET_NAMES = (
    'UI_TST_MBase_BSC.png',
    'UI_TST_MBase_ADV.png',
    'UI_TST_MBase_EXP.png',
    'UI_TST_MBase_MST.png',
    'UI_TST_MBase_MST_Re.png',
)
ANALYSIS_TYPE_BADGE_ASSET_NAMES = {
    'SD': 'UI_UPE_Infoicon_StandardMode.png',
    'DX': 'UI_UPE_Infoicon_DeluxeMode.png',
}


def analysis_card_asset(level_index: int) -> Path:
    """返回对应谱面难度的内置原生卡片底板。"""
    index = min(max(int(level_index), 0), len(ANALYSIS_CARD_ASSET_NAMES) - 1)
    return ANALYSIS_ASSET_DIR / ANALYSIS_CARD_ASSET_NAMES[index]


def analysis_type_badge_asset(song_type: str) -> Path:
    """返回标准谱或 DX 谱使用的原生类型标签。"""
    normalized_type = str(song_type).upper()
    asset_name = ANALYSIS_TYPE_BADGE_ASSET_NAMES.get(
        normalized_type,
        ANALYSIS_TYPE_BADGE_ASSET_NAMES['SD'],
    )
    return ANALYSIS_ASSET_DIR / asset_name


@dataclass(frozen=True)
class GoldChart:
    """一张 B50 谱面的含金量信息。"""

    record: PlayedResult
    fitted_ds: float
    delta: float

    @property
    def official_ds(self) -> float:
        return self.record.level_value


@dataclass(frozen=True)
class GoldAnalysis:
    """当前 B50 的含金量统计及最高的十张谱面。"""

    top_charts: List[GoldChart]
    total_count: int
    valid_count: int
    mean: float
    maximum: float
    minimum: float
    median: float
    std_dev: float


@dataclass(frozen=True)
class WaterChart:
    """一张 B50 谱面的含水量信息。"""

    record: PlayedResult
    fitted_ds: float
    delta: float

    @property
    def official_ds(self) -> float:
        return self.record.level_value


@dataclass(frozen=True)
class WaterAnalysis:
    """当前 B50 的含水量统计及最高的十张谱面。"""

    top_charts: List[WaterChart]
    total_count: int
    valid_count: int
    mean: float
    maximum: float
    minimum: float
    median: float
    std_dev: float


def fitted_level_value(record: PlayedResult) -> Optional[float]:
    """读取谱面的原始高精度拟合定数；不存在时返回 None。"""
    music_list = getattr(mai, 'total_list', None)
    if not music_list:
        return None
    music = music_list.by_id(str(record.song_id))
    if not music or not music.stats:
        return None
    if not 0 <= record.level_index < len(music.stats):
        return None
    stats = music.stats[record.level_index]
    if not stats or stats.fit_diff is None:
        return None
    return float(stats.fit_diff)


def analyze_b50_gold(
    best50: Best50,
    fitted_level_of: Callable[[PlayedResult], Optional[float]] = fitted_level_value,
) -> GoldAnalysis:
    """按 ``拟合定数 - 官方定数`` 分析当前普通 B50。"""
    records = list(best50.sd) + list(best50.dx)
    charts: List[GoldChart] = []
    for record in records:
        if record.level_value <= 0:
            continue
        fitted_ds = fitted_level_of(record)
        if fitted_ds is None or fitted_ds <= 0:
            continue
        charts.append(
            GoldChart(
                record=record,
                fitted_ds=fitted_ds,
                delta=fitted_ds - record.level_value,
            )
        )

    charts.sort(
        key=lambda chart: (
            chart.delta,
            chart.record.rating,
            chart.record.achievements,
            chart.record.song_id,
            chart.record.level_index,
        ),
        reverse=True,
    )
    values = [chart.delta for chart in charts]
    if not values:
        return GoldAnalysis([], len(records), 0, 0, 0, 0, 0, 0)
    return GoldAnalysis(
        top_charts=charts[:10],
        total_count=len(records),
        valid_count=len(charts),
        mean=statistics.mean(values),
        maximum=max(values),
        minimum=min(values),
        median=statistics.median(values),
        std_dev=statistics.pstdev(values),
    )


def analyze_b50_water(
    best50: Best50,
    fitted_level_of: Callable[[PlayedResult], Optional[float]] = fitted_level_value,
) -> WaterAnalysis:
    """按 ``官方定数 - 拟合定数`` 分析当前普通 B50。"""
    records = list(best50.sd) + list(best50.dx)
    charts: List[WaterChart] = []
    for record in records:
        if record.level_value <= 0:
            continue
        fitted_ds = fitted_level_of(record)
        if fitted_ds is None or fitted_ds <= 0:
            continue
        charts.append(
            WaterChart(
                record=record,
                fitted_ds=fitted_ds,
                delta=record.level_value - fitted_ds,
            )
        )

    charts.sort(
        key=lambda chart: (
            chart.delta,
            chart.record.rating,
            chart.record.achievements,
            chart.record.song_id,
            chart.record.level_index,
        ),
        reverse=True,
    )
    values = [chart.delta for chart in charts]
    if not values:
        return WaterAnalysis([], len(records), 0, 0, 0, 0, 0, 0)
    return WaterAnalysis(
        top_charts=charts[:10],
        total_count=len(records),
        valid_count=len(charts),
        mean=statistics.mean(values),
        maximum=max(values),
        minimum=min(values),
        median=statistics.median(values),
        std_dev=statistics.pstdev(values),
    )


class DrawGoldAnalysis:
    """绘制 1280×544 的含金量分析图。"""

    WIDTH = 1280
    HEIGHT = 544
    LEFT_WIDTH = 292
    LEFT_CENTER_X = 147
    CARD_WIDTH = 164
    CARD_HEIGHT = 228
    CARD_X_GAP = 17
    CARD_Y = (28, 282)

    TITLE = '含金量分析'
    DESCRIPTION_LINE_1 = '右侧是当前 B50 中'
    DESCRIPTION_LINE_2 = '含金量最高的 10 张谱面'
    FORMULA_LINE_1 = '含金量 = 谱面拟合定数'
    FORMULA_LINE_2 = '- 官方定数'

    def __init__(
        self,
        player: Player,
        analysis: Union[GoldAnalysis, WaterAnalysis],
    ) -> None:
        self.player = player
        self.analysis = analysis
        self.image = self._make_background()
        self.draw = ImageDraw.Draw(self.image)
        self.font_cn = str(SIYUAN)
        self.font_en = str(TBFONT)

    @staticmethod
    def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(path, size)

    def _make_background(self) -> Image.Image:
        """以画面中心为焦点，等比裁切现有 Circle B50 背景。"""
        paths = (
            maimaidir / 'circle' / 'b50.png',
            maimaidir / 'prism_plus' / 'b50.png',
        )
        background_path = next((path for path in paths if path.exists()), None)
        if background_path is None:
            return Image.new('RGBA', (self.WIDTH, self.HEIGHT), (250, 67, 181, 255))

        source = Image.open(background_path).convert('RGBA')
        return ImageOps.fit(
            source,
            (self.WIDTH, self.HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    def _text(
        self,
        xy: Tuple[int, int],
        text: Union[str, int],
        *,
        size: int,
        fill=(255, 255, 255, 255),
        font: Optional[str] = None,
        anchor: str = 'la',
        stroke_width: int = 0,
        stroke_fill=(128, 20, 145, 255),
    ) -> None:
        self.draw.text(
            xy,
            str(text),
            font=self._font(font or self.font_cn, size),
            fill=fill,
            anchor=anchor,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )

    def _truncate(self, text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
        if self.draw.textbbox((0, 0), text, font=font)[2] <= width:
            return text
        suffix = '...'
        while text and self.draw.textbbox((0, 0), text + suffix, font=font)[2] > width:
            text = text[:-1]
        return text + suffix

    def _draw_summary(self) -> None:
        name_font = self._font(self.font_cn, 32)
        player_name = self._truncate(self.player.name or '舞萌玩家', name_font, 200)
        self._text(
            (self.LEFT_CENTER_X, 72), player_name,
            size=32, anchor='mm', stroke_width=2,
            stroke_fill=(173, 20, 166, 255)
        )
        self._draw_player_badges()

        self._text(
            (self.LEFT_CENTER_X, 163), self.TITLE,
            size=47, anchor='ma', stroke_width=3,
            stroke_fill=(198, 35, 185, 255)
        )
        self._text(
            (self.LEFT_CENTER_X, 239), self.DESCRIPTION_LINE_1,
            size=18, anchor='ma', stroke_width=2
        )
        self._text(
            (self.LEFT_CENTER_X, 267), self.DESCRIPTION_LINE_2,
            size=18, anchor='ma', stroke_width=2
        )
        self._text(
            (self.LEFT_CENTER_X, 305), self.FORMULA_LINE_1,
            size=18, anchor='ma', stroke_width=2
        )
        self._text(
            (self.LEFT_CENTER_X, 333), self.FORMULA_LINE_2,
            size=18, anchor='ma', stroke_width=2
        )
        self._text(
            (self.LEFT_CENTER_X, 434), 'Designed by 宇航员Dale',
            size=18, anchor='ma', stroke_width=2
        )
        self._text(
            (self.LEFT_CENTER_X, 462), '& Kyy008',
            size=18, anchor='ma', stroke_width=2
        )
        self._text(
            (self.LEFT_CENTER_X, 495), f'Generated by {get_botname()}',
            size=18, anchor='ma', stroke_width=2
        )

    def _draw_player_badges(self) -> None:
        """复用普通 B50 页头的 Rating 牌和数字素材。"""
        rating_width, rating_height = 240, 45
        rating_x = self.LEFT_CENTER_X - rating_width // 2
        rating_y = 120
        rating_badge = Image.open(
            themed_path(
                Theme.CIRCLE,
                rating_asset_name(self.player.rating, Theme.CIRCLE),
            )
        ).convert('RGBA').resize(
            (rating_width, rating_height),
            Image.Resampling.LANCZOS,
        )
        self.image.alpha_composite(rating_badge, (rating_x, rating_y))

        rating = f'{max(self.player.rating, 0):05d}'[-5:]
        rating_digit_x = 138
        for index, digit in enumerate(rating):
            digit_image = Image.open(
                maimaidir / f'UI_NUM_Drating_{digit}.png'
            ).convert('RGBA').resize((23, 26), Image.Resampling.LANCZOS)
            self.image.alpha_composite(
                digit_image,
                (rating_digit_x + 19 * index, rating_y + 9),
            )
        self.draw = ImageDraw.Draw(self.image)

    def _draw_card(
        self,
        chart: Union[GoldChart, WaterChart],
        x: int,
        y: int,
    ) -> None:
        record = chart.record
        level_index = min(
            max(record.level_index, 0),
            len(ANALYSIS_CARD_ASSET_NAMES) - 1,
        )

        layer = Image.new('RGBA', self.image.size, (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.rounded_rectangle(
            (x + 4, y + 5, x + self.CARD_WIDTH + 4, y + self.CARD_HEIGHT + 5),
            radius=10,
            fill=(67, 15, 82, 80),
        )
        self.image.alpha_composite(layer)

        card_base = Image.open(analysis_card_asset(level_index)).convert('RGBA')
        card_base = card_base.resize(
            (self.CARD_WIDTH, self.CARD_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        self.image.alpha_composite(card_base, (x, y))

        cover = Image.open(music_picture(record.song_id)).convert('RGBA')
        cover = ImageOps.fit(cover, (133, 133), Image.Resampling.LANCZOS)
        self.image.alpha_composite(cover, (x + 16, y + 12))

        self.draw = ImageDraw.Draw(self.image)

        type_path = analysis_type_badge_asset(record.type)
        if type_path.exists():
            badge = Image.open(type_path).convert('RGBA').resize(
                (73, 20), Image.Resampling.LANCZOS
            )
            self.image.alpha_composite(badge, (x + 2, y + 1))
        else:
            self.draw.rounded_rectangle(
                (x + 2, y + 1, x + 74, y + 21), radius=8,
                fill=(39, 181, 231, 255)
            )
            self._text(
                (x + 38, y + 11), record.type, size=10,
                font=self.font_en, anchor='mm'
            )

        self._text(
            (x + 135, y + 147), f'{chart.official_ds:.1f}', size=13,
            font=self.font_en, anchor='ma', stroke_width=1
        )
        self._text(
            (x + 135, y + 161), f'({chart.fitted_ds:.3f})', size=11,
            font=self.font_en, anchor='ma', stroke_width=1
        )

        title_font = self._font(self.font_cn, 14)
        title = self._truncate(record.song_name, title_font, self.CARD_WIDTH - 12)
        self._text(
            (x + self.CARD_WIDTH // 2, y + 187), title,
            size=14, anchor='mm', stroke_width=1,
            stroke_fill=(10, 31, 66, 255),
        )

        self._text(
            (x + self.CARD_WIDTH // 2, y + 207),
            f'{record.achievements:.4f}%', size=13,
            font=self.font_en, anchor='mm', stroke_width=2,
            stroke_fill=(128, 20, 145, 255),
        )

    def render(self) -> Image.Image:
        self._draw_summary()
        for index, chart in enumerate(self.analysis.top_charts):
            row, column = divmod(index, 5)
            x = 305 + column * (self.CARD_WIDTH + self.CARD_X_GAP)
            self._draw_card(chart, x, self.CARD_Y[row])
        return self.image


class DrawWaterAnalysis(DrawGoldAnalysis):
    """绘制 1280×544 的水分分析图。"""

    TITLE = '水分分析'
    DESCRIPTION_LINE_2 = '含水量最高的 10 张谱面'
    FORMULA_LINE_1 = '含水量 = 官方定数'
    FORMULA_LINE_2 = '- 谱面拟合定数'


async def generate_gold_analysis(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
) -> Union[MessageSegment, str]:
    """查询玩家当前普通 B50 并生成含金量分析图。"""
    from .maimaidx_lxns import LxnsError
    from .maimaidx_source import get_best50

    try:
        player, best50 = await get_best50(qqid=qqid, username=username)
        analysis = analyze_b50_gold(best50)
        if not analysis.valid_count:
            return '当前 B50 暂无可用的谱面拟合定数'
        image = DrawGoldAnalysis(player, analysis).render()
        return MessageSegment.image(image_to_base64(image))
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
        LxnsError,
    ) as error:
        return str(error)
    except Exception as error:
        log.error(traceback.format_exc())
        return f'未知错误：{type(error)}\n请联系Bot管理员'


async def generate_water_analysis(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
) -> Union[MessageSegment, str]:
    """查询玩家当前普通 B50 并生成水分分析图。"""
    from .maimaidx_lxns import LxnsError
    from .maimaidx_source import get_best50

    try:
        player, best50 = await get_best50(qqid=qqid, username=username)
        analysis = analyze_b50_water(best50)
        if not analysis.valid_count:
            return '当前 B50 暂无可用的谱面拟合定数'
        image = DrawWaterAnalysis(player, analysis).render()
        return MessageSegment.image(image_to_base64(image))
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
        LxnsError,
    ) as error:
        return str(error)
    except Exception as error:
        log.error(traceback.format_exc())
        return f'未知错误：{type(error)}\n请联系Bot管理员'


__all__ = [
    'GoldChart',
    'GoldAnalysis',
    'WaterChart',
    'WaterAnalysis',
    'analyze_b50_gold',
    'analyze_b50_water',
    'analysis_card_asset',
    'analysis_type_badge_asset',
    'fitted_level_value',
    'DrawGoldAnalysis',
    'DrawWaterAnalysis',
    'generate_gold_analysis',
    'generate_water_analysis',
]
