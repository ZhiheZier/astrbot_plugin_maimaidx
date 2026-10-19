"""QQ location lookup and opt-in nearcade attendance reporting."""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlencode

import aiohttp
import astrbot.api.message_components as Comp
from pydantic import BaseModel, Field, ValidationError

from .maimaidx_arcade_games import ARCADE_GAMES, ArcadeGameType, DEFAULT_ARCADE_GAME, resolve_arcade_game
from .maimaidx_arcade_messages import ArcadeQueryGame, ArcadeQueryResult


API_BASE = 'https://nearca.de/api'
MAIMAI_DX_TITLE_ID = 1
RADIUS_OPTIONS = (1, 2, 5, 10, 20, 30)


class NearcadeError(Exception):
    pass


@dataclass(frozen=True)
class QQCoordinates:
    latitude: float
    longitude: float
    name: str = ''


def _coordinates(latitude, longitude, name='') -> QQCoordinates | None:
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
        return QQCoordinates(lat, lon, _plain(name, 80))
    return None


def extract_qq_location(event) -> QQCoordinates | None:
    for component in event.message_obj.message:
        if isinstance(component, Comp.Location):
            if point := _coordinates(component.lat, component.lon, component.title):
                return point
        elif isinstance(component, Comp.Json):
            payload = component.data
            if isinstance(payload, str):
                if len(payload) > 65536:
                    continue
                try:
                    payload = json.loads(payload)
                except (ValueError, RecursionError):
                    continue
            if not isinstance(payload, dict) or payload.get('app') not in ('com.tencent.map', 'com.tencent.maps'):
                continue
            meta = payload.get('meta')
            if not isinstance(meta, dict):
                continue
            for location in meta.values():
                if not isinstance(location, dict):
                    continue
                point = _coordinates(
                    location.get('lat', location.get('latitude')),
                    location.get('lng', location.get('lon', location.get('longitude'))),
                    location.get('name', location.get('title', '')),
                )
                if point is not None:
                    return point
    return None


class NearcadeGame(BaseModel):
    gameId: int = Field(ge=0)
    titleId: int
    name: str = ''
    version: str = ''
    quantity: int = Field(default=0, ge=0)
    totalAttendance: int | None = Field(default=None, ge=0)


class NearcadeAttendanceGame(NearcadeGame):
    gameId: int = Field(ge=0, strict=True)
    quantity: int = Field(ge=0, strict=True)
    total: int = Field(ge=0, strict=True)


@dataclass(frozen=True)
class NearcadeShopAttendance:
    name: str
    status: str
    games: tuple[NearcadeAttendanceGame, ...]
    reports: dict


def _report_time(value) -> str:
    if not isinstance(value, str):
        return ''
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OverflowError, OSError):
        return ''


def _game_family(game: NearcadeGame) -> ArcadeGameType:
    return next((family for family in ARCADE_GAMES if game.titleId in family.title_ids),
                ArcadeGameType(f'cloud-{game.titleId}', _plain(game.name) or f'游戏{game.titleId}',
                               (game.titleId,), ()))


class NearbyShop(BaseModel):
    id: int = Field(ge=0)
    name: str
    distance: float = Field(ge=0, allow_inf_nan=False)
    games: list[NearcadeGame]
    address: dict = Field(default_factory=dict)
    isOpen: bool | None = None
    isClosed: bool = False
    currentReportedAttendance: dict | None = None


class CatalogAddress(BaseModel):
    general: list[str] = Field(default_factory=list)
    detailed: str = ''


class CatalogShop(BaseModel):
    id: int = Field(ge=0, strict=True)
    name: str = Field(min_length=1)
    address: CatalogAddress = Field(default_factory=CatalogAddress)
    games: list[NearcadeGame]


def _native_bindings(shop_id, games):
    families = [_game_family(game) for game in games]
    bindings = []
    for game, family in zip(games, families):
        if families.count(family) > 1:
            label = f'{family.key}机台{game.gameId}号'
            family = ArcadeGameType(f'{family.key}:机台{game.gameId}号', label, family.title_ids, (label,))
        bindings.append((family, shop_id, game.gameId))
    return bindings


def _bounded_number(config, key: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lower, min(upper, value)) if math.isfinite(value) else default


def _plain(value, limit: int = 150) -> str:
    return ' '.join(str(value or '').split())[:limit]


class NearcadeClient:
    def __init__(self, config):
        self.config = config
        self._locks: dict[str, asyncio.Lock] = {}
        self._requests = asyncio.Semaphore(4)
        self._catalog = None

    @property
    def uses_cloud_catalog(self) -> bool:
        return self.config.get('arcade_data_source', 'local') == 'nearcade'

    @property
    def catalog(self):
        if self._catalog is None:
            from .. import arcades_json
            from .maimaidx_arcade_catalog import NearcadeArcadeList
            try:
                self._catalog = NearcadeArcadeList(arcades_json.with_name('nearcade_arcades.json'))
            except (OSError, ValueError):
                raise NearcadeError('nearcade 订阅文件无法读取，请联系管理员检查；未覆盖原文件') from None
        return self._catalog

    async def search_catalog(self, keyword, *, group_id=None):
        keyword = keyword.strip()
        if not keyword or len(keyword) > 150:
            raise NearcadeError('机厅关键词需为1至150个字符')
        scoped = [record for record in self.catalog
                  if group_id is not None and keyword in record.group_aliases.get(str(group_id), [])]
        if scoped and not keyword.isdigit():
            records = []
            for record in scoped:
                records.extend((await self.search_catalog(record.id))[0])
            return records, False
        if keyword.isascii() and keyword.isdigit():
            if len(keyword) > 12:
                raise NearcadeError('机厅 ID 格式不正确')
            data = await self._request('GET', f'/shops/{int(keyword)}')
            items, more = [data.get('shop')], False
        else:
            data = await self._request('GET', '/shops', params={'q': keyword, 'page': 1, 'limit': 20})
            items, more = data.get('shops'), data.get('hasNextPage') is True
        if not isinstance(items, list):
            raise NearcadeError('云端机厅列表格式异常')
        try:
            shops = [CatalogShop.model_validate(item) for item in items]
            if keyword.isascii() and keyword.isdigit() and shops[0].id != int(keyword):
                raise NearcadeError('云端返回的机厅 ID 不匹配')
        except ValidationError:
            raise NearcadeError('云端机厅资料格式异常') from None
        records = []
        for shop in shops:
            if len({game.gameId for game in shop.games}) != len(shop.games):
                raise NearcadeError('云端机厅游戏记录重复')
        for shop in shops:
            bindings = _native_bindings(shop.id, shop.games)
            quantities = {item.gameId: item.quantity for item in shop.games}
            records.append(self.catalog.remember(shop, [(game, quantities[game_id]) for game, _, game_id in bindings]))
        return records, more

    async def find_catalog(self, keyword, *, group_id=None):
        # Only explicit aliases identify an ID; cached search names may be incomplete or ambiguous.
        scoped = [record for record in self.catalog
                  if group_id is not None and keyword in record.group_aliases.get(str(group_id), [])]
        matches = scoped or [record for record in self.catalog if keyword in record.alias]
        if len(matches) == 1 and not keyword.isdigit():
            return (await self.search_catalog(matches[0].id))[0]
        if len(matches) > 1 and not keyword.isdigit():
            return matches
        records, more = await self.search_catalog(keyword)
        exact = [record for record in records if keyword == record.name or keyword == record.id]
        if exact and not more:
            return exact
        if more:
            raise NearcadeError('找到较多机厅，请缩小关键词，或先查找机厅后使用云端 ID')
        return records

    @property
    def location_enabled(self) -> bool:
        return bool(self.config.get('nearcade_location_enabled', False))

    @property
    def upload_enabled(self) -> bool:
        return bool(self.config.get('nearcade_upload_enabled', False))

    @property
    def radius(self) -> float:
        return _bounded_number(self.config, 'nearcade_radius_km', 5, 1, 30)

    @property
    def limit(self) -> int:
        return int(_bounded_number(self.config, 'nearcade_result_limit', 5, 1, 20))

    def lock_for(self, arcade_id: str) -> asyncio.Lock:
        return self._locks.setdefault(str(arcade_id), asyncio.Lock())

    async def _request(self, method: str, path: str, *, params=None, payload=None, authenticated=False):
        headers = {'Accept': 'application/json', 'Accept-Language': 'zh-CN'}
        if authenticated:
            token = str(self.config.get('nearcade_api_token', '') or '').strip()
            if not token:
                raise NearcadeError('尚未配置 nearcade API Token，请联系管理员')
            headers['Authorization'] = f'Bearer {token}'
        try:
            async with self._requests:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=True) as session:
                    async with session.request(method, API_BASE + path, params=params, json=payload,
                                               headers=headers, allow_redirects=False) as response:
                        if response.status != 200:
                            hints = {
                                400: '云端拒绝请求，请检查账号是否绑定手机号、机厅营业时间及上报参数',
                                401: 'nearcade API Token 无效或已过期，请联系管理员',
                                403: '无云端上报权限，请检查账号手机号绑定及已认领机厅的专用凭据',
                                404: '云端机厅或机台不存在，请检查店铺 ID 及游戏记录',
                                429: '云端请求过于频繁，请稍后再试',
                            }
                            raise NearcadeError(hints.get(response.status, f'nearcade 服务暂不可用（HTTP {response.status}）'))
                        data = await response.json()
                        if not isinstance(data, dict):
                            raise NearcadeError('nearcade 返回的数据格式异常')
                        return data
        except asyncio.TimeoutError:
            raise NearcadeError('nearcade 请求超时；上报结果请稍后核对') from None
        except (aiohttp.ClientError, ValueError):
            # Never echo network exceptions: they can include coordinates or credentials.
            raise NearcadeError('无法连接 nearcade 或返回数据异常，请稍后再试') from None

    async def nearby(self, point: QQCoordinates) -> list[NearbyShop]:
        radius = self.radius
        # The API snaps radii to fixed steps and can include distant metro results.
        # Over-fetch, then enforce the configured distance and result count locally.
        requested_radius = next(option for option in RADIUS_OPTIONS if option >= radius)
        data = await self._request('GET', '/discover', params={
            'latitude': point.latitude, 'longitude': point.longitude,
            'convertFrom': 'GCJ02', 'radius': requested_radius,
            'limit': 150, 'gameTitleIds': str(MAIMAI_DX_TITLE_ID),
            'fetchAttendance': 'true', 'includeTimeInfo': 'true',
        })
        if not isinstance(data.get('shops'), list):
            raise NearcadeError('nearcade 返回的机厅列表格式异常')
        shops = {}
        for item in data['shops']:
            try:
                shop = NearbyShop.model_validate(item)
            except ValidationError:
                continue
            if (shop.distance <= radius and not shop.isClosed
                    and any(game.titleId == MAIMAI_DX_TITLE_ID and game.quantity > 0 for game in shop.games)):
                shops[shop.id] = shop
        return sorted(shops.values(), key=lambda shop: (shop.distance, shop.id))[:self.limit]

    def _bindings(self, local_id: str):
        bindings = self.config.get('nearcade_shop_bindings', [])
        if not isinstance(bindings, list):
            raise NearcadeError('云端机厅映射配置格式错误，请联系管理员')
        result = []
        for entry in bindings:
            match = re.fullmatch(r'\s*(\d+)(?:\s*\[([^\]]+)\])?\s*=\s*(\d+)(?:\s*:\s*(\d+))?\s*', str(entry))
            if match and match[1] == str(local_id):
                game = resolve_arcade_game(match[2]) if match[2] else None
                if match[2] and game is None:
                    raise NearcadeError('映射名需以游戏名开头，如 maiA区、maiB区、chu')
                selected_game = int(match[4]) if match[4] is not None else None
                if game and game.is_named and selected_game is None:
                    raise NearcadeError(f'分区映射 {game.name} 必须指定 gameId')
                result.append((game, int(match[3]), selected_game))
        return result

    def games_for(self, local_id: str) -> list[ArcadeGameType]:
        if self.uses_cloud_catalog:
            matches = self.catalog.search_id(str(local_id))
            return matches[0].known_games() if matches else []
        return list({game.key: game for game, _, _ in self._bindings(local_id) if game}.values())

    async def _read_shop_attendance(self, shop_id: int) -> NearcadeShopAttendance | NearcadeError:
        try:
            async with asyncio.timeout(15):
                detail = await self._request('GET', f'/shops/{shop_id}')
                if not isinstance(detail, dict):
                    raise NearcadeError('云端机厅详情格式异常')
                shop = detail.get('shop')
                if not isinstance(shop, dict) or shop.get('id') != shop_id or not isinstance(shop.get('name'), str):
                    raise NearcadeError('云端机厅详情格式异常')
                data = await self._request('GET', f'/shops/{shop_id}/attendance')
                if (not isinstance(data, dict) or data.get('success') is not True or not isinstance(data.get('games'), list)
                        or not isinstance(data.get('reported'), list) or not isinstance(data.get('registered'), list)):
                    raise NearcadeError('云端人数数据格式异常')
                games = tuple(NearcadeAttendanceGame.model_validate(item) for item in data['games'])
                if len({game.gameId for game in games}) != len(games):
                    raise NearcadeError('云端游戏记录重复')
                reports = {}
                for report in data['reported']:
                    if (not isinstance(report, dict) or type(report.get('gameId')) is not int
                            or type(report.get('currentAttendances')) is not int or report['currentAttendances'] < 0):
                        continue
                    reporter = report.get('reporter')
                    reporter = reporter if isinstance(reporter, dict) else {}
                    reports[report['gameId']] = (
                        _plain(reporter.get('displayName') or reporter.get('name')) or 'Nearcade',
                        _report_time(report.get('reportedAt')),
                    )
                status = '闭店' if shop.get('isClosed') is True or shop.get('isOpen') is False else '营业'
                return NearcadeShopAttendance(_plain(shop['name']), status, games, reports)
        except (asyncio.TimeoutError, ValidationError):
            return NearcadeError('云端查询超时或人数数据格式异常')
        except NearcadeError as exc:
            return exc
        except Exception:
            return NearcadeError('无法读取云端机厅信息，请稍后再试')

    def _query_bindings(self, local_id: str):
        bindings = self._bindings(local_id)
        keys, targets = set(), set()
        for game, shop_id, game_id in bindings:
            key = game.key if game else None
            if key in keys:
                raise NearcadeError('本地机厅存在重复云端映射')
            keys.add(key)
            if game_id is not None:
                target = (shop_id, game_id)
                if target in targets:
                    raise NearcadeError('多个映射指向同一云端游戏记录')
                targets.add(target)
        return bindings

    def _query_result(self, bindings, snapshots) -> ArcadeQueryResult:
        groups = {}
        overridden = {game.family_key for game, _, _ in bindings if game}
        for selected, shop_id, game_id in bindings:
            snapshot = snapshots[shop_id]
            if isinstance(snapshot, NearcadeError):
                raise snapshot
            if selected is None and game_id is not None and not any(
                    item.gameId == game_id and item.titleId == MAIMAI_DX_TITLE_ID for item in snapshot.games):
                raise NearcadeError('云端未找到映射的舞萌记录')
            matched = []
            for item in snapshot.games:
                family = _game_family(item)
                if selected:
                    if item.titleId not in selected.title_ids or (game_id is not None and item.gameId != game_id):
                        continue
                    family = selected
                elif family.family_key in overridden:
                    continue
                elif family.key == 'mai' and game_id is not None and item.gameId != game_id:
                    continue
                matched.append(item)
                groups.setdefault(family, []).append((item, snapshot))
            if selected and not matched:
                raise NearcadeError(f'云端未找到映射的{selected.name}记录')
        rows = []
        observed_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for family, items in groups.items():
            count = sum(item.total for item, _ in items)
            known = count > 0 or any(item.gameId in snapshot.reports for item, snapshot in items)
            reports = [snapshot.reports[item.gameId] for item, snapshot in items if item.gameId in snapshot.reports]
            if len(items) == 1 and reports:
                by, stamp = reports[0]
            elif reports:
                by, stamp = 'Nearcade（多记录汇总）', max(stamp for _, stamp in reports)
            else:
                by, stamp = ('Nearcade（登记数据）', observed_at) if known else ('', '')
            status = '闭店' if all(snapshot.status == '闭店' for _, snapshot in items) else '营业'
            rows.append(ArcadeQueryGame(family, count if known else None, sum(item.quantity for item, _ in items),
                                       by, stamp, status))
        rows.sort(key=lambda row: (row.game.family_key != 'mai', row.game.family_key, row.game.key))
        ids = {shop_id for _, shop_id, _ in bindings}
        name = snapshots[next(iter(ids))].name if len(ids) == 1 else ''
        return ArcadeQueryResult(name, tuple(rows), source='nearcade')

    async def read_attendance(self, local_ids) -> dict[str, ArcadeQueryResult | NearcadeError]:
        """Read each mapped cloud shop once per query, never sending a token or writing counts."""
        plans, results = {}, {}
        for local_id in dict.fromkeys(str(value) for value in local_ids):
            try:
                if self.uses_cloud_catalog:
                    if not local_id.isascii() or not local_id.isdigit() or len(local_id) > 12:
                        raise NearcadeError('云端机厅 ID 格式不正确')
                    bindings = [(None, int(local_id), None)]
                else:
                    bindings = self._query_bindings(local_id)
                if bindings:
                    plans[local_id] = bindings
            except NearcadeError as exc:
                results[local_id] = exc
        shop_ids = sorted({shop_id for bindings in plans.values() for _, shop_id, _ in bindings})
        snapshots = dict(zip(shop_ids, await asyncio.gather(*(self._read_shop_attendance(shop_id) for shop_id in shop_ids))))
        for local_id, bindings in plans.items():
            try:
                if self.uses_cloud_catalog:
                    snapshot = snapshots[int(local_id)]
                    if isinstance(snapshot, NearcadeError):
                        raise snapshot
                    bindings = _native_bindings(int(local_id), snapshot.games)
                    if not bindings:
                        results[local_id] = ArcadeQueryResult(snapshot.name, (), source='nearcade')
                        continue
                results[local_id] = self._query_result(bindings, snapshots)
            except NearcadeError as exc:
                results[local_id] = exc
        return results

    def _binding(self, local_id: str, game: ArcadeGameType = DEFAULT_ARCADE_GAME) -> tuple[int, int | None]:
        if self.uses_cloud_catalog:
            if not str(local_id).isascii() or not str(local_id).isdigit() or len(str(local_id)) > 12:
                raise NearcadeError('云端机厅 ID 格式不正确')
            if game.is_named:
                match = re.fullmatch(r'机台([0-9]+)号', game.key.partition(':')[2])
                if not match:
                    raise NearcadeError('请使用查找机厅显示的游戏分区名')
                return int(local_id), int(match[1])
            return int(local_id), None
        bindings = self._bindings(local_id)
        if not game.is_named and any(selected and selected.is_named and selected.family_key == game.key
                                     for selected, _, _ in bindings):
            raise NearcadeError('该游戏配置了分区，请使用具体映射名报人数')
        general, specific = [], []
        for selected, shop_id, selected_game in bindings:
            if selected is None and not game.is_named:
                # Legacy explicit game IDs belong to maimai DX, not other titles.
                general.append((shop_id, selected_game if game == DEFAULT_ARCADE_GAME else None))
            elif selected and selected.key == game.key:
                specific.append((shop_id, selected_game))
        matches = specific or general
        if not matches:
            raise NearcadeError(f'本地机厅 ID {local_id} 尚未配置云端机厅映射，请联系管理员')
        if len(matches) != 1:
            raise NearcadeError(f'本地机厅 ID {local_id} 存在重复云端映射，请联系管理员')
        if game.is_named:
            # Two labels for one cloud counter would silently overwrite each other.
            if sum((shop_id, game_id) == matches[0] for _, shop_id, game_id in bindings) != 1:
                raise NearcadeError('多个映射名指向同一云端游戏记录，请联系管理员修正')
        return matches[0]

    async def upload_attendance(self, local_id: str, count: int,
                                game_type: ArcadeGameType = DEFAULT_ARCADE_GAME) -> str:
        if not self.upload_enabled:
            return ''
        if not str(self.config.get('nearcade_api_token', '') or '').strip():
            raise NearcadeError('尚未配置 nearcade API Token，请联系管理员')
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise NearcadeError('只能上传非负整数人数')
        shop_id, selected_game = self._binding(local_id, game_type)
        data = await self._request('GET', f'/shops/{shop_id}')
        shop = data.get('shop')
        if not isinstance(shop, dict) or shop.get('id') != shop_id or not isinstance(shop.get('games'), list):
            raise NearcadeError('云端机厅详情格式异常，请检查映射')
        games = []
        for item in shop['games']:
            try:
                game = NearcadeGame.model_validate(item)
            except ValidationError:
                continue
            if game.titleId in game_type.title_ids and game.quantity > 0:
                if selected_game is None or game.gameId == selected_game:
                    games.append(game)
        if not games:
            if self.uses_cloud_catalog:
                raise NearcadeError(f'云端机厅中未找到对应{game_type.name}记录，请查找机厅确认当前资料')
            raise NearcadeError(f'映射的云端机厅中未找到对应{game_type.name}记录，请检查映射')
        if len(games) != 1:
            if self.uses_cloud_catalog:
                labels = '、'.join(f'{game_type.family_key}机台{item.gameId}号' for item in games)
                raise NearcadeError(f'该游戏有多条记录，请指定分区：{labels}')
            raise NearcadeError(f'云端机厅有多条{game_type.name}记录，请在对应游戏的映射中指定 gameId')
        if not self.upload_enabled:
            return ''
        result = await self._request('POST', f'/shops/{shop_id}/attendance', authenticated=True, payload={
            'games': [{'id': games[0].gameId, 'currentAttendances': count}],
        })
        if result.get('success') is not True:
            raise NearcadeError('云端未确认上报成功，请稍后核对')
        return f'已同步 nearcade：{game_type.name} {count} 人'


def format_nearby_shops(shops: list[NearbyShop], radius: float, point: QQCoordinates) -> str:
    blocks = []
    for shop in shops:
        distance = f'{round(shop.distance * 1000)}米' if shop.distance < 1 else f'{round(shop.distance, 2):g}公里'
        address = _plain(shop.address.get('detailed', '')) or '暂无地址'
        blocks.append(f'🎮 {_plain(shop.name)}（{distance}）\n📍 {address}')
    if not shops:
        blocks.append(f'附近 {radius:g} 公里内未找到有舞萌 DX 的机厅。')
    params = {
        'latitude': point.latitude, 'longitude': point.longitude,
        'radius': f'{radius:g}', 'convertFrom': 'GCJ02', 'gameTitleIds': MAIMAI_DX_TITLE_ID,
    }
    if point.name:
        params['name'] = _plain(point.name, 80)
    url = 'https://nearca.de/discover?' + urlencode(params, quote_via=quote)
    blocks.append(f'👉 更多详情请点开：{url}')
    return '\n\n'.join(blocks)
