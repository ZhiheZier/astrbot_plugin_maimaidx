"""查分数据源路由。

根据用户设置的数据源（水鱼 / 落雪）分发查询请求。
内部统一为 PlayedResult / Best50 / Player，对外仍可转水鱼模型以复用绘图。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple, Union

from .maimaidx_api_data import maiApi
from .maimaidx_error import TokenNotFoundError
from .maimaidx_lxns import (
    LxnsAPI,
    LxnsFeatureUnavailable,
    lxns_best50_to_best50,
    lxns_score_to_playinfodefault,
    lxns_scores_to_played,
    lxns_scores_to_records,
)
from .maimaidx_model import PlayInfoDefault, PlayInfoDev, UserInfo
from .maimaidx_play_result import (
    Best50,
    PlayedResult,
    Player,
    best50_to_userinfo,
    dx_star_from_scores,
    playinfo_to_played,
    played_list_to_plate,
    played_list_to_records,
    userinfo_to_best50,
    userinfo_to_player,
)
from .maimaidx_music import mai
from .maimaidx_user import ServiceName, User, userstore


def get_service(
    qqid: Optional[Union[int, str]], username: Optional[str] = None
) -> ServiceName:
    """解析应使用的数据源。查分器用户名（username）仅水鱼支持。"""
    if username:
        return ServiceName.DIVINGFISH
    if qqid is None:
        return ServiceName.DIVINGFISH
    try:
        return userstore.get(int(qqid)).service
    except (ValueError, TypeError):
        return ServiceName.DIVINGFISH


def is_lxns(qqid: Optional[Union[int, str]], username: Optional[str] = None) -> bool:
    return get_service(qqid, username) == ServiceName.LXNS


async def _friend_code(user: User) -> int:
    """获取用户好友码（优先缓存，否则按 QQ 查询并缓存）"""
    if user.friend_code:
        return user.friend_code
    api = LxnsAPI(qqid=user.qqid)
    player = await api.player_by_qq(user.qqid)
    if player.friend_code:
        await userstore.update(user.qqid, friend_code=player.friend_code)
    return player.friend_code


# ---------------------------------------------------------------------------
# 统一 API（PlayedResult）
# ---------------------------------------------------------------------------
async def get_best50(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
    *,
    all_perfect: bool = False,
    min_dx_star: Optional[int] = None,
    fitted: bool = False,
    all_perfect_plus: bool = False,
) -> Tuple[Player, Best50]:
    """按用户数据源获取普通、AP、AP+、星级或拟合 B50。"""
    if sum((all_perfect, min_dx_star is not None, fitted, all_perfect_plus)) > 1:
        raise ValueError('AP50、AP+50、DX 星级与拟合 B50 不能同时启用')
    if min_dx_star is not None and not 1 <= min_dx_star <= 5:
        raise ValueError('DX 星级必须在 1 到 5 之间')

    if all_perfect_plus:
        if is_lxns(qqid, username):
            player, _ = await _lxns_best50_raw(qqid, all_perfect=False)
            records = await _lxns_records_raw(qqid, exact=False)
        else:
            player, records = await _divingfish_dev_records_raw(
                qqid=qqid, username=username
            )
        best50 = select_ap_plus50_records(records, _is_new_song)
    elif fitted:
        if is_lxns(qqid, username):
            player, _ = await _lxns_best50_raw(qqid, all_perfect=False)
            records = await _lxns_records_raw(qqid, exact=False)
        else:
            player, records = await _divingfish_dev_records_raw(
                qqid=qqid, username=username
            )
        best50 = select_fitted_b50_records(
            records,
            _is_new_song,
            _fitted_level_value,
            _rating_for_level_value,
        )
    elif min_dx_star is not None:
        if is_lxns(qqid, username):
            player, _ = await _lxns_best50_raw(qqid, all_perfect=False)
            records = await _lxns_records_raw(qqid, exact=False)
        else:
            player, records = await _divingfish_dev_records_raw(
                qqid=qqid, username=username
            )
        best50 = select_star_b50_records(
            records,
            min_dx_star,
            _is_new_song,
            _dx_star_for_record,
        )
    elif is_lxns(qqid, username):
        player, best50 = await _lxns_best50_raw(
            qqid, all_perfect=all_perfect
        )
    elif all_perfect:
        player, best50 = await _divingfish_ap50_raw(
            qqid=qqid, username=username
        )
    else:
        user = await maiApi.query_user_b50(qqid=qqid, username=username)
        return userinfo_to_player(user), userinfo_to_best50(user)

    if all_perfect or all_perfect_plus or min_dx_star is not None or fitted:
        filtered_rating = best50.sd_total + best50.dx_total
        player = player.model_copy(update={'rating': filtered_rating})
    return player, best50


def select_ap50_records(
    records: List[PlayedResult],
    is_new_song: Callable[[int], Optional[bool]],
) -> Best50:
    """从完整成绩中选出旧曲 AP35 与新曲 AP15。"""
    old_records: List[PlayedResult] = []
    new_records: List[PlayedResult] = []

    for record in records:
        if (record.fc or '').lower() not in {'ap', 'app'}:
            continue
        is_new = is_new_song(record.song_id)
        if is_new is None:
            continue
        (new_records if is_new else old_records).append(record)

    sort_key = lambda r: (
        r.rating,
        r.achievements,
        r.level_value,
        r.song_id,
        r.level_index,
    )
    ap35 = sorted(old_records, key=sort_key, reverse=True)[:35]
    ap15 = sorted(new_records, key=sort_key, reverse=True)[:15]
    return Best50(
        sd_total=sum(record.rating for record in ap35),
        dx_total=sum(record.rating for record in ap15),
        sd=ap35,
        dx=ap15,
    )


def select_ap_plus50_records(
    records: List[PlayedResult],
    is_new_song: Callable[[int], Optional[bool]],
) -> Best50:
    """从玩家完整成绩中选出旧曲 AP+35 与新曲 AP+15。"""
    old_records: List[PlayedResult] = []
    new_records: List[PlayedResult] = []
    for record in records:
        if (record.fc or '').lower() != 'app':
            continue
        is_new = is_new_song(record.song_id)
        if is_new is None:
            continue
        (new_records if is_new else old_records).append(record)

    sort_key = lambda r: (
        r.rating,
        r.achievements,
        r.level_value,
        r.song_id,
        r.level_index,
    )
    app35 = sorted(old_records, key=sort_key, reverse=True)[:35]
    app15 = sorted(new_records, key=sort_key, reverse=True)[:15]
    return Best50(
        sd_total=sum(record.rating for record in app35),
        dx_total=sum(record.rating for record in app15),
        sd=app35,
        dx=app15,
    )


def select_star_b50_records(
    records: List[PlayedResult],
    min_dx_star: int,
    is_new_song: Callable[[int], Optional[bool]],
    dx_star_of: Callable[[PlayedResult], Optional[int]],
) -> Best50:
    """从完整成绩中选出 DX SCORE 至少 N 星的 B35 与 B15。"""
    if not 1 <= min_dx_star <= 5:
        raise ValueError('DX 星级必须在 1 到 5 之间')

    old_records: List[PlayedResult] = []
    new_records: List[PlayedResult] = []
    for record in records:
        dx_star = dx_star_of(record)
        if dx_star is None or dx_star < min_dx_star:
            continue
        is_new = is_new_song(record.song_id)
        if is_new is None:
            continue
        (new_records if is_new else old_records).append(record)

    sort_key = lambda r: (
        r.rating,
        r.achievements,
        r.level_value,
        r.song_id,
        r.level_index,
    )
    b35 = sorted(old_records, key=sort_key, reverse=True)[:35]
    b15 = sorted(new_records, key=sort_key, reverse=True)[:15]
    return Best50(
        sd_total=sum(record.rating for record in b35),
        dx_total=sum(record.rating for record in b15),
        sd=b35,
        dx=b15,
    )


def select_fitted_b50_records(
    records: List[PlayedResult],
    is_new_song: Callable[[int], Optional[bool]],
    fitted_level_value_of: Callable[[PlayedResult], Optional[float]],
    rating_of: Callable[[float, float], int],
) -> Best50:
    """用拟合定数重算 Rating，并选出拟合 B35 与 B15。"""
    old_records: List[PlayedResult] = []
    new_records: List[PlayedResult] = []
    for record in records:
        fitted_level_value = fitted_level_value_of(record)
        if fitted_level_value is None or fitted_level_value <= 0:
            continue
        is_new = is_new_song(record.song_id)
        if is_new is None:
            continue
        fitted_record = record.model_copy(
            update={
                'level_value': fitted_level_value,
                'rating': rating_of(fitted_level_value, record.achievements),
            }
        )
        (new_records if is_new else old_records).append(fitted_record)

    sort_key = lambda r: (
        r.rating,
        r.achievements,
        r.level_value,
        r.song_id,
        r.level_index,
    )
    b35 = sorted(old_records, key=sort_key, reverse=True)[:35]
    b15 = sorted(new_records, key=sort_key, reverse=True)[:15]
    return Best50(
        sd_total=sum(record.rating for record in b35),
        dx_total=sum(record.rating for record in b15),
        sd=b35,
        dx=b15,
    )


def _is_new_song(song_id: int) -> Optional[bool]:
    music = mai.total_list.by_id(str(song_id))
    return music.basic_info.is_new if music else None


def _dx_star_for_record(record: PlayedResult) -> Optional[int]:
    """取得成绩的 DX SCORE 星级；水鱼成绩按谱面物量计算。"""
    if record.dx_star is not None and (
        record.dx_star > 0 or record.dx_score <= 0
    ):
        return record.dx_star
    music = mai.total_list.by_id(str(record.song_id))
    if (
        not music
        or record.level_index < 0
        or record.level_index >= len(music.charts)
    ):
        return None
    max_dx_score = sum(music.charts[record.level_index].notes) * 3
    return dx_star_from_scores(record.dx_score, max_dx_score)


def _fitted_level_value(record: PlayedResult) -> Optional[float]:
    """读取谱面拟合定数；暂无统计时回退到官方定数。"""
    music = mai.total_list.by_id(str(record.song_id))
    if music and music.stats and 0 <= record.level_index < len(music.stats):
        stats = music.stats[record.level_index]
        if stats and stats.fit_diff is not None:
            return round(stats.fit_diff, 2)
    return record.level_value or None


def _rating_for_level_value(level_value: float, achievements: float) -> int:
    """按指定定数和达成率计算单曲 Rating。"""
    from .maimai_best_50 import computeRa

    return int(computeRa(level_value, achievements))


async def _divingfish_dev_records_raw(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
) -> Tuple[Player, List[PlayedResult]]:
    """读取水鱼开发者接口的完整成绩。"""
    if not maiApi.token:
        raise TokenNotFoundError
    user = await maiApi.query_user_get_dev(qqid=qqid, username=username)
    records = [playinfo_to_played(record) for record in (user.records or [])]
    return userinfo_to_player(user), records


async def _divingfish_ap50_raw(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
) -> Tuple[Player, Best50]:
    """使用水鱼开发者接口的完整成绩生成 AP50。"""
    player, records = await _divingfish_dev_records_raw(
        qqid=qqid, username=username
    )
    return player, select_ap50_records(records, _is_new_song)


async def get_records(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
    *,
    exact: bool = False,
) -> List[PlayedResult]:
    """获取全部成绩（统一 PlayedResult）。

    水鱼优先开发者全量 records；无 token / 失败时回退 plate（全版本）。
    """
    from .. import plate_to_dx_version
    if is_lxns(qqid, username):
        return await _lxns_records_raw(qqid, exact=exact)

    try:
        dev = await maiApi.query_user_get_dev(qqid=qqid, username=username)
        if dev.records:
            return [playinfo_to_played(r) for r in dev.records]
    except Exception:
        pass

    from .maimai_best_50 import computeRa
    from .maimaidx_play_result import lookup_meta

    plate = await maiApi.query_user_plate(
        qqid=qqid,
        username=username,
        version=list(plate_to_dx_version.values()),
    )
    result: List[PlayedResult] = []
    for r in plate:
        p = playinfo_to_played(r)
        if not p.level_value:
            ds, level, title = lookup_meta(p.song_id, p.level_index)
            ra, rate = (
                computeRa(ds, p.achievements, israte=True) if ds else (p.rating, p.rate or 'd')
            )
            p = p.model_copy(
                update={
                    'level_value': ds,
                    'level': level or p.level,
                    'song_name': title or p.song_name,
                    'rating': int(ra) if ds else p.rating,
                    'rate': (rate.lower() if isinstance(rate, str) else p.rate),
                }
            )
        result.append(p)
    return result


async def get_player_b50_userinfo(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
    *,
    all_perfect: bool = False,
    min_dx_star: Optional[int] = None,
    fitted: bool = False,
    all_perfect_plus: bool = False,
) -> UserInfo:
    """统一取 b50 并转为 UserInfo，供现有绘图直接使用。"""
    player, best50 = await get_best50(
        qqid,
        username,
        all_perfect=all_perfect,
        min_dx_star=min_dx_star,
        fitted=fitted,
        all_perfect_plus=all_perfect_plus,
    )
    return best50_to_userinfo(player, best50)


async def get_plate(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
    *,
    version: Optional[List[str]] = None,
    exact: bool = False,
) -> List[PlayInfoDefault]:
    """统一取成绩列表（PlayInfoDefault），水鱼可按 version 过滤。"""
    if is_lxns(qqid, username):
        return await lxns_plate(qqid, exact=exact)
    if version is None:
        from .. import plate_to_dx_version

        version = list(plate_to_dx_version.values())
    return await maiApi.query_user_plate(qqid=qqid, username=username, version=version)


async def get_player_records(
    qqid: Optional[Union[int, str]] = None,
    username: Optional[str] = None,
    *,
    exact: bool = False,
) -> List[PlayInfoDev]:
    """统一取成绩列表（PlayInfoDev），供进度 / 分数列表使用。"""
    return played_list_to_records(
        await get_records(qqid=qqid, username=username, exact=exact)
    )


async def get_music_record(
    qqid: Union[int, str],
    music_id: Union[int, str],
    username: Optional[str] = None,
) -> List[PlayInfoDev]:
    """统一取单曲成绩。无开发者 token 时用水鱼 plate 全量再按曲目过滤。"""
    from .. import plate_to_dx_version
    from .maimaidx_play_result import playinfo_to_played

    if is_lxns(qqid, username):
        return await lxns_music_record(qqid, music_id)
    if maiApi.token:
        return await maiApi.query_user_post_dev(
            qqid=qqid, username=username, music_id=music_id
        )
    version = list(set(plate_to_dx_version.values()))
    plate = await maiApi.query_user_plate(
        qqid=qqid, username=username, version=version
    )
    mid = int(music_id)
    played = [
        playinfo_to_played(r) for r in plate if int(getattr(r, 'song_id', 0)) == mid
    ]
    return played_list_to_records(played)


# ---------------------------------------------------------------------------
# 落雪取数（兼容旧导出：仍返回水鱼模型）
# ---------------------------------------------------------------------------
async def _lxns_best50_raw(
    qqid: Union[int, str], *, all_perfect: bool = False
) -> Tuple[Player, Best50]:
    user = userstore.get(int(qqid))
    api = LxnsAPI(qqid=user.qqid, access_token=user.access_token)
    if user.access_token and not all_perfect:
        player = await api.player_personal()
        best50 = await api.bests_personal()
    else:
        if not maiApi.config.lxns_dev_token:
            raise LxnsFeatureUnavailable(
                'ap50 / 开发者查询需要 BOT 管理员配置落雪开发者 Token'
                if all_perfect
                else 'BOT 管理员未配置落雪开发者 Token，无法按 QQ 查询'
            )
        fc = await _friend_code(user)
        player = await api.player_by_qq(user.qqid)
        best50 = await api.bests_by_friend_code(fc, ap=all_perfect)
    return lxns_best50_to_best50(player, best50)


async def _lxns_records_raw(
    qqid: Union[int, str], *, exact: bool = False
) -> List[PlayedResult]:
    user = userstore.get(int(qqid))
    api = LxnsAPI(qqid=user.qqid, access_token=user.access_token)
    if user.access_token:
        scores = await api.all_scores_personal()
        return lxns_scores_to_played(scores)
    if exact:
        raise LxnsFeatureUnavailable(
            '该功能需要精确达成率，落雪「开发者 Token」模式不支持；'
            '请使用「绑定落雪」进行 OAuth 授权，或用「数据源」切回水鱼。'
        )
    if not maiApi.config.lxns_dev_token:
        raise LxnsFeatureUnavailable('BOT 管理员未配置落雪开发者 Token')
    fc = await _friend_code(user)
    scores = await api.simple_scores_by_friend_code(fc)
    return lxns_scores_to_played(scores)


async def lxns_b50(qqid: Union[int, str], all_perfect: bool = False) -> UserInfo:
    player, best50 = await _lxns_best50_raw(qqid, all_perfect=all_perfect)
    return best50_to_userinfo(player, best50)


async def lxns_records(qqid: Union[int, str], *, exact: bool = False) -> List[PlayInfoDev]:
    return played_list_to_records(await _lxns_records_raw(qqid, exact=exact))


async def lxns_plate(qqid: Union[int, str], *, exact: bool = False) -> List[PlayInfoDefault]:
    return played_list_to_plate(await _lxns_records_raw(qqid, exact=exact))


async def lxns_music_record(qqid: Union[int, str], music_id: Union[int, str]) -> List[PlayInfoDev]:
    user = userstore.get(int(qqid))
    api = LxnsAPI(qqid=user.qqid, access_token=user.access_token)
    df_id = int(music_id)
    if df_id >= 100000:
        song_type, lxns_id = 'utage', df_id
    elif df_id >= 10000:
        song_type, lxns_id = 'dx', df_id - 10000
    else:
        song_type, lxns_id = 'standard', df_id
    if user.access_token:
        scores = await api.song_bests_personal(lxns_id, song_type)
    else:
        if not maiApi.config.lxns_dev_token:
            raise LxnsFeatureUnavailable('BOT 管理员未配置落雪开发者 Token')
        fc = await _friend_code(user)
        scores = await api.song_bests_by_friend_code(fc, lxns_id, song_type)
    return lxns_scores_to_records(scores)


# 兼容旧符号
__all__ = [
    'get_service',
    'is_lxns',
    'get_best50',
    'select_ap50_records',
    'select_ap_plus50_records',
    'select_star_b50_records',
    'select_fitted_b50_records',
    'get_records',
    'get_player_b50_userinfo',
    'get_plate',
    'get_player_records',
    'get_music_record',
    'lxns_b50',
    'lxns_records',
    'lxns_plate',
    'lxns_music_record',
    'lxns_score_to_playinfodefault',
]
