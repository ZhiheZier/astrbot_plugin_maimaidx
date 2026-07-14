"""查分数据源路由。

根据用户设置的数据源（水鱼 / 落雪）分发查询请求，并统一返回水鱼同款模型，
使现有绘图逻辑无需改动即可同时支持两个查分器。
"""

from typing import List, Optional, Union

from .maimaidx_api_data import maiApi
from .maimaidx_lxns import (
    LxnsAPI,
    LxnsFeatureUnavailable,
    lxns_best50_to_userinfo,
    lxns_score_to_playinfodefault,
    lxns_scores_to_records,
)
from .maimaidx_model import PlayInfoDefault, PlayInfoDev, UserInfo
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
# 落雪取数
# ---------------------------------------------------------------------------
async def lxns_b50(qqid: Union[int, str], all_perfect: bool = False) -> UserInfo:
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
    return lxns_best50_to_userinfo(player, best50)


async def lxns_records(qqid: Union[int, str], *, exact: bool = False) -> List[PlayInfoDev]:
    user = userstore.get(int(qqid))
    api = LxnsAPI(qqid=user.qqid, access_token=user.access_token)
    if user.access_token:
        scores = await api.all_scores_personal()
        return lxns_scores_to_records(scores)
    # 开发者模式
    if exact:
        raise LxnsFeatureUnavailable(
            '该功能需要精确达成率，落雪「开发者 Token」模式不支持；'
            '请使用「绑定落雪」进行 OAuth 授权，或用「数据源」切回水鱼。'
        )
    if not maiApi.config.lxns_dev_token:
        raise LxnsFeatureUnavailable('BOT 管理员未配置落雪开发者 Token')
    fc = await _friend_code(user)
    scores = await api.simple_scores_by_friend_code(fc)
    return lxns_scores_to_records(scores)


async def lxns_plate(qqid: Union[int, str], *, exact: bool = False) -> List[PlayInfoDefault]:
    user = userstore.get(int(qqid))
    api = LxnsAPI(qqid=user.qqid, access_token=user.access_token)
    if user.access_token:
        scores = await api.all_scores_personal()
    else:
        if exact:
            raise LxnsFeatureUnavailable(
                '该功能需要精确达成率，落雪「开发者 Token」模式不支持；'
                '请使用「绑定落雪」进行 OAuth 授权，或用「数据源」切回水鱼。'
            )
        if not maiApi.config.lxns_dev_token:
            raise LxnsFeatureUnavailable('BOT 管理员未配置落雪开发者 Token')
        fc = await _friend_code(user)
        scores = await api.simple_scores_by_friend_code(fc)
    return [lxns_score_to_playinfodefault(s) for s in scores]


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
