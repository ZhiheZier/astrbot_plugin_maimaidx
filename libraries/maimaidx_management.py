"""Admin operations over live plugin state, not an arbitrary JSON editor."""

import asyncio
from copy import deepcopy

from .maimaidx_management_store import (
    ManagementError, check_revision, identifier, local_alias_lock, read_json, revision, text, write_json,
)
from .maimaidx_music import alias, guess, mai
from .maimaidx_arcade import Arcade, arcade
from .maimaidx_api_data import maiApi
from .maimaidx_resource_archive import ResourceError
from .maimaidx_nearcade import NearcadeError


def page(items, query):
    try:
        number = max(1, min(100000, int(query.get('page', '1'))))
    except (TypeError, ValueError):
        raise ManagementError('页码格式错误。') from None
    total = len(items)
    number = min(number, max(1, (total + 29) // 30))
    return {'items': items[(number - 1) * 30:number * 30], 'page': number, 'total': total,
            'pages': max(1, (total + 29) // 30)}


def labels(values):
    if not isinstance(values, list) or len(values) > 100:
        raise ManagementError('别名列表格式错误或数量超过100。')
    result = []
    for value in values:
        value = text(value, '机厅别名', 40)
        if value.isdigit() or value == '机厅':
            raise ManagementError('机厅别名不能是纯数字或「机厅」。')
        if value not in result:
            result.append(value)
    return result


class MaimaiManagement:
    def __init__(self, plugin):
        self.plugin = plugin
        self._arcade_lock = asyncio.Lock()
        from .maimaidx_management_extra import ExtraManagement

        self.extra = ExtraManagement(plugin)

    def records(self, source):
        if source == 'nearcade':
            return self.plugin.nearcade.catalog
        if source == 'local':
            records = getattr(arcade, 'total', None)
            if records is None:
                raise ManagementError('本地机厅数据尚未加载。')
            return records
        raise ManagementError('请选择本地或 nearcade 数据源。')

    def status(self):
        return {'arcade_source': 'nearcade' if self.plugin.nearcade.uses_cloud_catalog else 'local',
                'maimai_whitelist': bool(self.plugin.config.get('maimaidxwhitelist', False)),
                'alias_whitelist': bool(maiApi.config.maimaidxaliaswhitelist),
                'alias_global': bool(maiApi.config.maimaidxaliaspush and alias.push.global_enabled),
                'songs': len(getattr(mai, 'total_list', []))}

    def group_row(self, gid):
        state = {
            'maimai': gid in self.plugin.enabled_groups and gid not in self.plugin.disabled_groups,
            'disabled': gid in self.plugin.disabled_groups,
            'arcade': gid in self.plugin.arcade_enabled_groups,
            'guess': gid in guess.switch.enable,
            'alias': gid in alias.push.enable and gid not in alias.push.disable,
            'alias_disabled': gid in alias.push.disable,
        }
        modes = self.status()
        alias_allowed = ((gid in alias.push.enable) if modes['alias_whitelist'] else gid not in alias.push.disable)
        enabled = self.plugin._is_group_enabled(gid)
        return {'id': gid, **state,
                'effective': {'maimai': enabled, 'arcade': enabled and state['arcade'],
                              'guess': enabled and state['guess'],
                              'alias': modes['alias_global'] and alias_allowed},
                'revision': revision({'state': state, 'modes': modes})}

    def groups(self, query):
        gids = set(self.plugin.enabled_groups) | set(self.plugin.disabled_groups) | set(self.plugin.arcade_enabled_groups)
        gids.update(guess.switch.enable + guess.switch.disable + alias.push.enable + alias.push.disable)
        for source in ('local', 'nearcade'):
            try:
                for record in self.records(source):
                    gids.update(str(group) for group in record.group)
            except (ResourceError, NearcadeError):
                continue
        keyword = text(query.get('q', ''), '群号', 20, empty=True)
        if keyword.isascii() and keyword.isdigit() and int(keyword) > 0:
            gids.add(str(int(keyword)))
        rows = [self.group_row(gid) for gid in sorted(gids, key=lambda value: (len(value), value)) if keyword in gid]
        return {**page(rows, query), 'modes': self.status()}

    def change_group(self, body):
        from .. import group_alias_file, guess_file

        gid = identifier(body.get('id'), '群号')
        field, enabled = body.get('field'), body.get('enabled')
        if field not in ('maimai', 'arcade', 'guess', 'alias') or type(enabled) is not bool:
            raise ManagementError('群开关参数错误。')
        if not self.plugin._group_settings_valid:
            raise ManagementError('群设置文件加载异常，请先修复原文件后重载插件。')
        check_revision(body.get('revision'), self.group_row(gid)['revision'])
        path = {'maimai': self.plugin.data_file, 'arcade': self.plugin.arcade_switch_file,
                'guess': guess_file, 'alias': group_alias_file}[field]
        existing = read_json(path, {})
        if not isinstance(existing, dict):
            raise ManagementError('群设置文件格式错误，原文件未修改。')
        if path.exists():
            keys = ('enabled_groups', 'disabled_groups') if field == 'maimai' else (
                ('enabled_groups',) if field == 'arcade' else ('enable', 'disable'))
            for key in keys:
                if not isinstance(existing.get(key, []), list) or any(
                    isinstance(value, bool) or not isinstance(value, (str, int)) for value in existing.get(key, [])
                ):
                    raise ManagementError('群设置文件内容异常，原文件未修改。')
            if field in ('guess', 'alias'):
                current = guess.switch if field == 'guess' else alias.push
                if any(set(str(value) for value in existing.get(key, [])) != set(getattr(current, key)) for key in keys):
                    raise ManagementError('设置文件已被外部修改，请重载插件后再编辑。')
                if field == 'alias' and existing.get('global_enabled', True) != alias.push.global_enabled:
                    raise ManagementError('推送总开关已被外部修改，请重载插件后再编辑。')
            else:
                current = {'enabled_groups': self.plugin.enabled_groups, 'disabled_groups': self.plugin.disabled_groups}
                if field == 'arcade':
                    current = {'enabled_groups': self.plugin.arcade_enabled_groups}
                if any(set(str(value) for value in existing.get(key, [])) != current[key] for key in keys):
                    raise ManagementError('设置文件已被外部修改，请重载插件后再编辑。')
        if field == 'maimai':
            on, off = set(self.plugin.enabled_groups), set(self.plugin.disabled_groups)
            (on.add if enabled else on.discard)(gid)
            (off.discard if enabled else off.add)(gid)
            write_json(self.plugin.data_file, {'enabled_groups': sorted(on), 'disabled_groups': sorted(off)})
            self.plugin.enabled_groups, self.plugin.disabled_groups = on, off
        elif field == 'arcade':
            on = set(self.plugin.arcade_enabled_groups)
            (on.add if enabled else on.discard)(gid)
            write_json(self.plugin.arcade_switch_file, {'enabled_groups': sorted(on)})
            self.plugin.arcade_enabled_groups = on
        else:
            store = guess if field == 'guess' else alias
            switch = store.switch if field == 'guess' else store.push
            updated = switch.model_copy(deep=True)
            updated.enable = [item for item in updated.enable if item != gid] + ([gid] if enabled else [])
            updated.disable = [item for item in updated.disable if item != gid] + ([] if enabled else [gid])
            write_json(guess_file if field == 'guess' else group_alias_file, updated.model_dump())
            if field == 'guess':
                guess.switch = updated
                if not enabled:
                    guess.end(gid)
            else:
                alias.push = updated
        return {'item': self.group_row(gid), 'message': '群开关已保存。'}

    @staticmethod
    def arcade_row(record, source, group=''):
        editable = {key: getattr(record, key) for key in ('name', 'location', 'province', 'mall', 'num', 'alias', 'group',
                                                        'group_aliases', 'game_machine_counts')}
        return {'id': record.id, **deepcopy(editable), 'person': record.person, 'status': record.status,
                'subscribed': int(group) in record.group if group else False,
                'revision': revision(editable), 'source': source}

    async def arcades(self, query):
        source = query.get('source', self.status()['arcade_source'])
        records = self.records(source)
        group = identifier(query['group'], '群号') if query.get('group') else ''
        keyword = text(query.get('q', ''), '搜索词', 150, empty=True)
        warning, more = '', False
        if source == 'nearcade' and query.get('remote') == '1':
            if not keyword:
                raise ManagementError('请填写关键词或云端店铺 ID。')
            records, more = await self.plugin.nearcade.search_catalog(keyword, group_id=group or None)
        else:
            records = [record for record in records if not keyword or keyword in '\n'.join(
                (record.id, record.name, record.location, *record.alias, *record.group_aliases.get(group, [])))]
        if query.get('subscribed') == '1':
            if not group:
                raise ManagementError('筛选订阅需要填写群号。')
            records = [record for record in records if int(group) in record.group]
        rows = [self.arcade_row(record, source, group) for record in records]
        if more:
            warning = '云端仅返回前20家，请缩小搜索范围。'
        return {**page(rows, query), 'source': source, 'warning': warning}

    async def change_arcade(self, body):
        async def commit():
            async with self._arcade_lock:
                return await self._change_arcade(body)

        task = asyncio.create_task(commit())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Finish the disk/memory transaction even if the dashboard request disconnects.
            await task
            raise

    async def _change_arcade(self, body):
        source = body.get('source')
        records = self.records(source)
        action = body.get('action')
        if action == 'create':
            if source != 'local':
                raise ManagementError('nearcade 店铺资料只读，请在 nearcade 维护。')
            fields = self.arcade_fields(body.get('fields', {}))
            if any(item.name == fields['name'] for item in records):
                raise ManagementError('本地已有同名机厅。')
            aid = str(max([9999] + [int(item.id) for item in records if item.id.isdigit()]) + 1)
            record = Arcade(id=aid, alias=[], group=[], person=0, by='', time='', **fields)
            records.append(record)
            try:
                if not await records.save_arcade():
                    raise OSError('save failed')
            except BaseException:
                records.remove(record)
                raise
        else:
            aid = identifier(body.get('id'), '机厅ID')
            matches = [record for record in records if record.id == aid]
            if len(matches) != 1:
                raise ManagementError('机厅不存在或 ID 重复，请刷新列表。')
            record = matches[0]
            async with records.alias_lock:
                check_revision(body.get('revision'), self.arcade_row(record, source)['revision'])
                before = record.model_copy(deep=True)
                index = records.index(record)
                if action == 'save':
                    if source != 'local':
                        raise ManagementError('nearcade 店名、地址、机台数量和人数只读。')
                    fields = self.arcade_fields(body.get('fields', {}))
                    if any(item.id != aid and item.name == fields['name'] for item in records):
                        raise ManagementError('本地已有同名机厅。')
                    for key, value in fields.items():
                        setattr(record, key, value)
                    if 'mai' in record.game_machine_counts:
                        record.game_machine_counts['mai'] = fields['num']
                elif action == 'delete':
                    if source != 'local':
                        raise ManagementError('不能从管理页删除云端店铺，请在 nearcade 维护。')
                    if body.get('confirm') != aid:
                        raise ManagementError('请输入机厅 ID 确认删除。')
                    records.remove(record)
                elif action == 'subscribe':
                    group = int(identifier(body.get('group'), '群号'))
                    if type(body.get('enabled')) is not bool:
                        raise ManagementError('订阅参数错误。')
                    record.group = [item for item in record.group if item != group] + ([group] if body['enabled'] else [])
                elif action == 'alias':
                    group = identifier(body['group'], '群号') if body.get('group') else ''
                    values = labels(body.get('aliases'))
                    if group:
                        previous = record.group_aliases.get(group, [])
                        if any(value not in previous for value in values) and int(group) not in record.group:
                            raise ManagementError('该群尚未订阅此机厅，请先订阅再添加群别名。')
                        for other in records:
                            if other.id != aid and any(value == other.name or value in other.group_aliases.get(group, []) for value in values):
                                raise ManagementError(f'群别名与机厅「{other.name}」冲突。')
                        if values:
                            record.group_aliases[group] = values
                        else:
                            record.group_aliases.pop(group, None)
                    else:
                        record.alias = values
                else:
                    raise ManagementError('不支持的机厅操作。')
                changed = {field: deepcopy(getattr(record, field)) for field in (
                    'name', 'location', 'province', 'mall', 'num', 'alias', 'group', 'group_aliases', 'game_machine_counts')
                    if getattr(record, field) != getattr(before, field)}
                try:
                    if not await records.save_arcade():
                        raise OSError('save failed')
                except BaseException:
                    if action == 'delete':
                        records.insert(index, record)
                    else:
                        for field, expected in changed.items():
                            if getattr(record, field) == expected:
                                setattr(record, field, deepcopy(getattr(before, field)))
                    raise
        if source == 'local':
            arcade.idList = [int(item.id) for item in records if item.id.isdigit()]
        return {'item': None if action == 'delete' else self.arcade_row(record, source), 'message': '机厅设置已保存。'}

    @staticmethod
    def arcade_fields(fields):
        if not isinstance(fields, dict) or set(fields) - {'name', 'location', 'province', 'mall', 'num'}:
            raise ManagementError('机厅资料字段不正确。')
        if type(fields.get('num')) is not int or not 0 <= fields['num'] <= 9999:
            raise ManagementError('舞萌机台数量需为0至9999的整数。')
        return {'name': text(fields.get('name'), '店名'), 'location': text(fields.get('location'), '地址', 500),
                'province': text(fields.get('province', ''), '省份', 50, empty=True),
                'mall': text(fields.get('mall', ''), '商场', 150, empty=True), 'num': fields['num']}

    def songs(self, query):
        from .maimaidx_local_alias_store import load_local_aliases

        local = load_local_aliases()
        keyword = text(query.get('q', ''), '歌曲搜索词', 150, empty=True).lower()
        rows = []
        for song in getattr(mai, 'total_list', []):
            sid = str(song.song_id)
            merged = mai.total_alias_list.by_id(sid) if hasattr(mai, 'total_alias_list') else []
            all_aliases = list(merged[0].Alias) if merged else []
            own = local.get(sid, [])
            if query.get('local_only') == '1' and not own:
                continue
            if keyword.isascii() and keyword.isdigit():
                if keyword != sid:
                    continue
            elif keyword and keyword not in '\n'.join((sid, song.song_name, *all_aliases, *own)).lower():
                continue
            rows.append({'id': sid, 'name': song.song_name, 'type': song.type,
                         'local': own, 'other': [value for value in all_aliases if value not in own],
                         'revision': revision(own)})
        return page(rows, query)

    async def change_song_alias(self, body):
        from .maimaidx_local_alias_store import change_local_alias, load_local_aliases

        sid = identifier(body.get('id'), '歌曲ID')
        value = text(body.get('alias'), '歌曲别名', 100).lower()
        action = body.get('action')
        if action not in ('add', 'delete'):
            raise ManagementError('不支持的歌曲别名操作。')
        async with local_alias_lock:
            local = load_local_aliases()
            check_revision(body.get('revision'), revision(local.get(sid, [])))
            await change_local_alias(sid, value, adding=action == 'add', reject_conflicts=True)
        return {'message': '本地歌曲别名已保存。'}
