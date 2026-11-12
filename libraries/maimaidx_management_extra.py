"""Scoped maintenance operations; credentials and arbitrary filesystem paths stay private."""

import asyncio
import copy
import hashlib
import json
import math
import re
import time
from datetime import datetime

from .maimaidx_management_store import ManagementError, check_revision, identifier, read_json, revision, text, write_json
from .maimaidx_nearcade import CatalogShop, NearcadeClient
from .maimaidx_user import ServiceName, Theme, User, userstore


KINDS = {'mappings', 'players', 'rankings', 'pc', 'notices', 'cache', 'limits', 'tunnel'}


async def publish(writer, value, callback):
    task = asyncio.create_task(asyncio.to_thread(writer, value))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        callback()
        raise
    callback()


def confirmed(body, expected):
    if body.get('confirm') != expected:
        raise ManagementError(f'请输入「{expected}」确认此操作。')


class ExtraManagement:
    def __init__(self, plugin):
        self.plugin = plugin
        self._config_lock = asyncio.Lock()
        self._notice_lock = asyncio.Lock()
        self._task = None
        self.job = {'phase': 'idle', 'message': '暂无维护任务'}

    async def shutdown(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def listing(self, query):
        from .maimaidx_management import page

        kind = query.get('kind')
        if kind not in KINDS:
            raise ManagementError('请选择有效的管理模块。')
        keyword = '' if kind in ('cache', 'tunnel') else text(query.get('q', ''), '搜索词', 150, empty=True).casefold()
        rows, meta = await getattr(self, f'list_{kind}')(query)
        if keyword:
            keys = {'mappings': ('entry', 'local', 'alias', 'shop', 'game'), 'players': ('id',),
                    'rankings': ('name', 'qq', 'scope'), 'pc': ('id', 'name'), 'limits': ('id',),
                    'notices': ('version', 'notes')}[kind]
            rows = [row for row in rows if any(keyword in str(row.get(key, '')).casefold() for key in keys)]
        return {**page(rows, query), 'meta': meta, 'job': dict(self.job)}

    async def change(self, body):
        kind = body.get('kind')
        if kind not in KINDS:
            raise ManagementError('请选择有效的管理模块。')
        return await getattr(self, f'change_{kind}')(body)

    async def list_tunnel(self, query):
        from .maimaidx_tunnel import environment
        return [], await asyncio.to_thread(environment)

    async def change_tunnel(self, body):
        from .maimaidx_tunnel import generate_script
        if body.get('action') != 'generate':
            raise ManagementError('请选择生成连接脚本。')
        return await asyncio.to_thread(generate_script, body)

    async def detail(self, query):
        if query.get('kind') == 'pc':
            _, snapshot, _ = await asyncio.to_thread(self.snapshot, query.get('id'))
            from .maimaidx_management import page
            from .maimaidx_music import mai

            rows = []
            for score in snapshot.scores:
                song = mai.get_song(score.song_id)
                rows.append({'id': str(score.song_id), 'name': song.song_name if song else '未知曲目',
                             'difficulty': score.level_index, 'achievements': score.achievements,
                             'dx_score': score.dx_score, 'pc': score.play_count})
            keyword = text(query.get('q', ''), '搜索词', 150, empty=True).casefold()
            rows = [row for row in rows if keyword in f"{row['id']} {row['name']}".casefold()]
            return {**page(rows, query), 'name': snapshot.name}
        if query.get('kind') == 'mappings':
            shop_id = identifier(query.get('shop'), '云端店铺ID')
            if len(shop_id) > 12:
                raise ManagementError('云端店铺ID过长。')
            response = await self.plugin.nearcade._request('GET', f'/shops/{shop_id}')
            try:
                shop = CatalogShop.model_validate(response.get('shop'))
            except ValueError:
                raise ManagementError('云端店铺资料格式异常。') from None
            if shop.id != int(shop_id):
                raise ManagementError('云端返回的店铺ID不匹配。')
            return {'name': shop.name, 'items': [{'id': str(game.gameId), 'name': game.name,
                    'version': game.version, 'quantity': game.quantity} for game in shop.games]}
        raise ManagementError('该模块不支持详情查询。')

    def mapping_entries(self, source):
        if source not in ('local', 'nearcade'):
            raise ManagementError('请选择本地或 nearcade 模式。')
        key = 'nearcade_shop_bindings' if source == 'local' else 'nearcade_game_aliases'
        entries = self.plugin.config.get(key, [])
        if not isinstance(entries, list) or any(not isinstance(value, str) for value in entries):
            raise ManagementError('映射配置格式异常，请先在插件配置中修正。')
        return key, list(entries)

    async def list_mappings(self, query):
        source = query.get('source', 'local')
        _, entries = self.mapping_entries(source)
        rows = []
        for entry in entries:
            pattern = (r'\s*(\d+)(?:\s*\[([^\]]+)\])?\s*=\s*(\d+)(?:\s*:\s*(\d+))?\s*'
                       if source == 'local' else r'\s*(\d+)\[([^\]]+)\]\s*=\s*(\d+)\s*')
            match = re.fullmatch(pattern, entry)
            fields = {'local': '', 'alias': '', 'shop': '', 'game': ''}
            if match:
                fields.update(local=match[1], alias=match[2] or '', shop=match[3] if source == 'local' else match[1],
                              game=(match[4] or '') if source == 'local' else match[3])
            rows.append({'id': revision(entry), 'entry': entry, **fields, 'valid': bool(match)})
        return rows, {'source': source, 'revision': revision(entries)}

    def save_config(self, group, changes):
        raw = getattr(self.plugin, '_raw_plugin_config', None)
        save = getattr(raw, 'save_config', None)
        if not callable(save):
            raise ManagementError('当前环境无法保存 AstrBot 插件配置，未修改。')
        old = copy.deepcopy(dict(raw))
        node = copy.deepcopy(raw.get(group, {}))
        if not isinstance(node, dict):
            raise ManagementError('插件配置分组格式错误。')
        node.update(changes)
        raw[group] = node
        try:
            if save() is False:
                raise OSError('config save rejected')
        except Exception:
            raw.clear(); raw.update(old)
            raise ManagementError('插件配置保存失败，运行中的设置未修改。') from None
        self.plugin.config.update(changes)
        self.plugin.nearcade.config.update(changes)

    async def change_mappings(self, body):
        async with self._config_lock:
            source = body.get('source')
            key, entries = self.mapping_entries(source)
            check_revision(body.get('revision'), revision(entries))
            original = body.get('id')
            index = next((n for n, value in enumerate(entries) if revision(value) == original), None)
            if body.get('action') == 'delete':
                confirmed(body, '删除映射')
                if index is None:
                    raise ManagementError('映射不存在，请刷新。')
                entries.pop(index)
            elif body.get('action') == 'save':
                local_id = identifier(body.get('local'), '机厅ID')
                shop = identifier(body.get('shop') if source == 'local' else local_id, '云端店铺ID')
                game_id = identifier(body['game'], 'gameId') if body.get('game') else ''
                label = text(body.get('alias', ''), '机台别名', 40, empty=source == 'local')
                entry = f'{local_id}' + (f'[{label}]' if label else '') + f'={shop}' + (f':{game_id}' if game_id else '')
                if source == 'nearcade':
                    if not game_id:
                        raise ManagementError('nearcade 机台别名必须填写 gameId。')
                    entry = f'{local_id}[{label}]={game_id}'
                if original and index is None:
                    raise ManagementError('原映射已变化，请刷新。')
                if index is None:
                    entries.append(entry)
                else:
                    entries[index] = entry
                if len(entries) > 500 or len(set(entries)) != len(entries):
                    raise ManagementError('映射重复或超过500条。')
                check = NearcadeClient({**self.plugin.config, 'arcade_data_source': source, key: entries})
                for value in entries:
                    match = re.fullmatch(r'\s*(\d+)(?:\s*\[[^\]]+\])?\s*=\s*\d+(?:\s*:\s*\d+)?\s*', value)
                    if not match:
                        raise ManagementError('现有映射包含无效格式，请先修正或删除。')
                    check._bindings(match[1])
            else:
                raise ManagementError('不支持的映射操作。')
            self.save_config('arcade', {key: entries})
            self.plugin.nearcade._cabinet_aliases.clear()
        return {'message': '映射已保存并生效；未上报云端人数。'}

    @staticmethod
    def checked_users():
        data = read_json(userstore.path, {})
        if not isinstance(data, dict):
            raise ManagementError('玩家文件格式错误，未覆盖原文件。')
        try:
            parsed = {identifier(str(qq), 'QQ号'): User.model_validate({**info, 'qqid': str(qq)}) for qq, info in data.items()}
        except (TypeError, ValueError):
            raise ManagementError('玩家文件包含无效记录，未覆盖原文件。') from None
        if parsed != userstore._data:
            raise ManagementError('玩家文件与内存不一致，请重载插件后再编辑。')

    @staticmethod
    def player_row(qq, official_id=None):
        from .maimaidx_api_data import maiApi

        user = userstore.get(qq)
        cached = False
        if maiApi.divingfish_oauth_configured:
            subject = maiApi._divingfish_subject(qqid=qq)
            cached = any((key == subject or key.startswith(subject + '|')) and value[1] > time.monotonic()
                         for key, value in maiApi._oauth_tokens.items())
        return {'id': qq, 'service': user.service.value, 'theme': user.theme.value,
                'official_user_id': official_id, 'official_revision': revision(official_id),
                'lxns_bound': bool(user.access_token or user.refresh_token),
                'df_cached': cached, 'df_configured': maiApi.divingfish_oauth_configured,
                'revision': revision(user.model_dump(mode='json'))}

    async def binding_records(self):
        from .maimaidx_official import OfficialError

        try:
            return await self.plugin.score_sync.bindings.records()
        except OfficialError as exc:
            raise ManagementError(str(exc)) from None

    async def player_record(self, qq):
        return self.player_row(qq, (await self.binding_records()).get(qq))

    async def list_players(self, query):
        bindings = await self.binding_records()
        async with userstore._lock:
            self.checked_users()
            ids = set(userstore._data) | set(bindings)
            value = query.get('q', '').strip()
            if value.isascii() and value.isdigit() and 0 < int(value) < 10 ** 20:
                ids.add(str(int(value)))
            return [self.player_row(qq, bindings.get(qq)) for qq in sorted(ids)], {
                'services': [{'value': item.value, 'label': item.label} for item in ServiceName],
                'themes': [{'value': item.value, 'label': item.value} for item in Theme]}

    async def change_players(self, body):
        if body.get('action') == 'unbind_official':
            from .maimaidx_official import OfficialError

            confirmed(body, '解绑游戏账号')
            qq = identifier(body.get('id'), 'QQ号')
            store = self.plugin.score_sync.bindings
            lock = store.account_lock(qq)
            if lock.locked():
                raise ManagementError('该玩家正在同步或重传成绩，请结束后再解绑。')
            async with lock:
                current = (await self.binding_records()).get(qq)
                check_revision(body.get('official_revision'), revision(current))
                try:
                    await store.unbind(qq, current)
                except OfficialError as exc:
                    raise ManagementError(str(exc)) from None
            return {'message': '游戏账号已解绑；查分器授权与本地快照保留，旧快照不能直接重传。', 'item': await self.player_record(qq)}
        if body.get('action') == 'unlink_lxns':
            from .maimaidx_lxns import LxnsAPI

            qq = identifier(body.get('id'), 'QQ号')
            lock = LxnsAPI._refresh_locks.setdefault(qq, asyncio.Lock())
            async with lock:
                return await self._change_players(body)
        return await self._change_players(body)

    async def _change_players(self, body):
        from .maimaidx_api_data import maiApi

        qq = identifier(body.get('id'), 'QQ号')
        action = body.get('action')
        if action == 'check_df':
            try:
                await maiApi._divingfish_access_token(maiApi._divingfish_subject(qqid=qq))
            except Exception:
                raise ManagementError('未能确认水鱼授权，请检查绑定、应用配置或网络。') from None
            return {'message': '水鱼授权检测通过。', 'item': await self.player_record(qq)}
        async with userstore._lock:
            self.checked_users()
            row = self.player_row(qq)
            check_revision(body.get('revision'), row['revision'])
            user = userstore.get(qq).model_copy(deep=True)
            if action == 'save':
                try:
                    user.service = ServiceName(body.get('service'))
                    user.theme = Theme(body.get('theme'))
                except ValueError:
                    raise ManagementError('数据源或主题无效。') from None
            elif action == 'unlink_lxns':
                confirmed(body, '清除本地授权')
                user.friend_code = None; user.access_token = None; user.refresh_token = None
            elif action == 'clear_df_cache':
                confirmed(body, '清除短期缓存')
                if maiApi.divingfish_oauth_configured:
                    maiApi._clear_divingfish_tokens(maiApi._divingfish_subject(qqid=qq))
                return {'message': '水鱼短期缓存已清除，服务端绑定未解除。', 'item': await self.player_record(qq)}
            else:
                raise ManagementError('不支持的玩家操作。')
            updated = {**userstore._data, qq: user}
            dump = {key: value.model_dump(exclude={'qqid'}, mode='json') for key, value in updated.items()}
            await publish(lambda value: write_json(userstore.path, value), dump, lambda: setattr(userstore, '_data', updated))
        return {'message': '玩家设置已保存。' if action == 'save' else '落雪本地授权已清除；服务端授权未撤销。',
                'item': await self.player_record(qq)}

    async def list_rankings(self, query):
        store = self.plugin.song_ranking_store
        async with store._lock:
            await store._load()
            return [{'id': f'{scope}|{qq}', 'scope': scope, 'qq': qq, 'name': name,
                     'revision': revision(name)} for scope, members in sorted(store._groups.items())
                    for qq, name in members.items()], {}

    async def change_rankings(self, body):
        confirmed(body, '移出排行榜')
        if body.get('action') != 'remove':
            raise ManagementError('只允许移除自愿参与者，不允许代替用户加入。')
        scope = text(body.get('scope'), '排行榜分组', 60)
        qq = identifier(body.get('qq'), 'QQ号')
        if not re.fullmatch(r'\d{1,20}:\d{1,20}', scope):
            raise ManagementError('排行榜分组格式错误。')
        store = self.plugin.song_ranking_store
        async with store._lock:
            await store._load()
            if qq not in store._groups.get(scope, {}):
                raise ManagementError('成员已退出，请刷新。')
            check_revision(body.get('revision'), revision(store._groups[scope][qq]))
            updated = copy.deepcopy(store._groups)
            updated[scope].pop(qq)
            if not updated[scope]:
                updated.pop(scope)
            await publish(store._write, updated, lambda: setattr(store, '_groups', updated))
        return {'message': '已移出该群排行榜，未删除查分器绑定或其他群记录。'}

    def snapshot(self, key):
        from .maimaidx_sync import PCSnapshot

        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ManagementError('快照编号无效。')
        root = self.plugin.score_sync.store.directory
        path = root / f'{key}.json'
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ManagementError('快照路径异常。')
        if not path.is_file():
            raise ManagementError('快照不存在，请刷新。')
        raw = read_json(path, None)
        try:
            data = PCSnapshot.model_validate(raw)
        except ValueError:
            raise ManagementError('快照已损坏，不能显示或通过页面删除，请管理员检查原文件。') from None
        return path, data, revision(raw)

    def sync_active(self, key):
        return any(hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest() == key and lock.locked()
                   for identity, lock in self.plugin.score_sync._locks.items())

    async def list_pc(self, query):
        root = self.plugin.score_sync.store.directory
        rows = []
        for path in sorted(root.glob('*.json')):
            if not re.fullmatch(r'[0-9a-f]{64}', path.stem):
                continue
            try:
                _, snapshot, stamp = await asyncio.to_thread(self.snapshot, path.stem)
                rows.append({'id': path.stem, 'name': snapshot.name, 'updated_at': snapshot.updated_at,
                             'count': len(snapshot.scores), 'revision': stamp, 'valid': True})
            except ManagementError:
                rows.append({'id': path.stem, 'name': '损坏或不可读取的快照', 'updated_at': 0, 'count': 0, 'valid': False})
        return rows, {}

    async def change_pc(self, body):
        if body.get('action') != 'delete':
            raise ManagementError('快照只允许查看和删除，不允许修改成绩。')
        confirmed(body, '删除快照')
        key = body.get('id')
        if self.sync_active(key):
            raise ManagementError('该用户正在同步或重传成绩，请稍后再试。')
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ManagementError('快照编号无效。')
        store = self.plugin.score_sync.store
        async with store.path_lock(store.directory / f'{key}.json'):
            path, _, stamp = await asyncio.to_thread(self.snapshot, key)
            check_revision(body.get('revision'), stamp)
            if self.sync_active(key):
                raise ManagementError('该用户正在同步或重传成绩，请稍后再试。')
            path.unlink()
        return {'message': '本地快照已删除；查分器成绩、绑定和同步额度未修改。'}

    async def list_limits(self, query):
        from .maimaidx_sync_limits import DEFAULTS, SHANGHAI

        store = self.plugin.score_sync.limits
        async with store._lock:
            if store._state is None:
                store._state = await asyncio.to_thread(store._load)
            day = datetime.now(SHANGHAI).date().isoformat()
            rows = []
            for key, records in store._state.users.items():
                for action, usage in records.items():
                    cooldown, limit = store._settings(action)
                    rows.append({'id': key, 'action': action, 'day': usage.day,
                                 'count': usage.count if usage.day == day else 0, 'daily_limit': limit,
                                 'remaining': max(0, math.ceil(usage.last_at + cooldown - time.time())),
                                 'revision': revision(usage.model_dump())})
            settings = {key: self.plugin.config.get(key, value) for key, value in DEFAULTS.items()}
            return rows, {'settings': settings, 'revision': revision(settings)}

    async def change_limits(self, body):
        if body.get('action') == 'settings':
            from .maimaidx_sync_limits import DEFAULTS
            values = body.get('settings')
            if not isinstance(values, dict) or set(values) != set(DEFAULTS) or any(
                type(value) is not int or not 0 <= value <= 1000000 for value in values.values()
            ):
                raise ManagementError('限流设置必须为0至1000000的整数；0关闭对应限制。')
            async with self._config_lock:
                current = {key: self.plugin.config.get(key, value) for key, value in DEFAULTS.items()}
                check_revision(body.get('revision'), revision(current))
                self.save_config('official_sync', values)
            return {'message': '同步额度配置已保存；现有使用记录未清空。'}
        confirmed(body, '重置额度')
        key, action = body.get('id'), body.get('action')
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key) or action not in ('fetch', 'upload'):
            raise ManagementError('额度记录参数错误。')
        if self.sync_active(key):
            raise ManagementError('该用户正在同步，请稍后重置。')
        store = self.plugin.score_sync.limits
        async with store._lock:
            if store._state is None:
                store._state = await asyncio.to_thread(store._load)
            if self.sync_active(key):
                raise ManagementError('该用户正在同步，请稍后重置。')
            usage = store._state.users.get(key, {}).get(action)
            if usage is None:
                raise ManagementError('额度记录不存在，请刷新。')
            check_revision(body.get('revision'), revision(usage.model_dump()))
            updated = store._state.model_copy(deep=True)
            updated.users[key].pop(action)
            if not updated.users[key]:
                updated.users.pop(key)
            await publish(store._write, updated, lambda: setattr(store, '_state', updated))
        return {'message': '所选操作的冷却和每日次数已重置；其他记录未修改。'}

    @staticmethod
    def cache_paths():
        from .. import music_file, chart_file, alias_file, lxns_music_file, lxns_alias_file, merge_music_file, merge_alias_file
        return {'music': music_file, 'charts': chart_file, 'aliases': alias_file, 'lxns_music': lxns_music_file,
                'lxns_aliases': lxns_alias_file, 'merged_music': merge_music_file, 'merged_aliases': merge_alias_file}

    async def list_cache(self, query):
        rows = []
        for key, path in self.cache_paths().items():
            stat = path.stat() if path.exists() and not path.is_symlink() else None
            rows.append({'id': key, 'name': path.name, 'bytes': stat.st_size if stat else 0,
                         'updated_at': int(stat.st_mtime) if stat else 0, 'exists': stat is not None,
                         'clearable': key.startswith('merged_'),
                         'revision': revision([stat.st_size, stat.st_mtime_ns]) if stat else revision(None)})
        return rows, {}

    async def change_cache(self, body):
        from .maimaidx_music import mai

        if self._task is not None and not self._task.done():
            raise ManagementError('已有维护任务正在运行。')
        action = body.get('action')
        if action in ('refresh_music', 'refresh_aliases'):
            async def work():
                self.job = {'phase': 'running', 'message': '正在刷新曲库和别名' if action == 'refresh_music' else '正在刷新别名'}
                try:
                    await (mai.update() if action == 'refresh_music' else mai.get_music_alias())
                    self.job = {'phase': 'completed', 'message': '刷新完成。'}
                except asyncio.CancelledError:
                    self.job = {'phase': 'cancelled', 'message': '维护任务已停止。'}
                    raise
                except Exception:
                    self.job = {'phase': 'failed', 'message': '刷新失败，请检查网络与缓存文件。'}
            self.job = {'phase': 'running', 'message': '维护任务已创建。'}
            self._task = asyncio.create_task(work())
            return {'message': '后台维护任务已启动。', 'job': dict(self.job)}
        if action == 'clear_help':
            confirmed(body, '清理帮助缓存')
            from .maimaidx_help import _cached_help_image, _help_lock
            async with _help_lock:
                _cached_help_image.cache_clear()
            return {'message': '帮助图内存缓存已清理，下次查询时重新生成。'}
        path = self.cache_paths().get(body.get('id'))
        if path is None or path.is_symlink():
            raise ManagementError('缓存记录无效。')
        rows, _ = await self.list_cache({})
        row = next(item for item in rows if item['id'] == body['id'])
        check_revision(body.get('revision'), row['revision'])
        if action == 'validate':
            data = await asyncio.to_thread(read_json, path, None)
            if not isinstance(data, (list, dict)):
                raise ManagementError('缓存不存在或不是有效的列表/对象。')
            return {'message': 'JSON 结构校验通过；这不代表上游数据字段一定完整。'}
        if action == 'clear' and row['clearable']:
            confirmed(body, '清理派生缓存')
            path.unlink(missing_ok=True)
            return {'message': '派生缓存文件已清理；内存曲库、本地别名、原始离线缓存和图片素材未修改。'}
        raise ManagementError('只允许清理派生缓存；原始缓存和素材不通过本页删除。')

    def platforms(self):
        return [item for item in self.plugin.context.platform_manager.platform_insts if item.meta().name == 'aiocqhttp']

    async def list_notices(self, query):
        from .maimaidx_update_notice import changelog_versions, release_notes, update_message

        notifier = self.plugin.update_notifier
        state = copy.deepcopy(notifier.state) if notifier.state is not None else read_json(notifier.path, {})
        if not isinstance(state, dict):
            raise ManagementError('更新通知记录格式异常。')
        content = await asyncio.to_thread(notifier.changelog.read_text, encoding='utf-8')
        preview = update_message(content, None, notifier.version)
        pending = state.get('pending') or {}
        if not isinstance(pending, dict):
            raise ManagementError('更新通知进度格式异常。')
        if (not isinstance(pending.get('delivered', []), list)
                or any(not isinstance(value, str) for value in pending.get('delivered', []))
                or type(pending.get('attempts', 0)) is not int):
            raise ManagementError('更新通知进度内容异常。')
        row = {'id': 'current', 'current': notifier.version, 'recorded': state.get('version', '尚未记录'),
               'attempts': pending.get('attempts', 0), 'delivered': pending.get('delivered', []),
               'pending': bool(pending), 'revision': revision([state, preview])}
        log_path = self.plugin.data_file.parent / 'management_notice_log.json'
        audit = read_json(log_path, [])
        if not isinstance(audit, list):
            raise ManagementError('手动通知记录格式异常。')
        releases = []
        for version in changelog_versions(content, notifier.version):
            notes = release_notes(content, None, version)
            summary = next((line.strip().lstrip('-* ') for line in notes.splitlines()
                            if line.strip() and not line.startswith(('【v', '━━'))), '暂无更新条目')
            releases.append({**row, 'id': version, 'version': version, 'notes': notes,
                             'summary': summary[:100] + ('…' if len(summary) > 100 else '')})
        return releases, {'preview': preview, 'current': row, 'revision': row['revision'],
                          'platforms': [{'value': str(item.meta().id), 'label': str(item.meta().id)}
                                        for item in self.platforms()], 'audit': audit[-20:]}

    async def change_notices(self, body):
        from .maimaidx_update_notice import update_message_chunks

        group = identifier(body.get('group'), '群号')
        confirmed(body, group)
        if body.get('action') != 'send' or not self.plugin.config.get('update_notice', True):
            raise ManagementError('通知开关已关闭或操作无效。')
        if not self.plugin._is_update_notice_group_enabled(group):
            raise ManagementError('该群未明确开启舞萌功能或已被禁用，未发送。')
        task = self.plugin.update_notice_task
        if task is not None and not task.done():
            raise ManagementError('自动更新通知仍在运行，请结束后再手动发送。')
        async with self._notice_lock:
            rows, meta = await self.list_notices({})
            check_revision(body.get('revision'), meta['revision'])
            platform = next((item for item in self.platforms() if str(item.meta().id) == body.get('platform')), None)
            if platform is None:
                raise ManagementError('所选 OneBot 平台未就绪。')
            status = 'success'
            try:
                client = platform.get_client()
                login = await asyncio.wait_for(client.get_login_info(), timeout=15)
                nodes = [{'type': 'node', 'data': {'user_id': str(int(login['user_id'])),
                          'nickname': login.get('nickname') or '舞萌DX更新通知',
                          'content': [{'type': 'text', 'data': {'text': chunk.strip('\r\n')}}]}}
                         for chunk in update_message_chunks(meta['preview'])]
                await asyncio.wait_for(client.send_group_forward_msg(group_id=int(group), messages=nodes), timeout=30)
            except Exception:
                status = 'unconfirmed'
            log_path = self.plugin.data_file.parent / 'management_notice_log.json'
            audit = read_json(log_path, [])
            audit.append({'at': int(time.time()), 'platform': body['platform'], 'group': group, 'status': status})
            try:
                await asyncio.to_thread(write_json, log_path, audit[-100:])
            except OSError:
                raise ManagementError('发送结果的本地记录保存失败，请先核对群消息，勿直接重复发送。') from None
            if status != 'success':
                raise ManagementError('发送失败或未能确认，请先检查群消息，避免重复发送。')
        return {'message': '已向指定群发送当前版本通知；版本号及自动广播进度未重置。'}
