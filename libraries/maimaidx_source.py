"""查分数据源路由。

根据用户设置的数据源（水鱼 / 落雪）分发查询请求。
内部统一为 PlayedResult / Best50 / Player，对外仍可转水鱼模型以复用绘图。
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Union

from .maimaidx_api_data import maiApi
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
    played_list_to_plate,
    played_list_to_records,
    userinfo_to_best50,
    userinfo_to_player,
)
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
) -> Tuple[Player, Best50]:
    """按用户数据源获取 b50 / ap50，返回统一 Player + Best50。"""
    if is_lxns(qqid, username):
        return await _lxns_best50_raw(qqid, all_perfect=all_perfect)
    user = await maiApi.query_user_b50(qqid=qqid, username=username)
    return userinfo_to_player(user), userinfo_to_best50(user)


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
    from .maimaidx_play_result import playinfo_to_played

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
) -> UserInfo:
    """统一取 b50 并转为 UserInfo，供现有绘图直接使用。"""
    player, best50 = await get_best50(qqid, username, all_perfect=all_perfect)
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
