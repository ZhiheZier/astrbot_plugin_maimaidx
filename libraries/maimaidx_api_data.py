import asyncio
import hashlib
import time
from typing import Any, Dict, Tuple

from aiohttp import ClientError, ClientSession, ClientTimeout

from .. import UUID
from .maimaidx_error import *
from .maimaidx_model import *


class MaiConfig(BaseModel):
    
    maimaidxtoken: Optional[str] = None
    df_client_id: Optional[str] = None
    df_client_secret: Optional[str] = None
    maimaidxproberproxy: bool = False
    maimaidxaliasproxy: bool = False
    maimaidxaliaspush: bool = True
    # True：仅向「开启别名推送」的群广播；False：向所有群广播，但排除 disable 列表（默认，兼容旧行为）
    maimaidxaliaswhitelist: bool = False
    saveinmem: Optional[bool] = True
    # 对于有 icon / plate 资源可设为 False 使用本地素材，否则默认在线获取（落雪查分器需要）
    assets_online: bool = True
    # 落雪查分器（lxns）相关配置
    lxns_dev_token: Optional[str] = None      # 开发者 Token（模式A：按 QQ 号查询）
    lx_client_id: Optional[str] = None        # OAuth 应用 client_id（模式B）
    lx_client_secret: Optional[str] = None    # OAuth 应用 client_secret（模式B）
    lx_redirect_uri: Optional[str] = None     # OAuth 回调地址（模式B）


class MaimaiAPI:
    
    MaiProxyAPI = 'https://proxy.yuzuchan.site'
    
    MaiProberAPI = 'https://www.diving-fish.com/api/maimaidxprober'
    MaiCover = 'https://www.diving-fish.com/covers'
    MaiAliasAPI = 'https://www.yuzuchan.moe/api/maimaidx'
    DivingFishAuthAPI = 'https://auth.diving-fish.com'
    DivingFishOAuthScope = 'prober.records.read'
    QQAPI = 'http://q1.qlogo.cn/g'
    
    def __init__(self) -> None:
        """封装Api"""
        self.config: MaiConfig = self.load_config()
        self.headers = None
        self.token = None
        self.MaiProberProxyAPI = None
        self.MaiAliasProxyAPI = None
        self._oauth_tokens: Dict[str, Tuple[str, float]] = {}
        self._oauth_locks: Dict[str, asyncio.Lock] = {}
        self._oauth_cache_client_id: Optional[str] = None
    
    def load_config(self) -> MaiConfig:
        # 配置改由 AstrBot 插件配置（_conf_schema.json）在插件初始化时注入，
        # 不再从 static/data/config.json 读取，此处仅提供默认值。
        return MaiConfig()
    
    def load_token_proxy(self) -> None:
        self.MaiProberProxyAPI = self.MaiProberAPI if not self.config.maimaidxproberproxy else self.MaiProxyAPI + '/maimaidxprober'
        self.MaiAliasProxyAPI = self.MaiAliasAPI if not self.config.maimaidxaliasproxy else self.MaiProxyAPI + '/maimaidxaliases'
        self.token = self.config.maimaidxtoken
        self.headers = None
        if self.token:
            self.headers = {'developer-token': self.token}
        if self._oauth_cache_client_id != self.config.df_client_id:
            self._oauth_tokens.clear()
            self._oauth_locks.clear()
            self._oauth_cache_client_id = self.config.df_client_id

    @property
    def divingfish_oauth_configured(self) -> bool:
        return bool(self.config.df_client_id and self.config.df_client_secret)

    def _divingfish_subject_ref(self, external_id: Union[int, str]) -> str:
        client_id = self.config.df_client_id
        if not client_id:
            raise DivingFishOAuthConfigError
        value = f'{client_id}:{external_id}'.encode()
        return hashlib.sha256(value).hexdigest()

    def _divingfish_subject(
        self,
        qqid: Optional[Union[int, str]] = None,
        username: Optional[str] = None,
    ) -> str:
        if username and username.strip():
            return f'username:{username.strip()}'
        if qqid is not None and str(qqid).strip():
            return f'ref:{self._divingfish_subject_ref(str(qqid).strip())}'
        raise UserNotFoundError

    @staticmethod
    def mask_qq(qqid: Union[int, str]) -> str:
        value = str(qqid).strip()
        if len(value) <= 4:
            return 'QQ ****'
        return f'QQ {value[:2]}****{value[-2:]}'

    async def _request_divingfish_oauth(
        self,
        endpoint: str,
        *,
        data: Dict[str, str],
    ) -> Dict[str, Any]:
        try:
            async with ClientSession(timeout=ClientTimeout(total=30)) as session:
                async with session.post(
                    self.DivingFishAuthAPI + endpoint,
                    data=data,
                ) as res:
                    try:
                        payload = await res.json(content_type=None)
                    except Exception:
                        payload = {}
                    if res.status == 200 and isinstance(payload, dict):
                        return payload

                    error = (
                        payload.get('error', '')
                        if isinstance(payload, dict)
                        else ''
                    )
                    description = (
                        payload.get('error_description', '')
                        if isinstance(payload, dict)
                        else ''
                    )
                    if error == 'consent_required':
                        raise DivingFishOAuthNotBoundError
                    if error == 'authorization_pending':
                        raise DivingFishOAuthPendingError
                    if error == 'access_denied':
                        raise DivingFishOAuthBindingDeniedError
                    if error == 'expired_token':
                        raise DivingFishOAuthBindingExpiredError
                    if error == 'invalid_client':
                        raise DivingFishOAuthConfigError
                    if (
                        error == 'invalid_scope'
                        or 'scope' in description.lower()
                    ):
                        raise DivingFishOAuthPermissionError
                    if error == 'slow_down' or res.status == 429:
                        raise DivingFishOAuthRateLimitError
                    if res.status >= 500:
                        raise DivingFishOAuthServiceError
                    raise DivingFishOAuthError
        except (ClientError, asyncio.TimeoutError) as exc:
            raise DivingFishOAuthServiceError from exc

    async def start_divingfish_binding(
        self,
        external_id: Union[int, str],
    ) -> Dict[str, Any]:
        """创建一次设备码授权，绑定关系由水鱼服务端持久化。"""
        if not self.divingfish_oauth_configured:
            raise DivingFishOAuthConfigError
        return await self._request_divingfish_oauth(
            '/oauth/device_authorization',
            data={
                'client_id': self.config.df_client_id,
                'client_secret': self.config.df_client_secret,
                'scope': self.DivingFishOAuthScope,
                'subject_ref': self._divingfish_subject_ref(external_id),
                'binding_label': self.mask_qq(external_id),
            },
        )

    async def _divingfish_access_token(self, subject: str) -> str:
        if not self.divingfish_oauth_configured:
            raise DivingFishOAuthConfigError

        cached = self._oauth_tokens.get(subject)
        if cached and time.monotonic() < cached[1] - 30:
            return cached[0]

        lock = self._oauth_locks.setdefault(subject, asyncio.Lock())
        async with lock:
            cached = self._oauth_tokens.get(subject)
            if cached and time.monotonic() < cached[1] - 30:
                return cached[0]

            payload = await self._request_divingfish_oauth(
                '/oauth/token',
                data={
                    'grant_type': (
                        'urn:diving-fish:params:oauth:grant-type:on-behalf-of'
                    ),
                    'client_id': self.config.df_client_id,
                    'client_secret': self.config.df_client_secret,
                    'subject': subject,
                    'scope': self.DivingFishOAuthScope,
                },
            )
            token = payload.get('access_token')
            if not token:
                raise DivingFishOAuthServiceError
            expires_in = max(int(payload.get('expires_in', 300)), 1)
            self._oauth_tokens[subject] = (
                str(token),
                time.monotonic() + expires_in,
            )
            return str(token)

    async def wait_for_divingfish_binding(
        self,
        external_id: Union[int, str],
        device_code: str,
        *,
        interval: int = 5,
        expires_in: int = 600,
    ) -> None:
        """轮询设备码授权结果，成功后缓存本次返回的访问令牌。"""
        subject = self._divingfish_subject(qqid=external_id)
        poll_interval = max(int(interval), 1)
        deadline = time.monotonic() + max(int(expires_in), 1)

        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            try:
                payload = await self._request_divingfish_oauth(
                    '/oauth/token',
                    data={
                        'grant_type': (
                            'urn:ietf:params:oauth:grant-type:device_code'
                        ),
                        'device_code': device_code,
                        'client_id': self.config.df_client_id,
                        'client_secret': self.config.df_client_secret,
                    },
                )
            except DivingFishOAuthPendingError:
                continue
            except DivingFishOAuthRateLimitError:
                poll_interval += 5
                continue

            token = payload.get('access_token')
            if not token:
                raise DivingFishOAuthServiceError
            token_expires_in = max(int(payload.get('expires_in', 300)), 1)
            self._oauth_tokens[subject] = (
                str(token),
                time.monotonic() + token_expires_in,
            )
            return

        raise DivingFishOAuthBindingExpiredError

    async def _requestmai_oauth(
        self,
        method: str,
        endpoint: str,
        *,
        qqid: Optional[Union[int, str]] = None,
        username: Optional[str] = None,
        **kwargs,
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        subject = self._divingfish_subject(qqid=qqid, username=username)
        token = await self._divingfish_access_token(subject)
        try:
            return await self._requestmai(
                method,
                endpoint,
                request_headers={'Authorization': f'Bearer {token}'},
                **kwargs,
            )
        except DivingFishOAuthTokenExpiredError:
            self._oauth_tokens.pop(subject, None)
            token = await self._divingfish_access_token(subject)
            return await self._requestmai(
                method,
                endpoint,
                request_headers={'Authorization': f'Bearer {token}'},
                **kwargs,
            )
    
    
    async def _requestalias(self, method: str, endpoint: str, **kwargs) -> APIResult:
        """
        别名库通用请求

        Params:
            `method`: 请求方式
            `endpoint`: 请求接口
            `kwargs`: 其它参数
        Returns:
            `APIResult` 返回结果
        """
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            async with session.request(method, self.MaiAliasProxyAPI + endpoint, **kwargs) as res:
                if res.status == 200:
                    data = await res.json()
                    return APIResult.model_validate(data)
                elif res.status == 500:
                    raise ServerError
                else:
                    raise UnknownError

    async def _requestmai(
        self, 
        method: str, 
        endpoint: str, 
        request_headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        查分器通用请求

        Params:
            `method`: 请求方式
            `endpoint`: 请求接口
            `kwargs`: 其它参数
        Returns:
            `Dict[str, Any]` 返回结果
        """
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            async with session.request(
                method, 
                self.MaiProberProxyAPI + endpoint, 
                headers=(self.headers if request_headers is None else request_headers),
                **kwargs
            ) as res:
                if res.status == 200:
                    data = await res.json()
                elif res.status == 400:
                    error: Dict = await res.json()
                    if 'message' in error:
                        if error['message'] == 'no such user':
                            raise UserNotFoundError
                        elif error['message'] == 'user not exists':
                            raise UserNotExistsError
                        else:
                            raise UserNotFoundError
                    elif 'msg' in error:
                        if error['msg'] == '开发者token有误':
                            raise TokenError
                        elif error['msg'] == '开发者token被禁用':
                            raise TokenDisableError
                        else:
                            raise TokenNotFoundError
                    else:
                        raise UserNotFoundError
                elif res.status == 403:
                    error = await res.json(content_type=None)
                    message = str(error.get('message', '')) if isinstance(error, dict) else ''
                    if request_headers and 'Authorization' in request_headers:
                        if '权限' in message or 'scope' in message.lower():
                            raise DivingFishOAuthPermissionError
                    raise UserDisabledQueryError
                elif res.status == 401:
                    if request_headers and 'Authorization' in request_headers:
                        raise DivingFishOAuthTokenExpiredError
                    raise TokenError
                elif res.status == 410:
                    raise DivingFishLegacyApiRetiredError
                elif res.status == 429:
                    raise DivingFishOAuthRateLimitError
                elif res.status == 503:
                    raise DivingFishOAuthServiceError
                else:
                    raise UnknownError
        return data
    
    async def music_data(self):
        """获取曲目数据"""
        return await self._requestmai(
            'GET', '/music_data', request_headers={}
        )

    async def chart_stats(self):
        """获取单曲数据"""
        return await self._requestmai(
            'GET', '/chart_stats', request_headers={}
        )

    async def query_user_b50(
        self, 
        *, 
        qqid: Optional[int] = None, 
        username: Optional[str] = None
    ) -> UserInfo:
        """
        获取玩家B50
        
        Params:
            `qqid`: QQ号
            `username`: 用户名
        Returns:
            `UserInfo` b50数据模型
        """
        json = {}
        if qqid:
            json['qq'] = qqid
        if username:
            json['username'] = username
        json['b50'] = True

        return UserInfo.model_validate(
            await self._requestmai(
                'POST',
                '/query/player',
                request_headers={},
                json=json,
            )
        )

    async def query_user_plate(
        self,
        *,
        qqid: Optional[int] = None,
        username: Optional[str] = None,
        version: Optional[List[str]] = None
    ) -> List[PlayInfoDefault]:
        """
        请求用户数据

        Params:
            `qqid`: 用户QQ
            `username`: 查分器用户名
            `version`: 版本
        Returns:
            `List[PlayInfoDefault]` 数据列表
        """
        json = {}
        if version:
            json['version'] = version
        if self.divingfish_oauth_configured:
            result = await self._requestmai_oauth(
                'POST',
                '/player/plate',
                qqid=qqid,
                username=username,
                json=json,
            )
        else:
            if qqid:
                json['qq'] = qqid
            if username:
                json['username'] = username
            result = await self._requestmai('POST', '/query/plate', json=json)
        return [PlayInfoDefault.model_validate(d) for d in result['verlist']]

    async def query_user_get_dev(
        self, 
        *, 
        qqid: Optional[int] = None, 
        username: Optional[str] = None
    ) -> UserInfoDev:
        """
        使用开发者接口获取用户数据，请确保拥有和输入了开发者 `token`

        Params:
            qqid: 用户QQ
            username: 查分器用户名
        Returns:
            `UserInfoDev` 开发者用户信息
        """
        if self.divingfish_oauth_configured:
            result = await self._requestmai_oauth(
                'GET',
                '/player/records',
                qqid=qqid,
                username=username,
            )
        else:
            params = {}
            if qqid:
                params['qq'] = qqid
            if username:
                params['username'] = username
            result = await self._requestmai(
                'GET', '/dev/player/records', params=params
            )
        return UserInfoDev.model_validate(result)

    async def query_user_post_dev(
        self,
        *,
        qqid: Optional[int] = None,
        username: Optional[str] = None,
        music_id: Union[str, int, List[Union[str, int]]]
    ) -> List[PlayInfoDev]:
        """
        使用开发者接口获取用户指定曲目数据，请确保拥有和输入了开发者 `token`

        Params:
            `qqid`: 用户QQ
            `username`: 查分器用户名
            `music_id`: 曲目id，可以为单个ID或者列表
        Returns:
            `List[PlayInfoDev]` 开发者成绩列表
        """
        json = {'music_id': music_id}
        if self.divingfish_oauth_configured:
            result = await self._requestmai_oauth(
                'POST',
                '/player/record',
                qqid=qqid,
                username=username,
                json=json,
            )
        else:
            if qqid:
                json['qq'] = qqid
            if username:
                json['username'] = username
            result = await self._requestmai(
                'POST', '/dev/player/record', json=json
            )
        if result == {}:
            raise MusicNotPlayError
        
        if isinstance(music_id, list):
            return [PlayInfoDev.model_validate(d) for k, v in result.items() for d in v]
        return [PlayInfoDev.model_validate(d) for d in result[str(music_id)]]

    async def rating_ranking(self) -> List[UserRanking]:
        """
        获取查分器排行榜
        
        Returns:
            `List[UserRanking]` 按`ra`从高到低排序后的查分器排行模型列表
        """
        result = await self._requestmai(
            'GET', '/rating_ranking', request_headers={}
        )
        return sorted([UserRanking.model_validate(u) for u in result], key=lambda x: x.ra, reverse=True)

    async def get_plate_json(self) -> Dict[str, List[int]]:
        """获取所有版本牌子完成需求"""
        result = await self._requestalias('GET', '/maimaidxplate')
        if result.code == 0:
            return result.content
        raise UnknownError
    
    async def get_alias(self) -> Dict[str, Union[str, int, List[str]]]:
        """获取所有别名"""
        result = await self._requestalias('GET', '/maimaidxalias')
        if result.code == 0:
            return result.content
        raise UnknownError

    async def get_songs(self, name: str) -> Union[List[AliasStatus], List[Alias]]:
        """
        使用别名查询曲目。
        `code` 为 `0` 时返回值为 `List[Alias]`。
        `code` 为 `3006` 时返回值为 `List[AliasStatus]`。
        
        Params:
            `name`: 别名
        Returns:
            `Union[List[AliasStatus], List[Alias]]`
        """
        result = await self._requestalias('GET', '/getsongs', params={'name': name})
        if result.code == 3006:
            return [AliasStatus.model_validate(s) for s in result.content]
        elif result.code == 1004:
            return []
        elif result.code == 0:
            return [Alias.model_validate(s) for s in result.content]
        else:
            raise UnknownError

    async def get_songs_alias(self, song_id: int) -> Alias:
        """
        使用曲目 `id` 查询别名
        
        Params:
            `song_id`: 曲目 `ID`
        Returns:
            `Alias` | `str`
        """
        result = await self._requestalias('GET', '/getsongsalias', params={'song_id': song_id})
        if result.code == 0:
            return Alias.model_validate(result.content)
        elif result.code == 1004:
            return result.content
        else:
            raise UnknownError

    async def get_alias_status(self) -> List[AliasStatus]:
        """获取当前正在进行的别名投票"""
        result = await self._requestalias('GET', '/getaliasstatus')
        if result.code == 0:
            return [AliasStatus.model_validate(s) for s in result.content]
        elif result.code == 1004:
            return []
        else:
            raise UnknownError

    async def post_alias(
        self, 
        song_id: int, 
        aliasname: str, 
        user_id: int,
        group_id: int
    ) -> Union[AliasStatus, str]:
        """
        提交别名申请

        Params:
            `id`: 曲目 `id`
            `aliasname`: 别名
            `user_id`: 提交的用户
        Returns:
            `AliasStatus`
        """
        json = {
            'SongID': song_id,
            'ApplyAlias': aliasname,
            'ApplyUID': user_id,
            'GroupID': group_id,
            'WSUUID': str(UUID)
        }
        result = await self._requestalias('POST', '/applyalias', json=json)
        return result.content
    
    async def post_agree_user(self, tag: str, user_id: int) -> str:
        """
        提交同意投票

        Params:
            `tag`: 标签
            `user_id`: 同意投票的用户
        Returns:
            `str`
        """
        json = {
            'Tag': tag,
            'AgreeUser': user_id
        }
        result = await self._requestalias('POST', '/agreeuser', json=json)
        return result.content

    async def qqlogo(self, qqid: int = None, icon: str = None) -> Optional[bytes]:
        """获取QQ头像"""
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            if qqid:
                params = {
                    'b': 'qq',
                    'nk': qqid,
                    's': 100
                }
                res = await session.request('GET', self.QQAPI, params=params)
            elif icon:
                res = await session.request('GET', icon)
            else:
                return None
            return await res.read()


maiApi = MaimaiAPI()
