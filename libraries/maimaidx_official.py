"""Opt-in HTTPS-only official score retrieval, without native runtimes."""

import asyncio
import hashlib
import json
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientConnectionError, ClientHttpProxyError, ClientSession, ClientSSLError, ClientTimeout, TCPConnector


TITLE_URL = 'https://maimai-gm.wahlap.com:42081/Maimai2Servlet/'
LOGIN_HOSTS = ('ai.sys-allnet.cn', 'ai.sys-all.cn')
DEFAULT_KEYCHIP_ID = 'A63E-01E11890000'
DEFAULT_CLIENT_ID = 'A63E01E1189'
DEFAULT_PLACE_ID = 3496
_KEY = b'FKM2JX:VjZNK6hc:A0<JU:i5oR7LA]9W'
_IV = b'F>;24DjU9W6ZsRH['
MAX_RESPONSE = 8 * 1024 * 1024
SGID_PATTERN = re.compile(r'SGWCMAID([0-9]{12})([A-Fa-f0-9]{64})')
_LOGIN_SALT = 'XcW5FW4cPArBXEk4vzKz3CIrMuA5EVVW'
_ISSUER_SHA256 = 'd2ac9dcc7a255029a5f5180d63f1a9ed60e11412a85c148d4b71f6ae1a36695c'


class OfficialError(Exception):
    pass


class OfficialLoginError(OfficialError):
    def __init__(self, code: int, server_index: int | None = None):
        self.code = code
        self.server_index = server_index
        server = f'，线路 {server_index + 1}' if server_index is not None else ''
        super().__init__(f'官方 HTTPS 授权被拒绝（接口错误码 {code}{server}）。'
                         '请检查账号状态并获取新的 SGID 重试；此编号不是旧原生组件错误码。')


@lru_cache(maxsize=1)
def official_tls_context() -> ssl.SSLContext:
    import certifi

    try:
        pem = (Path(__file__).parent / 'certs/globalsign-r46-alphassl-2025.pem').read_text(encoding='ascii')
        der = ssl.PEM_cert_to_DER_cert(pem)
        if hashlib.sha256(der).hexdigest() != _ISSUER_SHA256:
            raise ValueError('issuer digest mismatch')
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=certifi.where())
        context.load_verify_locations(cadata=pem)
        # Supply a missing intermediate, but still require a trusted root.
        context.verify_flags &= ~getattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN', 0)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context
    except (OSError, ValueError):
        raise OfficialError('官方 HTTPS 证书链初始化失败，请重新安装插件并检查 CA 证书依赖。') from None


@dataclass(frozen=True)
class OfficialSession:
    user_id: int = field(repr=False)
    token: str = field(repr=False)


@dataclass(frozen=True)
class OfficialData:
    account_id: int = field(repr=False)
    name: str
    rating: int
    details: list[dict] = field(repr=False)


def validate_sgid(value: str, *, now: float | None = None) -> str:
    value = value.strip()
    match = SGID_PATTERN.fullmatch(value)
    if match is None:
        raise OfficialError('SGID 格式不正确，请提供官方公众号中的完整 SGWCMAID 文本。')
    try:
        created = datetime.strptime(match[1], '%y%m%d%H%M%S').replace(
            tzinfo=timezone(timedelta(hours=8)),
        ).timestamp()
    except ValueError:
        raise OfficialError('SGID 时间戳无效，请重新获取。') from None
    age = (time.time() if now is None else now) - created
    if not -60 <= age <= 180:
        raise OfficialError('SGID 超出本地三分钟安全窗口，或服务器时间不正确，请重新获取。')
    return value


def encode_packet(value: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    payload = zlib.compress(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
    pad = PKCS7(128).padder()
    padded = pad.update(payload) + pad.finalize()
    encrypt = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).encryptor()
    return encrypt.update(padded) + encrypt.finalize()


def decode_packet(value: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    decrypt = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).decryptor()
    padded = decrypt.update(value) + decrypt.finalize()
    unpad = PKCS7(128).unpadder()
    compressed = unpad.update(padded) + unpad.finalize()
    decoder = zlib.decompressobj()
    payload = decoder.decompress(compressed, MAX_RESPONSE + 1)
    if len(payload) > MAX_RESPONSE or not decoder.eof:
        raise ValueError('invalid or oversized payload')
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError('expected object')
    return result


class OfficialClient:
    def __init__(self, root: Path, config: dict):
        self.root = root
        self.config = config
        self.keychip_id = str(config.get('official_client_id', '')).strip() or DEFAULT_KEYCHIP_ID
        self.client_id = self.keychip_id.replace('-', '')[:11]
        self.place_id = int(config.get('official_place_id') or DEFAULT_PLACE_ID)
        self._lock = asyncio.Lock()

    async def check_available(self):
        if not self.config.get('official_sync_enabled', False):
            raise OfficialError('官方成绩同步尚未开启，请联系管理员配置「官方成绩同步」。')
        if not re.fullmatch(r'(?:[A-Za-z0-9]{11}|[A-Za-z0-9]{4}-[A-Za-z0-9]{11})', self.keychip_id):
            raise OfficialError('官方协议机台标识应为11位短标识或完整的 XXXX-XXXXXXXXXXX 格式。')
        from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401

        self._proxy_options()
        return await asyncio.to_thread(official_tls_context)

    def _proxy_options(self) -> dict:
        value = str(self.config.get('official_http_proxy') or '').strip()
        if not value:
            return {}
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != 'http' or not parsed.hostname
                    or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                    or parsed.port == 0 or any(ord(char) < 33 or ord(char) == 127 for char in value)):
                raise ValueError
        except ValueError:
            raise OfficialError('官方取数代理地址无效，请填写 http://主机:端口；不会回显认证信息。') from None
        return {'proxy': value}

    def http_session(self, tls: ssl.SSLContext, *, timeout: int = 30):
        return ClientSession(connector=TCPConnector(ssl=tls), timeout=ClientTimeout(total=timeout),
                             auto_decompress=False, skip_auto_headers={'Accept-Encoding'}, trust_env=False)

    async def login_server_order(self) -> list[int]:
        if self._proxy_options():
            return list(range(len(LOGIN_HOSTS)))
        loop = asyncio.get_running_loop()
        servers = []
        for index, host in enumerate(LOGIN_HOSTS):
            try:
                addresses = await asyncio.wait_for(
                    loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=3,
                )
            except (OSError, asyncio.TimeoutError):
                continue
            if addresses:
                servers.append(index)
        if not servers:
            raise OfficialError('两个官方登录服务器均无法解析，请检查运行 AstrBot 的系统或容器的 DNS 与网络。')
        return servers

    def _login_payload(self, sgid: str) -> dict:
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%y%m%d%H%M%S')
        signature = hashlib.sha256((self.keychip_id + stamp + _LOGIN_SALT).encode()).hexdigest().upper()
        return {'chipID': self.keychip_id, 'openGameID': 'MAID', 'key': signature,
                'qrCode': sgid[20:], 'timestamp': stamp, 'titlekey': 'SDGB'}

    async def _read_response(self, response, limit: int) -> bytes:
        data = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            data.extend(chunk)
            if len(data) > limit:
                raise OfficialError('官方响应过大，已停止同步。')
        return bytes(data)

    async def _resolve_session(self, http, sgid: str) -> OfficialSession:
        servers = await self.login_server_order()
        for index in servers:
            validate_sgid(sgid)
            try:
                async with http.post(
                    f'https://{LOGIN_HOSTS[index]}/wc_aime/api/get_data',
                    data=json.dumps(self._login_payload(sgid), separators=(',', ':')).encode(),
                    headers={'User-Agent': 'WC_AIME_LIB'}, allow_redirects=False,
                    skip_auto_headers={'Content-Type', 'Accept-Encoding'},
                    **self._proxy_options(),
                ) as response:
                    if response.status != 200:
                        raise OfficialError(f'官方 HTTPS 授权请求失败（HTTP {response.status}），未跟随跳转。')
                    raw = await self._read_response(response, 65536)
            except (ClientSSLError, ssl.SSLError):
                raise OfficialError('官方 HTTPS 证书或 TLS 校验失败，已停止；不会回退到 HTTP 或关闭证书校验。') from None
            except ClientHttpProxyError:
                raise OfficialError('官方取数代理拒绝连接，请检查 SSH 通道或代理设置。') from None
            except (ClientConnectionError, asyncio.TimeoutError):
                if index == servers[-1]:
                    raise OfficialError('官方 HTTPS 授权连接失败，请检查系统或容器网络后重试。') from None
                continue
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise OfficialError('官方 HTTPS 授权响应无法解析，未回显原始内容。') from None
            if (not isinstance(result, dict) or type(result.get('errorID')) is not int
                    or not 0 <= result['errorID'] <= 65535):
                raise OfficialError('官方 HTTPS 授权响应缺少有效的状态码。')
            if result['errorID'] != 0:
                raise OfficialLoginError(result['errorID'], index)
            user_id, token = result.get('userID'), result.get('token')
            if type(user_id) is not int or not 0 < user_id <= 0xffffffff or not isinstance(token, str):
                raise OfficialError('官方 HTTPS 授权响应缺少有效会话。')
            if not re.fullmatch(r'[A-Za-z0-9+/=_-]{1,512}', token):
                raise OfficialError('官方 HTTPS 授权响应缺少有效会话。')
            return OfficialSession(user_id, token)
        raise OfficialError('官方 HTTPS 授权未返回有效会话。')

    async def probe_login(self, http, index: int):
        async with http.post(f'https://{LOGIN_HOSTS[index]}/wc_aime/api/alive_check',
                             data=b'{}', headers={'Content-Type': 'application/json'},
                             allow_redirects=False, **self._proxy_options()) as response:
            if response.status != 200 or (await self._read_response(response, 4096)).strip() != b'alive':
                raise OfficialError(f'官方 HTTPS 线路 {index + 1} 探活失败。')

    async def _post(self, http, base: str, api: str, user_id: int, payload: dict) -> dict:
        route = hashlib.md5(f'{api}MaimaiChn8bF76dE9'.encode()).hexdigest()
        body = await asyncio.to_thread(encode_packet, payload)
        async with http.post(
            base + route, data=body, allow_redirects=False,
            headers={'Mai-Encoding': '1.55', 'User-Agent': f'{route}#{user_id or self.client_id}',
                     'Content-Type': 'application/json', 'charset': 'UTF-8',
                     'Content-Encoding': 'deflate', 'number': '0'},
            **self._proxy_options(),
        ) as response:
            if response.status != 200:
                raise OfficialError(f'官方服务器请求失败（HTTP {response.status}）。')
            data = await self._read_response(response, MAX_RESPONSE)
        try:
            result = await asyncio.to_thread(decode_packet, bytes(data))
        except Exception:
            raise OfficialError('官方响应无法解析，协议可能已更新。') from None
        if result.get('errorId') not in (None, 0) or result.get('returnCode') not in (None, 0, 1, 100):
            raise OfficialError('官方服务器拒绝了请求，请退出机台后使用新的 SGID 重试。')
        return result

    async def fetch(self, sgid: str) -> OfficialData:
        tls = await self.check_available()
        sgid = validate_sgid(sgid)
        async with self._lock:
            validate_sgid(sgid)
            async with self.http_session(tls) as http:
                session = await self._resolve_session(http, sgid)
                base = TITLE_URL
                login_time = 0
                attempted_login = False
                try:
                    setting = await self._post(http, base, 'GetGameSettingApi', 0,
                                               {'placeId': self.place_id, 'clientId': self.client_id})
                    suffix = (setting.get('gameSetting') or {}).get('movieServerUri') or 'mE2s3Jhd/'
                    base = urljoin(base, suffix)
                    if urlsplit(base).netloc != urlsplit(TITLE_URL).netloc or urlsplit(base).scheme != 'https':
                        raise OfficialError('官方响应中的服务器地址不合法，已停止同步。')
                    base = base.rstrip('/') + '/'
                    preview = await self._post(http, base, 'GetUserPreviewApi', session.user_id,
                                              {'userId': session.user_id, 'token': session.token,
                                               'segaIdAuthKey': '', 'clientId': self.client_id})
                    if preview.get('isLogin') is True:
                        raise OfficialError('官方账号当前正在游戏中，请退出机台后重新同步。')
                    now = int(time.time())
                    login_time = now + 8 * 3600
                    attempted_login = True
                    login = await self._post(http, base, 'UserLoginApi', session.user_id,
                                             {'userId': session.user_id, 'token': session.token,
                                              'clientId': self.client_id, 'placeId': self.place_id, 'regionId': 8,
                                              'accessCode': '', 'dateTime': now + 3600,
                                              'loginDateTime': login_time, 'isContinue': False, 'genericFlag': 0})
                    if type(login.get('returnCode')) is not int or login['returnCode'] not in (1, 100):
                        raise OfficialError('官方未确认登录成功，已停止读取成绩。')
                    player = await self._post(http, base, 'GetUserDataApi', session.user_id,
                                              {'userId': session.user_id})
                    details, seen, cursor = [], set(), 0
                    for _ in range(1000):
                        seen.add(cursor)
                        page = await self._post(http, base, 'GetUserMusicApi', session.user_id,
                                                {'userId': session.user_id, 'nextIndex': cursor, 'maxCount': 50})
                        if not isinstance(page.get('userMusicList'), list):
                            raise OfficialError('官方成绩响应缺少曲目列表，未覆盖本地数据。')
                        for song in page['userMusicList']:
                            for detail in song.get('userMusicDetailList', []):
                                details.append({**detail, 'musicId': detail.get('musicId', song.get('musicId'))})
                        cursor = int(page.get('nextIndex', 0))
                        if cursor == 0:
                            break
                        if cursor in seen:
                            raise OfficialError('官方成绩分页重复，未覆盖本地数据。')
                    else:
                        raise OfficialError('官方成绩分页过多，未覆盖本地数据。')
                    profile = player.get('userData') or {}
                    return OfficialData(session.user_id, str(profile.get('userName') or preview.get('userName') or ''),
                                        int(profile.get('playerRating') or preview.get('playerRating') or 0), details)
                finally:
                    if attempted_login:
                        try:
                            await asyncio.wait_for(self._post(
                                http, base, 'UserLogoutApi', session.user_id,
                                {'userId': session.user_id, 'clientId': self.client_id,
                                 'placeId': self.place_id, 'regionId': 8, 'accessCode': '',
                                 'loginDateTime': login_time, 'type': 5},
                            ), timeout=5)
                        except Exception:
                            from .. import log
                            log.warning('官方同步登出未确认，请暂勿同时登录机台；日志不包含会话凭据')
