"""统一成绩模型 PlayedResult，以及与水鱼 / 落雪模型之间的转换。

阶段 B：查分适配以 PlayedResult 为中间层；
绘图仍使用 UserInfo / ChartInfo / PlayInfo*（通过本模块桥接）。
"""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, field_validator

from .. import diffs
from .maimaidx_model import (
    ChartInfo,
    Data,
    PlayInfoDefault,
    PlayInfoDev,
    UserInfo,
)
from .maimaidx_music import mai


# ---------------------------------------------------------------------------
# 统一成绩 / 玩家模型
# ---------------------------------------------------------------------------
class NotPlayedResult(BaseModel):
    song_id: int
    level_index: int
    level_value: float = 0.0


class PlayedResult(BaseModel):
    song_id: int
    song_name: str = ''
    level: str = ''
    level_index: int = 0
    level_value: float = 0.0
    type: str = 'SD'

    rating: int = 0
    achievements: float = 0.0
    rate: Optional[str] = None
    fc: Optional[str] = None
    fs: Optional[str] = None
    dx_score: int = 0

    dx_star: Optional[int] = None
    level_label: Optional[str] = None
    upload_time: Optional[str] = None

    @field_validator('rate', 'fc', 'fs', mode='before')
    @classmethod
    def _empty_to_none(cls, v):
        if v == '' or v is None:
            return None
        return v

    @field_validator('type', mode='before')
    @classmethod
    def _norm_type(cls, v: str) -> str:
        if not v:
            return 'SD'
        low = str(v).lower()
        if low in ('standard', 'sd'):
            return 'SD'
        if low in ('dx', 'utage'):
            return 'DX'
        return str(v).upper()

    # ---- ChartInfo / 绘图兼容别名 ----
    @property
    def ra(self) -> int:
        return self.rating

    @property
    def dxScore(self) -> int:
        return self.dx_score

    @property
    def title(self) -> str:
        return self.song_name

    @property
    def ds(self) -> float:
        return self.level_value


class Best50(BaseModel):
    sd_total: int = 0
    dx_total: int = 0
    sd: List[PlayedResult] = []
    dx: List[PlayedResult] = []


class Player(BaseModel):
    name: str = ''
    rating: int = 0
    course_rank: int = 0
    name_plate: Optional[str] = None
    friend_code: int = 0
    class_rank: int = 0
    star: int = 0


def dx_star_from_percentage(percentage: float) -> int:
    """按游戏 DX SCORE 百分比计算 0～5 星。"""
    if percentage < 85:
        return 0
    if percentage < 90:
        return 1
    if percentage < 93:
        return 2
    if percentage < 95:
        return 3
    if percentage < 97:
        return 4
    return 5


def dx_star_from_scores(dx_score: int, max_dx_score: int) -> Optional[int]:
    """按实际 DX SCORE 与理论满分计算星级；满分无效时返回 None。"""
    if max_dx_score <= 0:
        return None
    return dx_star_from_percentage(dx_score / max_dx_score * 100)


# ---------------------------------------------------------------------------
# 曲库定数字典查找
# ---------------------------------------------------------------------------
def lookup_level_value(song_id: int, level_index: int) -> float:
    key = f'{song_id}-{level_index}'
    if getattr(mai, 'total_level_value_map', None):
        if key in mai.total_level_value_map:
            return mai.total_level_value_map[key]
    music = mai.total_list.by_id(str(song_id)) if getattr(mai, 'total_list', None) else None
    if music and music.ds and len(music.ds) > level_index:
        return music.ds[level_index]
    return 0.0


def lookup_meta(song_id: int, level_index: int) -> tuple[float, str, str]:
    """返回 (level_value, level, title)。"""
    music = mai.total_list.by_id(str(song_id)) if getattr(mai, 'total_list', None) else None
    ds = lookup_level_value(song_id, level_index)
    if music and music.ds and len(music.ds) > level_index:
        level = music.level[level_index] if level_index < len(music.level) else ''
        return ds or music.ds[level_index], level, music.title
    return ds, '', ''


def df_song_id_from_lxns(lxns_id: int, song_type: str) -> int:
    t = (song_type or '').lower()
    if t == 'dx':
        return lxns_id + 10000
    return lxns_id


# ---------------------------------------------------------------------------
# → PlayedResult
# ---------------------------------------------------------------------------
def chartinfo_to_played(v: ChartInfo) -> PlayedResult:
    return PlayedResult(
        song_id=v.song_id,
        song_name=v.title,
        level=v.level,
        level_index=v.level_index,
        level_value=v.ds,
        type=v.type,
        rating=v.ra,
        achievements=v.achievements,
        fc=v.fc or None,
        fs=v.fs or None,
        rate=v.rate or None,
        dx_score=v.dxScore,
        level_label=v.level_label,
    )


def playinfo_to_played(
    v: Union[PlayInfoDefault, PlayInfoDev], *, level_value: float = 0
) -> PlayedResult:
    song_id = getattr(v, 'song_id', None)
    if song_id is None:
        song_id = getattr(v, 'id', 0)
    ds = v.ds if not level_value else level_value
    return PlayedResult(
        song_id=int(song_id),
        song_name=v.title,
        level=v.level,
        level_index=v.level_index,
        level_value=ds,
        type=v.type,
        rating=v.ra,
        achievements=v.achievements,
        fc=v.fc or None,
        fs=v.fs or None,
        rate=v.rate or None,
        dx_score=v.dxScore,
        level_label=getattr(v, 'level_label', None),
    )


def lxns_score_to_played(score) -> PlayedResult:
    """LxnsScore → PlayedResult（依赖曲库补定数 / 曲名）。"""
    from .maimai_best_50 import computeRa

    song_id = df_song_id_from_lxns(score.id, score.type)
    ds, level, title = lookup_meta(song_id, score.level_index)
    if not title:
        title = score.song_name or ''
    if not level:
        level = score.level or ''

    ach = score.achievements
    if ach is None:
        rate_map = {
            'sssp': 100.5,
            'sss': 100.0,
            'ssp': 99.5,
            'ss': 99.0,
            'sp': 98.0,
            's': 97.0,
            'aaa': 94.0,
            'aa': 90.0,
            'a': 80.0,
            'bbb': 75.0,
            'bb': 70.0,
            'b': 60.0,
            'c': 50.0,
            'd': 0.0,
        }
        ach = rate_map.get((score.rate or '').lower(), 0.0)

    ra, rate = computeRa(ds, ach, israte=True) if ds else (0, (score.rate or 'd').upper())
    if score.dx_rating is not None:
        ra = int(score.dx_rating)
    if score.rate:
        rate = score.rate

    rate_str = rate.lower() if isinstance(rate, str) else rate
    return PlayedResult(
        song_id=song_id,
        song_name=title,
        level=level,
        level_index=score.level_index,
        level_value=ds,
        type=score.type,
        rating=ra,
        achievements=float(ach or 0),
        fc=score.fc,
        fs=score.fs,
        rate=rate_str,
        dx_score=score.dx_score or 0,
        dx_star=score.dx_star,
        level_label=diffs[score.level_index] if score.level_index < len(diffs) else '',
    )


def userinfo_to_best50(user: UserInfo) -> Best50:
    sd = [chartinfo_to_played(c) for c in (user.charts.sd or [])] if user.charts else []
    dx = [chartinfo_to_played(c) for c in (user.charts.dx or [])] if user.charts else []
    return Best50(
        sd_total=sum(p.rating for p in sd),
        dx_total=sum(p.rating for p in dx),
        sd=sd,
        dx=dx,
    )


def userinfo_to_player(user: UserInfo) -> Player:
    return Player(
        name=user.nickname or '',
        rating=user.rating or 0,
        course_rank=user.additional_rating or 0,
        name_plate=user.plate,
    )


# ---------------------------------------------------------------------------
# PlayedResult → 水鱼模型（渲染兼容）
# ---------------------------------------------------------------------------
def played_to_chartinfo(p: PlayedResult) -> ChartInfo:
    return ChartInfo(
        achievements=p.achievements,
        fc=p.fc or '',
        fs=p.fs or '',
        level=p.level,
        level_index=p.level_index,
        title=p.song_name,
        type=p.type,
        ds=p.level_value,
        dxScore=p.dx_score,
        ra=p.rating,
        rate=(p.rate or '').lower(),
        level_label=p.level_label or (
            diffs[p.level_index] if p.level_index < len(diffs) else ''
        ),
        song_id=p.song_id,
    )


def played_to_playinfodefault(p: PlayedResult) -> PlayInfoDefault:
    return PlayInfoDefault(
        id=p.song_id,
        achievements=p.achievements,
        fc=p.fc or '',
        fs=p.fs or '',
        level=p.level,
        level_index=p.level_index,
        title=p.song_name,
        type=p.type,
        ds=p.level_value,
        dxScore=p.dx_score,
        ra=p.rating,
        rate=(p.rate or '').lower(),
    )


def played_to_playinfodev(p: PlayedResult) -> PlayInfoDev:
    return PlayInfoDev.model_validate(played_to_chartinfo(p).model_dump())


def best50_to_userinfo(player: Player, best50: Best50) -> UserInfo:
    return UserInfo(
        additional_rating=player.course_rank,
        nickname=player.name,
        plate=player.name_plate,
        rating=player.rating,
        username=None,
        charts=Data(
            sd=[played_to_chartinfo(p) for p in best50.sd],
            dx=[played_to_chartinfo(p) for p in best50.dx],
        ),
    )


def played_list_to_records(data: List[PlayedResult]) -> List[PlayInfoDev]:
    return [played_to_playinfodev(p) for p in data]


def played_list_to_plate(data: List[PlayedResult]) -> List[PlayInfoDefault]:
    return [played_to_playinfodefault(p) for p in data]


def fill_song_results(
    played: List[PlayedResult],
    *,
    song_id: int,
    difficulty_count: int,
    level_values: Optional[List[float]] = None,
) -> List[Union[PlayedResult, NotPlayedResult]]:
    """按难度槽位填充未游玩结果（单曲 info 用）。"""
    slots: List[Union[PlayedResult, NotPlayedResult]] = []
    for i in range(difficulty_count):
        if level_values and i < len(level_values):
            lv = level_values[i]
        else:
            lv = lookup_level_value(song_id, i)
        slots.append(NotPlayedResult(song_id=song_id, level_index=i, level_value=lv))
    for p in played:
        if 0 <= p.level_index < len(slots):
            slots[p.level_index] = p
    return slots
