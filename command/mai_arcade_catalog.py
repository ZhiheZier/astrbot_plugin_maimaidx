"""Commands backed by the live nearcade catalog, without local ID mappings."""

import re

from .mai_base import get_plain_message_text
from .permissions import can_manage_group
from ..libraries.maimaidx_arcade_messages import split_arcade_query_messages
from ..libraries.maimaidx_arcade import match_arcades
from ..libraries.maimaidx_nearcade import NearcadeError


def catalog_record_message(record, group_id=None):
    games = '、'.join(f'{game.name}×{record.game_machine_counts[game.key]}'
                     for game in record.known_games()) or '暂无游戏记录'
    aliases = record.group_aliases.get(str(group_id), [])
    suffix = '\n本群别名：' + '、'.join(aliases) if aliases else ''
    return f'{record.name}\nID：{record.id}（nearcade）\n地址：{record.location}\n机台：{games}' + suffix


async def catalog_management_handler(event, nearcade, action, superusers=None):
    text = get_plain_message_text(event).removeprefix('/')
    group = event.message_obj.group_id
    try:
        if action in ('add', 'delete', 'modify'):
            allowed = (str(event.get_sender_id()) in {str(item) for item in (superusers or [])} if action != 'modify'
                       else await can_manage_group(event, superusers))
            yield event.plain_result(
                '当前使用 nearcade 机厅信息，请在 https://nearca.de/shops 维护店铺及机台；本地机厅信息未修改'
                if allowed else ('仅允许管理员或群管理员管理机厅信息' if action == 'modify' else '仅允许管理员管理机厅信息'))
            return
        if action == 'check':
            if not group:
                yield event.plain_result('查看订阅功能仅在群聊中可用')
                return
            records = nearcade.catalog.group_subscribe_arcade(int(group))
            messages = []
            for record in records:
                warning = ''
                try:
                    await nearcade.search_catalog(record.id)
                except NearcadeError:
                    warning = '\n云端查询失败，以上为订阅缓存信息'
                messages.append(catalog_record_message(record, group) + warning)
            for message in split_arcade_query_messages(messages, compact=False):
                yield event.plain_result(message)
            if not records:
                yield event.plain_result('该群未订阅任何 nearcade 机厅')
            return
        if action == 'subscribe':
            if not await can_manage_group(event, superusers):
                yield event.plain_result('仅允许管理员或群管理员订阅和取消订阅')
                return
            if not group:
                yield event.plain_result('订阅功能仅在群聊中可用')
                return
            match = re.fullmatch(r'(订阅机厅|取消订阅机厅|取消订阅)\s+(.+)', text)
            if not match:
                return
            adding, name = match[1] == '订阅机厅', match[2].strip()
            # Offline cancellation must work even when a cloud shop was removed.
            records = match_arcades(nearcade.catalog, name, group)
            if adding or not records:
                records = await nearcade.find_catalog(name, group_id=group)
        elif action == 'alias':
            match = re.fullmatch(r'(添加机厅别名|删除机厅别名)\s+(\S+)\s+(\S+)', text)
            if not match:
                yield event.plain_result('格式错误：添加/删除机厅别名 <店名/ID> <别名>')
                return
            adding, name = match[1] == '添加机厅别名', match[2]
            records = await nearcade.find_catalog(name)
        else:
            name = re.sub(r'^(?:查找机厅|查询机厅|机厅查找|机厅查询|搜素机厅|机厅搜素)\s*', '', text).strip()
            records, more = await nearcade.search_catalog(name, group_id=group)
            blocks = [catalog_record_message(record, group) for record in records]
            if more:
                blocks.append('仅显示前20家，请缩小关键词后重查。')
            for message in split_arcade_query_messages(blocks, compact=False):
                yield event.plain_result(message)
            if not records:
                yield event.plain_result('没有这样的 nearcade 机厅哦')
            return
        if len(records) != 1:
            message = ('找到多个机厅，请使用云端 ID：\n' + '\n'.join(f'{item.id}：{item.name}' for item in records)
                       if records else '没有这样的 nearcade 机厅哦')
            yield event.plain_result(message)
            return
        record = records[0]
        field, value = ('group', int(group)) if action == 'subscribe' else ('alias', match[3])
        previous = list(getattr(record, field))
        updated = [item for item in previous if item != value]
        if adding:
            updated.append(value)
        if previous == updated:
            yield event.plain_result('记录没有变化，无需重复操作')
            return
        setattr(record, field, updated)
        try:
            if not await nearcade.catalog.save_arcade():
                raise OSError('save failed')
        except Exception:
            setattr(record, field, previous)
            raise NearcadeError('本地保存失败，订阅或别名未修改，请稍后重试') from None
        label = '订阅' if action == 'subscribe' else '别名'
        yield event.plain_result(f'nearcade 机厅：{record.name}（{record.id}）\n{label}{"添加" if adding else "取消"}成功')
    except NearcadeError as exc:
        yield event.plain_result(f'nearcade 查询或操作失败：{exc}')
