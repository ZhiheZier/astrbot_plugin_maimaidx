"""落雪查分器（lxns）客户端与数据适配层。

将落雪的成绩数据转换为水鱼（diving-fish）同款的数据模型，
从而复用现有的全部绘图逻辑。支持两种接入模式：

- 开发者 Token 模式（按 QQ 号查询）：仅需管理员配置 `lxns_dev_token`，
  用户在落雪绑定 QQ 即可。支持 b50 / ap50 / 单曲成绩 / 完成表 / 进度 等
  （基于 rate/fc/fs 的功能），但「分数列表」「我要上分」需要精确达成率，
  开发者接口的批量成绩为简化版，故这两项在该模式下不可用。
- OAuth 模式：用户授权后可获取包含精确达成率的全部成绩，功能完整。
"""

from typing import List, Optional

from aiohttp import ClientSession, ClientTimeout
from pydantic import BaseModel

from .. import diffs
from .maimaidx_api_data import maiApi
from .maimaidx_model import ChartInfo, Data, PlayInfoDefault, PlayInfoDev, UserInfo
from .maimaidx_music import mai

LXNS_BASE = 'https://maimai.lxns.net'
DEV_BASE = f'{LXNS_BASE}/api/v0/maimai'
USER_BASE = f'{LXNS_BASE}/api/v0/user/maimai/player'
OAUTH_TOKEN_URL = f'{LXNS_BASE}/api/v0/oauth/token'
AUTHORIZE_URL = f'{LXNS_BASE}/oauth/authorize'

# rate -> 代表达成率（各评级下界），用于开发者模式下由简化成绩推导达成率阈值
RATE_TO_ACHIEVEMENTS = {
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


class LxnsError(Exception):
    """落雪通用错误"""


class LxnsFeatureUnavailable(LxnsError):
    """当前落雪接入模式不支持该功能"""


class LxnsNotBindError(LxnsError):
    def __str__(self) -> str:
        return (
            '未查询到你的落雪成绩。\n'
            '※ 请先在落雪查分器绑定 QQ 号，并在「账号设置 -> 隐私设置」中允许第三方读取成绩；\n'
            '※ 或使用「绑定落雪」指令进行 OAuth 授权。'
        )


# ---------------------------------------------------------------------------
# 数据模型（仅保留需要的字段）
# ---------------------------------------------------------------------------
class LxnsToken(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Optional[str] = 'Bearer'
    expires_in: Optional[int] = None
    scope: Optional[str] = None


class LxnsPlayer(BaseModel):
    name: str = ''
    rating: int = 0
    friend_code: int = 0
    course_rank: int = 0
    class_rank: int = 0
    star: int = 0


class LxnsScore(BaseModel):
    id: int
    song_name: Optional[str] = None
    level: Optional[str] = None
    level_index: int
    achievements: Optional[float] = None
    fc: Optional[str] = None
    fs: Optional[str] = None
    rate: Optional[str] = None
    dx_score: Optional[int] = 0
    dx_star: Optional[int] = 0
    dx_rating: Optional[float] = None
    type: str


class LxnsBest50(BaseModel):
    standard_total: int = 0
    dx_total: int = 0
    standard: List[LxnsScore] = []
    dx: List[LxnsScore] = []


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
class LxnsAPI:
    def __init__(
        self,
        *,
        qqid: Optional[int] = None,
        access_token: Optional[str] = None,
        friend_code: Optional[int] = None,
    ) -> None:
        self.qqid = qqid
        self.access_token = access_token
        self.friend_code = friend_code

    @staticmethod
    def _dev_headers() -> dict:
        token = maiApi.config.lxns_dev_token
        if not token:
            raise LxnsFeatureUnavailable('BOT 管理员未配置落雪开发者 Token')
        return {'Authorization': token}

    def _user_headers(self) -> dict:
        if not self.access_token:
            raise LxnsFeatureUnavailable('用户未进行落雪 OAuth 授权')
        return {'Authorization': f'Bearer {self.access_token}'}

    async def _request(
        self, method: str, url: str, *, headers: dict, **kwargs
    ) -> dict:
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            async with session.request(method, url, headers=headers, **kwargs) as res:
                try:
                    data = await res.json()
                except Exception:
                    data = {}
                if res.status == 200:
                    return data
                if res.status == 401:
                    raise LxnsFeatureUnavailable('落雪授权失效，请重新绑定')
                if res.status == 404:
                    raise LxnsNotBindError
                if res.status == 403:
                    raise LxnsError('落雪：无权限访问该数据（请检查隐私设置）')
                if res.status == 429:
                    raise LxnsError('落雪：请求过于频繁，请稍后再试')
                raise LxnsError(f'落雪请求错误：HTTP {res.status}')

    # ---- OAuth ----
    async def oauth_fetch_token(self, code: str) -> LxnsToken:
        json = {
            'client_id': maiApi.config.lx_client_id,
            'client_secret': maiApi.config.lx_client_secret,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': maiApi.config.lx_redirect_uri,
        }
        data = await self._request('POST', OAUTH_TOKEN_URL, headers={}, json=json)
        return LxnsToken.model_validate(data.get('data', data))

    async def oauth_refresh_token(self, refresh_token: str) -> LxnsToken:
        json = {
            'client_id': maiApi.config.lx_client_id,
            'client_secret': maiApi.config.lx_client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
        data = await self._request('POST', OAUTH_TOKEN_URL, headers={}, json=json)
        return LxnsToken.model_validate(data.get('data', data))

    # ---- 玩家信息 ----
    async def player_by_qq(self, qq: int) -> LxnsPlayer:
        data = await self._request(
            'GET', f'{DEV_BASE}/player/qq/{qq}', headers=self._dev_headers()
        )
        return LxnsPlayer.model_validate(data['data'])

    async def player_personal(self) -> LxnsPlayer:
        data = await self._request('GET', USER_BASE, headers=self._user_headers())
        return LxnsPlayer.model_validate(data['data'])

    # ---- Best50 / AP50 ----
    async def bests_by_friend_code(self, friend_code: int, ap: bool = False) -> LxnsBest50:
        endpoint = f'{DEV_BASE}/player/{friend_code}/bests'
        if ap:
            endpoint += '/ap'
        data = await self._request('GET', endpoint, headers=self._dev_headers())
        return LxnsBest50.model_validate(data['data'])

    async def bests_personal(self) -> LxnsBest50:
        data = await self._request(
            'GET', f'{USER_BASE}/bests', headers=self._user_headers()
        )
        return LxnsBest50.model_validate(data['data'])

    # ---- 单曲成绩 ----
    async def song_bests_by_friend_code(
        self, friend_code: int, song_id: int, song_type: str
    ) -> List[LxnsScore]:
        params = {'song_id': song_id, 'song_type': song_type}
        data = await self._request(
            'GET',
            f'{DEV_BASE}/player/{friend_code}/bests',
            headers=self._dev_headers(),
            params=params,
        )
        return [LxnsScore.model_validate(s) for s in data.get('data', [])]

    async def song_bests_personal(
        self, song_id: int, song_type: str
    ) -> List[LxnsScore]:
        params = {'song_id': song_id, 'song_type': song_type}
        data = await self._request(
            'GET', f'{USER_BASE}/bests', headers=self._user_headers(), params=params
        )
        return [LxnsScore.model_validate(s) for s in data.get('data', [])]

    # ---- 全部成绩（含精确达成率，仅个人 OAuth 可用）----
    async def all_scores_personal(self) -> List[LxnsScore]:
        data = await self._request(
            'GET', f'{USER_BASE}/scores', headers=self._user_headers()
        )
        return [LxnsScore.model_validate(s) for s in data.get('data', [])]

    # ---- 全部成绩（简化，无达成率，开发者模式）----
    async def simple_scores_by_friend_code(self, friend_code: int) -> List[LxnsScore]:
        data = await self._request(
            'GET', f'{DEV_BASE}/player/{friend_code}/scores', headers=self._dev_headers()
        )
        return [LxnsScore.model_validate(s) for s in data.get('data', [])]


# ---------------------------------------------------------------------------
# 适配层：lxns -> 水鱼模型
# ---------------------------------------------------------------------------
def _df_song_id(lxns_id: int, song_type: str) -> int:
    """落雪曲目 ID（标准/DX 共用）转换为水鱼曲目 ID（DX 谱面 +10000）"""
    if song_type == 'dx':
        return lxns_id + 10000
    return lxns_id


def _type_str(song_type: str) -> str:
    return 'SD' if song_type == 'standard' else 'DX'


def _lookup(song_id: int, level_index: int):
    """返回 (ds, level, title)，从本地曲库获取"""
    music = mai.total_list.by_id(str(song_id))
    if music and music.ds and len(music.ds) > level_index:
        return music.ds[level_index], music.level[level_index], music.title
    return 0.0, '', ''


def _achievements(score: LxnsScore) -> float:
    if score.achievements is not None:
        return score.achievements
    if score.rate:
        return RATE_TO_ACHIEVEMENTS.get(score.rate.lower(), 0.0)
    return 0.0


def lxns_score_to_chartinfo(score: LxnsScore) -> ChartInfo:
    from .maimai_best_50 import computeRa

    song_id = _df_song_id(score.id, score.type)
    ds, level, title = _lookup(song_id, score.level_index)
    if not title:
        title = score.song_name or ''
    if not level:
        level = score.level or ''
    ach = _achievements(score)
    ra, rate = computeRa(ds, ach, israte=True) if ds else (0, (score.rate or 'd').upper())
    if score.rate:
        rate = score.rate
    return ChartInfo(
        achievements=ach,
        fc=score.fc or '',
        fs=score.fs or '',
        level=level,
        level_index=score.level_index,
        title=title,
        type=_type_str(score.type),
        ds=ds,
        dxScore=score.dx_score or 0,
        ra=ra,
        rate=rate.lower() if isinstance(rate, str) else rate,
        level_label=diffs[score.level_index] if score.level_index < len(diffs) else '',
        song_id=song_id,
    )


def lxns_score_to_playinfodefault(score: LxnsScore) -> PlayInfoDefault:
    from .maimai_best_50 import computeRa

    song_id = _df_song_id(score.id, score.type)
    ds, level, title = _lookup(song_id, score.level_index)
    if not title:
        title = score.song_name or ''
    if not level:
        level = score.level or ''
    ach = _achievements(score)
    ra, rate = computeRa(ds, ach, israte=True) if ds else (0, (score.rate or 'd').upper())
    if score.rate:
        rate = score.rate
    return PlayInfoDefault(
        id=song_id,
        achievements=ach,
        fc=score.fc or '',
        fs=score.fs or '',
        level=level,
        level_index=score.level_index,
        title=title,
        type=_type_str(score.type),
        ds=ds,
        dxScore=score.dx_score or 0,
        ra=ra,
        rate=rate.lower() if isinstance(rate, str) else rate,
    )


def lxns_best50_to_userinfo(player: LxnsPlayer, best50: LxnsBest50) -> UserInfo:
    sd = [lxns_score_to_chartinfo(s) for s in best50.standard]
    dx = [lxns_score_to_chartinfo(s) for s in best50.dx]
    return UserInfo(
        additional_rating=player.course_rank,
        nickname=player.name,
        plate=None,
        rating=player.rating,
        username=None,
        charts=Data(sd=sd, dx=dx),
    )


def lxns_scores_to_records(scores: List[LxnsScore]) -> List[PlayInfoDev]:
    result: List[PlayInfoDev] = []
    for s in scores:
        ci = lxns_score_to_chartinfo(s)
        result.append(PlayInfoDev.model_validate(ci.model_dump()))
    return result
