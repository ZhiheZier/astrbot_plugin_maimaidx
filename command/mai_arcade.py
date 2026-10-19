import asyncio
import re
import time
from re import Match
from typing import List

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import MessageSegment, loga
from ..command.mai_base import convert_message_segment_to_chain, get_plain_message_text
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_arcade import (
    arcade,
    match_arcades,
    subscribe,
    update_alias,
    update_attendance,
    AttendanceUpdateResult,
    updata_arcade,
)
from .permissions import can_manage_group as is_admin
from ..libraries.maimaidx_nearcade import NearcadeClient, NearcadeError
from ..libraries.maimaidx_arcade_games import DEFAULT_ARCADE_GAME, split_arcade_game, parse_arcade_reports, resolve_arcade_game
from ..libraries.maimaidx_arcade_messages import attendance_machine_count, format_attendance_lines
from .mai_arcade_queries import query_arcade_messages
from .mai_arcade_catalog import catalog_management_handler


def _arcade_records(nearcade=None):
    return nearcade.catalog if nearcade and nearcade.uses_cloud_catalog else arcade.total


async def _cloud_query_target(nearcade, name, group_id):
    records = nearcade.catalog
    if not name or name == '机厅':
        return records.group_subscribe_arcade(group_id), None
    await nearcade.prepare_cabinet_aliases((record.id for record in records), strict=False)
    configured = _configured_games(records, nearcade, strict=False)
    named = _named_games_for_target(records, name, configured, group_id)
    shop_name, game = split_arcade_game(name, named)
    if _matching_arcades(records, name, group_id):
        return await nearcade.find_catalog(name, group_id=group_id), None
    if not shop_name or shop_name == '机厅':
        return records.group_subscribe_arcade(group_id), game
    found = await nearcade.find_catalog(name, group_id=group_id)
    if any(item.name == name or item.id == name or name in item.aliases_for(group_id) for item in found):
        return found, None
    if shop_name != name:
        return await nearcade.find_catalog(shop_name, group_id=group_id), game
    return found, None


def _matching_arcades(records, name, group_id=None):
    return match_arcades(records, name, group_id)


def _configured_games(records, nearcade=None, *, strict=True):
    games = {}
    for record in records:
        choices = {game.key: game for game in record.known_games()}
        if nearcade:
            try:
                choices.update((game.key, game) for game in nearcade.games_for(record.id))
            except NearcadeError:
                if strict:
                    raise
        games[record.id] = list(choices.values())
    return games


def _named_games_for_target(records, target, configured, group_id=None):
    labels = [label for record in records for label in (record.name, record.id, *record.aliases_for(group_id))
              if label and target.startswith(label)]
    scoped = _matching_arcades(records, max(labels, key=len), group_id) if labels else records
    return [game for record in scoped for game in configured.get(record.id, []) if game.is_named]


def _arcade_target(records, target, nearcade=None, default_game=DEFAULT_ARCADE_GAME, *, group_id=None, scope_records=None):
    target = target.strip()
    allowed = {record.id for record in records}

    def matching(name):
        scope = records if scope_records is None else scope_records
        return [record for record in _matching_arcades(scope, name, group_id) if record.id in allowed]

    # An exact subscribed name takes precedence over game-like words in shop names.
    if matches := matching(target):
        return matches, default_game
    for suffix in ('人数', '卡'):
        if target.endswith(suffix):
            target = target[:-len(suffix)].rstrip()
            break
    if matches := matching(target):
        return matches, default_game
    configured = _configured_games(records, nearcade)
    named = _named_games_for_target(records, target, configured, group_id)
    name, game = split_arcade_game(target, named)
    if name == target:
        game = default_game
    if matches := matching(name):
        if game and game.is_named:
            matches = [record for record in matches if any(item.key == game.key for item in configured[record.id])]
        return matches, game
    if not name:
        return list(records), game
    return [], game


def _batch_arcade_reports(records, text, nearcade=None, *, group_id=None, scope_records=None):
    candidates = [(label, record) for record in records for label in (record.name, record.id, *record.aliases_for(group_id))
                  if label and text.startswith(label)]
    if candidates:
        prefix = max((label for label, _ in candidates), key=len)
        allowed = {record.id for record in records}
        scope = records if scope_records is None else scope_records
        records = [record for record in _matching_arcades(scope, prefix, group_id) if record.id in allowed]
        tail = text[len(prefix):].strip()
    else:
        tail = text
    configured = _configured_games(records, nearcade)
    named = [game for choices in configured.values() for game in choices if game.is_named]
    reports = parse_arcade_reports(tail, named)
    return (list(records), reports) if len(reports) > 1 else None


def _partition_hint(game, choices, name):
    partitions = [item for item in choices if item.is_named and ':@' not in item.key and item.family_key == game.family_key]
    if not game.is_named and partitions:
        return ('该游戏按分区记录人数，请指定映射名：' + '、'.join(item.name for item in partitions)
                + f'\n例如：{name}{partitions[0].name}3人')
    return ''


async def _apply_arcade_report(record, nickname, operation, person, game, nearcade):
    records = _arcade_records(nearcade)
    native = nearcade and nearcade.uses_cloud_catalog
    cloud_counter = native or ':@' in game.key
    snapshot = record.model_copy(deep=True) if cloud_counter else None
    if cloud_counter and nearcade and nearcade.upload_enabled and not (person == -1 and operation in ('=', '＝', '设置', '设定')):
        result = (await nearcade.read_attendance([record.id])).get(record.id)
        if result is None or isinstance(result, NearcadeError):
            return AttendanceUpdateResult(f'无法核对云端当前人数：{result}', successful=False), 'skipped', ''
        row = next((row for row in result.games if row.game.key == game.key), None)
        if row is None:
            return AttendanceUpdateResult('云端未找到该游戏，请查找机厅确认当前游戏记录', successful=False), 'skipped', ''
        if row.person is None and operation not in ('=', '＝', '设置', '设定'):
            return AttendanceUpdateResult('云端尚未上报人数，请先使用 =人数 设置', successful=False), 'skipped', ''
        current = record.attendance_for(game)
        current.person, current.by, current.time = row.person or 0, row.by, row.time
        if game != DEFAULT_ARCADE_GAME:
            record.game_attendance[game.key] = current
    update = await update_attendance(record, nickname, operation, person, game,
                                     **({'records': records} if native else {}))
    if cloud_counter and not update.successful:
        for field in ('person', 'status', 'by', 'time', 'game_attendance'):
            setattr(record, field, getattr(snapshot, field))
    if not update.changed or record.attendance_for(game).status == '闭店':
        return update, 'skipped', ''
    if not nearcade or not nearcade.upload_enabled:
        return update, 'disabled', ''
    try:
        count = record.attendance_for(game).person
        notice = (await nearcade.upload_attendance(record.id, count) if game == DEFAULT_ARCADE_GAME
                  else await nearcade.upload_attendance(record.id, count, game))
        return update, 'success' if notice else 'disabled', notice
    except NearcadeError as exc:
        return update, 'error', str(exc)
    except Exception as exc:
        loga.warning(f'nearcade 同步发生异常（{type(exc).__name__}），本地更新已保存')
        return update, 'error', '云端同步异常，请稍后核对'


async def _batch_arcade_report_message(event, records, reports, nearcade):
    if not records:
        return '本群尚未订阅该别名对应的机厅，请先订阅后再报人数'
    if len(records) != 1:
        return '已订阅多个机厅，请指定机厅名称或ID后报人数'
    record = records[0]
    choices = _configured_games(records, nearcade)[record.id]
    sender = await event.bot.get_group_member_info(group_id=int(event.message_obj.group_id), user_id=event.get_sender_id())
    nickname = sender.get('nickname', '') if sender else ''
    blocks = []
    async with (nearcade.lock_for(record.id) if nearcade else asyncio.Lock()):
        for report in reports:
            game = report.game
            if hint := _partition_hint(game, choices, record.name):
                blocks.append(f'❌ {game.name}：更新失败，{hint}')
                continue
            update, cloud_status, detail = await _apply_arcade_report(
                record, nickname, report.operation, report.person, game, nearcade,
            )
            attendance = record.attendance_for(game)
            if not update.successful:
                blocks.append(f'❌ {game.name}：更新失败，{update.message}')
                continue
            elif not update.changed:
                state = '（闭店）' if attendance.status == '闭店' else ''
                headline = f'人数未变化，当前 {attendance.person} 人{state}；未重复上传'
            elif attendance.status == '闭店':
                headline = '本地已闭店；未上传云端'
            else:
                suffix = {'success': '云端同步成功', 'disabled': '未开启云端上传',
                          'error': f'云端未确认同步：{detail}（本地记录保留）'}[cloud_status]
                headline = f'本地更新成功，当前 {attendance.person} 人；{suffix}'
            blocks.append('\n'.join(format_attendance_lines(
                record, game, headline=headline, known=True,
                machine_count=attendance_machine_count(record, game, choices),
            )))
    return f'📍 机厅：{record.name}\n' + '\n\n'.join(blocks)


sv_help = """排卡指令如下：
开启排卡 / 关闭排卡（管理员或群管理员）
添加机厅 <店名> <地址> <机台数量> 添加机厅信息
删除机厅 <店名> 删除机厅信息
修改机厅 <店名/ID> [游戏/分区名] 数量 <数量> 独立设置各游戏机台数，如修改机厅 123 chu 数量 2
添加机厅别名 <店名> <别名>
添加群机厅别名 / 删除群机厅别名 <店名/ID> <别名> 添加前需订阅该机厅（管理员或群管理员），支持查找、订阅、查询和报人数
订阅机厅 <店名> 订阅机厅，简化后续指令
查看订阅 查看群组订阅机厅的信息
取消订阅机厅 <店名> 取消群组机厅订阅
查找机厅,查询机厅,机厅查找,机厅查询 <关键词/ID> 查询对应机厅信息，无需开启排卡，仍遵守舞萌功能白名单
<店名/别名>人数设置,设定,=,增加,加,+,减少,减,-<人数> 操作排卡人数
<店名/别名> [游戏]=人数 / +人数 / -人数 分游戏报人数，如机厅chu=5、机厅太鼓+1
<店名/别名> [游戏]<人数>[人/卡] 也可直接报数，如机厅3、机厅mai3、机厅中二3人
同时报多个游戏：机厅mai3chu2；可加空格，逐项返回本地及云端结果
游戏支持舞萌/mai、中二/chu、太鼓/taiko、sdvx、iidx、音击/ongeki、wacca、jubeat等；省略默认舞萌
<店名/别名>=-1 闭店，清空人数并标记为闭店状态
<店名/别名> [游戏]几人 查看所选游戏的排卡人数，也可用几、j、jr等后缀，如机厅中二几
机厅几人 / jtj 每家机厅一行列出已订阅机厅各游戏人数，不显示等待等详情
已映射机厅优先读取nearcade人数及机台数量，无需Token，失败标明本地；查询不上传、不覆盖本地记录
更新及查询显示更新者、时间和机台信息；当日记录有可靠机台数时估算等待：舞萌每轮16分钟、每台2人，其他每轮12分钟、每台1人，仅供参考
直接发送QQ位置卡 查询附近舞萌DX机厅（需管理员开启位置查询，不依赖排卡开关）
管理员可在配置中选择 local 本地机厅或 nearcade 云端机厅数据源，默认local；两种订阅独立保存
nearcade模式直接查找、订阅云端店铺ID，无需本地机厅或映射；同类记录合并人数和机台数，官机、MML、手台等舞萌DX描述不分区
同类多条记录按总人数最少改动分配：增加和减少均优先人数最多、同人数靠前的，减少不足再扣下一条；调整后合计等于汇报值，不修改其他游戏
local模式可配置机台别名，如123[mml]=456:456000，报数用某机厅mml3；别名无需游戏前缀，按gameId精确更新
nearcade模式可在nearcade_game_aliases中配置14871[mml]=14871002，使用云端ID或店名加别名报数，如14871mml3
各游戏人数可选同步nearcade（需Token和上传开关；仅local模式还需机厅映射，闭店不上传）"""


def is_superuser(event: AstrMessageEvent, superusers: list = None) -> bool:
    """检查用户是否是管理员"""
    if not superusers:
        return False
    sender_id = event.get_sender_id()
    return str(sender_id) in superusers


async def dx_arcade_help_handler(event: AstrMessageEvent):
    """排卡帮助"""
    img = text_to_image(sv_help)
    img_base64 = image_to_base64(img)
    import tempfile
    import base64
    if img_base64.startswith('base64://'):
        img_base64 = img_base64[9:]
    img_data = base64.b64decode(img_base64)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
        temp_file.write(img_data)
        temp_file_path = temp_file.name
    chain = [Comp.Image.fromFileSystem(temp_file_path)]
    yield event.chain_result(chain)


async def add_arcade_handler(event: AstrMessageEvent, superusers: list = None, *, nearcade=None):
    """添加机厅"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'add', superusers):
            yield result
        return
    if not is_superuser(event, superusers):
        yield event.plain_result('仅允许管理员添加机厅')
        return
    
    message_str = get_plain_message_text(event)
    # 移除命令前缀
    for prefix in ['添加机厅', '新增机厅']:
        if message_str.startswith(prefix):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    if args_str in ['帮助', 'help', '指令帮助']:
        yield event.plain_result('添加机厅指令格式：添加机厅 <店名> <位置> <机台数量> [别名]；多个别名用空格分隔')
        return
    
    args: List[str] = args_str.split()
    if len(args) < 3:
        yield event.plain_result('格式错误：添加机厅 <店名> <地址> <机台数量> [别名]；多个别名用空格分隔')
        return
    
    if not args[2].isdigit():
        yield event.plain_result('格式错误：添加机厅 <店名> <地址> <机台数量> [别名]；多个别名用空格分隔')
        return
    
    if not arcade.total.search_fullname(args[0]):
        aid = sorted(arcade.idList, reverse=True) if arcade.idList else []
        if aid and (sid := aid[0]) >= 10000:
            sid += 1
        else:
            sid = 10000
        arcade_dict = {
            'name': args[0],
            'location': args[1],
            'province': '',
            'mall': '',
            'num': int(args[2]) if len(args) > 2 else 1,
            'id': str(sid),
            'alias': args[3:] if len(args) > 3 else [],
            'group': [],
            'person': 0,
            'by': '',
            'time': ''
        }
        arcade.total.add_arcade(arcade_dict)
        await arcade.total.save_arcade()
        yield event.plain_result(f'机厅：{args[0]} 添加成功')
    else:
        yield event.plain_result(f'机厅：{args[0]} 已存在，无法添加机厅')


async def delete_arcade_handler(event: AstrMessageEvent, superusers: list = None, *, nearcade=None):
    """删除机厅"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'delete', superusers):
            yield result
        return
    if not is_superuser(event, superusers):
        yield event.plain_result('仅允许管理员删除机厅')
        return
    
    message_str = get_plain_message_text(event)
    # 移除命令前缀
    for prefix in ['删除机厅', '移除机厅']:
        if message_str.startswith(prefix):
            name = message_str[len(prefix):].strip()
            break
    else:
        name = message_str.strip()
    
    if not name:
        yield event.plain_result('格式错误：删除机厅 <店名>，店名需全名')
        return
    
    if not arcade.total.search_fullname(name):
        yield event.plain_result(f'未找到机厅：{name}')
        return
    
    arcade.total.del_arcade(name)
    await arcade.total.save_arcade()
    yield event.plain_result(f'机厅：{name} 删除成功')


async def arcade_alias_handler(event: AstrMessageEvent, *, nearcade=None):
    """添加/删除机厅别名"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'alias'):
            yield result
        return
    message_str = get_plain_message_text(event)
    is_add = message_str.startswith('添加机厅别名')
    prefix = '添加机厅别名' if is_add else '删除机厅别名'
    
    if message_str.startswith(prefix):
        args_str = message_str[len(prefix):].strip()
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    if len(args) != 2:
        yield event.plain_result('格式错误：添加/删除机厅别名 <店名> <别名>')
        return
    
    if not args[0].isdigit() and len(_arc := arcade.total.search_fullname(args[0])) > 1:
        msg = '找到多个相同店名的机厅，请使用店铺ID更改机厅别名\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
        yield event.plain_result(msg)
        return
    
    msg = await update_alias(args[0], args[1], is_add)
    yield event.plain_result(msg)


async def group_arcade_alias_handler(event: AstrMessageEvent, superusers=None, *, nearcade=None):
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('群机厅别名仅在群聊中可用')
        return
    if not await is_admin(event, superusers):
        yield event.plain_result('仅允许管理员或群管理员管理群机厅别名')
        return
    text = get_plain_message_text(event).removeprefix('/')
    match = re.fullmatch(r'(添加群机厅别名|删除群机厅别名)\s+(.+?)\s+(\S+)', text)
    if not match:
        yield event.plain_result('格式错误：添加/删除群机厅别名 <店名/ID> <别名>')
        return
    adding, name, alias = match[1] == '添加群机厅别名', match[2].strip(), match[3]
    if len(alias) > 40 or alias.isdigit() or alias == '机厅':
        yield event.plain_result('别名最多40个字符，不能为纯数字或「机厅」；数字请用于机厅ID')
        return
    try:
        records = _arcade_records(nearcade)
        matches = _matching_arcades(records, name, group_id)
        if adding:
            matches = [record for record in matches if int(group_id) in record.group]
            if not matches:
                yield event.plain_result(f'本群尚未订阅该机厅，请先发送「订阅机厅 {name}」')
                return
        if nearcade and nearcade.uses_cloud_catalog:
            if adding and len(matches) == 1:
                matches = (await nearcade.search_catalog(matches[0].id))[0]
            elif not adding and not matches:
                matches = await nearcade.find_catalog(name, group_id=group_id)
        if len(matches) != 1:
            message = ('找到多个机厅，请使用ID：\n' + '\n'.join(f'{record.id}：{record.name}' for record in matches)
                       if matches else '未找到该机厅，请检查当前数据源及机厅名称或ID')
            yield event.plain_result(message)
            return
        record = matches[0]
        key = str(group_id)
        async with records.alias_lock:
            if adding and int(group_id) not in record.group:
                yield event.plain_result('本群尚未订阅该机厅，请先重新订阅后添加群别名')
                return
            if adding and (conflicts := [item for item in records if item.id != record.id
                                        and (alias == item.name or alias in item.group_aliases.get(key, []))]):
                yield event.plain_result(f'本群别名或机厅名称「{alias}」已指向 {conflicts[0].name}（{conflicts[0].id}），请换一个别名')
                return
            previous = {group: list(labels) for group, labels in record.group_aliases.items()}
            labels = record.group_aliases.get(key, [])
            if (alias in labels) == adding:
                yield event.plain_result('本群已拥有该别名，请勿重复添加' if adding else '本群未拥有该别名，未修改全局及其他群别名')
                return
            labels = [*labels, alias] if adding else [label for label in labels if label != alias]
            if labels:
                record.group_aliases[key] = labels
            else:
                record.group_aliases.pop(key, None)
            try:
                if not await records.save_arcade():
                    raise OSError('group alias save failed')
            except Exception:
                record.group_aliases = previous
                yield event.plain_result('本地保存失败，群机厅别名未修改，请稍后重试')
                return
        yield event.plain_result(f'机厅：{record.name}（{record.id}）\n已{"添加" if adding else "删除"}本群别名：{alias}')
    except NearcadeError as exc:
        yield event.plain_result(f'群机厅别名操作失败：{exc}')


async def modify_arcade_handler(event: AstrMessageEvent, superusers: list | None = None, *, nearcade=None):
    """修改机厅"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'modify', superusers):
            yield result
        return
    if not await is_admin(event, superusers):
        yield event.plain_result('仅允许管理员或群管理员修改机厅信息')
        return
    
    message_str = get_plain_message_text(event)
    # 移除命令前缀
    for prefix in ['修改机厅', '编辑机厅']:
        if message_str.startswith(prefix):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    if len(args) not in (3, 4):
        yield event.plain_result('格式错误：修改机厅 <店名/ID> [游戏/分区名] 数量 <数量>')
        return
    if not args[0].isdigit() and len(_arc := arcade.total.search_fullname(args[0])) > 1:
        msg = '找到多个相同店名的机厅，请使用店铺ID修改机厅\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
        yield event.plain_result(msg)
        return
    
    if len(args) == 3 and args[1] == '数量' and args[2].isascii() and args[2].isdigit() and len(args[2]) <= 4:
        msg = await updata_arcade(args[0], args[2])
        yield event.plain_result(msg)
        return
    if (len(args) == 4 and args[2] == '数量' and args[3].isascii() and args[3].isdigit()
            and len(args[3]) <= 4 and (game := resolve_arcade_game(args[1]))):
        msg = await updata_arcade(args[0], args[3], game)
        yield event.plain_result(msg)
        return
    
    yield event.plain_result('格式错误：修改机厅 <店名/ID> [游戏/分区名] 数量 <数量>')


async def subscribe_arcade_handler(event: AstrMessageEvent, superusers: list | None = None, *, nearcade=None):
    """订阅/取消订阅机厅"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'subscribe', superusers):
            yield result
        return
    if not await is_admin(event, superusers):
        yield event.plain_result('仅允许管理员或群管理员订阅和取消订阅')
        return
    
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('订阅功能仅在群聊中可用')
        return
    
    message_str = get_plain_message_text(event)
    # 匹配正则表达式
    match = re.match(r'^(订阅机厅|取消订阅机厅|取消订阅)\s(.+)', message_str)
    if not match:
        return
    
    sub = match.group(1) == '订阅机厅'
    name = match.group(2).strip()
    
    if not name.isdigit() and len(_arc := arcade.total.search_fullname(name)) > 1:
        msg = f'找到多个相同店名的机厅，请使用店铺ID订阅\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
        yield event.plain_result(msg)
        return
    
    msg = await subscribe(int(group_id), name, sub)
    yield event.plain_result(msg)


async def check_subscribe_handler(event: AstrMessageEvent, *, nearcade=None):
    """查看订阅"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'check'):
            yield result
        return
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('查看订阅功能仅在群聊中可用')
        return
    
    gid = int(group_id)
    arcadeList = arcade.total.group_subscribe_arcade(group_id=gid)
    if arcadeList:
        result = [f'群{gid}订阅机厅信息如下：']
        for a in arcadeList:
            alias = "\n  ".join(a.alias)
            result.append(f'''店名：{a.name}
    - 地址：{a.location}
    - 数量：{a.num}
    - 别名：{alias}
    - 本群别名：{'、'.join(a.group_aliases.get(str(gid), [])) or '无'}''')
        msg = '\n'.join(result)
        yield event.plain_result(msg)
    else:
        yield event.plain_result('该群未订阅任何机厅')


async def search_arcade_handler(event: AstrMessageEvent, *, nearcade=None):
    """查找机厅"""
    if nearcade and nearcade.uses_cloud_catalog:
        async for result in catalog_management_handler(event, nearcade, 'search'):
            yield result
        return
    message_str = get_plain_message_text(event)
    # 移除命令前缀
    for prefix in ['查找机厅', '查询机厅', '机厅查找', '机厅查询', '搜素机厅', '机厅搜素']:
        if message_str.startswith(prefix):
            name = message_str[len(prefix):].strip()
            break
    else:
        name = message_str.strip()
    
    if not name:
        yield event.plain_result('格式错误：查找机厅 <关键词>')
        return
    
    arcade_list = (arcade.total.search_id(name) if name.isascii() and name.isdigit()
                   else arcade.total.search_name(name, group_id=event.message_obj.group_id))
    if arcade_list:
        result = ['为您找到以下机厅：\n']
        for a in arcade_list:
            result.append(f'''店名：{a.name}
    - 地址：{a.location}
    - ID：{a.id}
    - 数量：{a.num}''')
        if len(arcade_list) < 5:
            yield event.plain_result('\n==========\n'.join(result))
        else:
            img = text_to_image('\n'.join(result))
            img_base64 = image_to_base64(img)
            import tempfile
            import base64
            if img_base64.startswith('base64://'):
                img_base64 = img_base64[9:]
            img_data = base64.b64decode(img_base64)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
                temp_file.write(img_data)
                temp_file_path = temp_file.name
            chain = [Comp.Image.fromFileSystem(temp_file_path)]
            yield event.chain_result(chain)
    else:
        yield event.plain_result('没有这样的机厅哦')


async def arcade_person_handler(event: AstrMessageEvent, nearcade: NearcadeClient | None = None):
    """操作排卡人数"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('排卡功能仅在群聊中可用')
        return
    
    message_str = get_plain_message_text(event).removeprefix('/')
    if len(message_str) > 4096:
        return
    try:
        all_records = _arcade_records(nearcade)
        arcade_list = all_records.group_subscribe_arcade(group_id=int(group_id))
    except NearcadeError as exc:
        yield event.plain_result(str(exc))
        return
    if arcade_list:
        try:
            if nearcade:
                await nearcade.prepare_cabinet_aliases(record.id for record in arcade_list)
            batch = _batch_arcade_reports(arcade_list, message_str, nearcade, group_id=group_id, scope_records=all_records)
            if batch:
                yield event.plain_result(await _batch_arcade_report_message(event, *batch, nearcade))
                return
        except (NearcadeError, ValueError) as exc:
            yield event.plain_result(f'批量报数未执行：{exc}')
            return
        except Exception as exc:
            loga.warning(f'批量更新机厅人数失败（{type(exc).__name__}）')
            yield event.plain_result('批量更新机厅人数异常，请查询核对当前人数后重试')
            return
    match = re.match(r'^(.+?)?\s?(设置|设定|＝|=|增加|添加|加|＋|\+|减少|降低|减|－|-)\s?(-?[0-9]+|＋|\+|－|-)(人|卡)?$', message_str)
    implicit = match is None
    if match:
        target, value, person_str = match.group(1) or '', match.group(2), match.group(3)
    else:
        # A numeric message alone cannot distinguish a shop ID from a headcount.
        if message_str.isascii() and message_str.isdigit():
            return
        labels = sorted({label for record in arcade_list for label in (record.name, record.id, *record.aliases_for(group_id))
                         if label and message_str.startswith(label)}, key=len, reverse=True)
        prefix = labels[0] if labels else ''
        count = re.fullmatch(r'(.*?)\s*([0-9]+)\s*(?:人|卡)?', message_str[len(prefix):])
        if count is None or not (prefix or count[1].strip()):
            return
        target, value, person_str = prefix + count[1].strip(), '=', count[2]
    if len(person_str) > 9:
        return
    if not person_str.lstrip('-').isdigit() and person_str not in ['＋', '+', '－', '-']:
        yield event.plain_result('请输入正确的数字')
        return
    
    if not arcade_list:
        if implicit:
            return
        yield event.plain_result('该群未订阅机厅，无法更改机厅人数')
        return
    
    person = int(person_str) if person_str.lstrip('-').isdigit() else 1
    
    try:
        _arcade, game = _arcade_target(arcade_list, target, nearcade, group_id=group_id, scope_records=all_records)
    except NearcadeError as exc:
        yield event.plain_result(f'排卡映射配置有误：{exc}')
        return
    if not _arcade:
        if implicit:
            return
        yield event.plain_result('已订阅的机厅中未找到该机厅，请检查名称和游戏名')
        return
    if len(_arcade) != 1:
        yield event.plain_result('已订阅多个机厅，请指定机厅名称或ID后报人数')
        return
    try:
        choices = _configured_games(_arcade, nearcade)[_arcade[0].id]
    except NearcadeError as exc:
        yield event.plain_result(f'排卡映射配置有误：{exc}')
        return
    if hint := _partition_hint(game, choices, _arcade[0].name):
        yield event.plain_result(hint)
        return
    try:
        sender_info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=event.get_sender_id())
        nickname = sender_info.get('nickname', '') if sender_info else ''
        async with (nearcade.lock_for(_arcade[0].id) if nearcade else asyncio.Lock()):
            update, cloud_status, detail = await _apply_arcade_report(_arcade[0], nickname, value, person, game, nearcade)
            record = _arcade[0]
            attendance = record.attendance_for(game)
            if not update.successful:
                msg = f'📍 机厅：{record.name}\n❌ {game.name}：更新失败，{update.message}'
            else:
                if attendance.status == '闭店':
                    headline = '本地已闭店，人数已清空；未上传云端'
                elif update.changed:
                    headline = f'人数已更新为 {attendance.person} 人'
                else:
                    headline = f'人数没有变化，当前 {attendance.person} 人；未重复上传'
                lines = format_attendance_lines(
                    record, game, headline=headline, known=True,
                    machine_count=attendance_machine_count(record, game, choices),
                )
                if cloud_status == 'success':
                    lines.append(f'☁️ {detail}')
                elif cloud_status == 'error':
                    lines.append(f'☁️ 本地更新已保存，云端未确认同步：{detail}')
                elif cloud_status == 'disabled':
                    lines.append('☁️ 未开启云端上传，仅保存本地记录')
                msg = f'📍 机厅：{record.name}\n' + '\n'.join(lines)
        yield event.plain_result(msg)
    except Exception as e:
        loga.error(f'更新机厅人数失败: {e}')
        yield event.plain_result('更新机厅人数失败')


async def arcade_query_multiple_handler(event: AstrMessageEvent, nearcade: NearcadeClient | None = None):
    """机厅几人"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('查询功能仅在群聊中可用')
        return
    
    gid = int(group_id)
    try:
        arcade_list = _arcade_records(nearcade).group_subscribe_arcade(gid)
    except NearcadeError as exc:
        yield event.plain_result(str(exc))
        return
    if arcade_list:
        try:
            configured = _configured_games(arcade_list, nearcade, strict=False)
        except NearcadeError as exc:
            yield event.plain_result(f'排卡映射配置有误：{exc}')
            return
        for message in await query_arcade_messages(arcade_list, configured, nearcade, compact=True):
            yield event.plain_result(message)
    else:
        yield event.plain_result('该群未订阅任何机厅')


async def arcade_query_person_handler(event: AstrMessageEvent, nearcade: NearcadeClient | None = None):
    """有多少人/有几人/有几卡"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('查询功能仅在群聊中可用')
        return
    
    message_str = get_plain_message_text(event).removeprefix('/')
    # 移除后缀
    for suffix in ['有多少人', '有几人', '有几卡', '多少人', '多少卡', '几人', '几卡', '有几', '几', 'jr', 'j']:
        if message_str.endswith(suffix):
            name = message_str[:-len(suffix)].strip()
            break
    else:
        name = message_str.strip()
    
    try:
        records = _arcade_records(nearcade)
        if nearcade and nearcade.uses_cloud_catalog:
            arcade_list, game = await _cloud_query_target(nearcade, name, int(group_id))
            if not arcade_list:
                yield event.plain_result('没有这样的机厅哦' if name and name != '机厅' else '该群未订阅任何机厅')
                return
            configured = _configured_games(arcade_list, nearcade, strict=False)
            for message in await query_arcade_messages(arcade_list, configured, nearcade, selected=game,
                                                       compact=not name or name == '机厅'):
                yield event.plain_result(message)
            return
        if nearcade:
            await nearcade.prepare_cabinet_aliases((record.id for record in records), strict=False)
        configured = _configured_games(records, nearcade, strict=False)
        if name and name != '机厅':
            if exact := _matching_arcades(records, name, group_id):
                arcade_list, game = exact, None
            else:
                named = _named_games_for_target(records, name, configured, group_id)
                shop_name, selected = split_arcade_game(name, named)
                game = selected if shop_name != name else None
                if not shop_name or shop_name == '机厅':
                    arcade_list = records.group_subscribe_arcade(int(group_id))
                else:
                    arcade_list = (_matching_arcades(records, shop_name, group_id)
                                   or records.search_name(shop_name, group_id=group_id))
                if game and game.is_named:
                    arcade_list = [record for record in arcade_list
                                   if any(item.key == game.key for item in configured[record.id])]
        else:
            arcade_list, game = records.group_subscribe_arcade(int(group_id)), None
        if not arcade_list:
            yield event.plain_result('没有这样的机厅哦' if name and name != '机厅' else '该群未订阅任何机厅')
            return
        for message in await query_arcade_messages(arcade_list, configured, nearcade, selected=game,
                                                  compact=not name or name == '机厅'):
            yield event.plain_result(message)
    except NearcadeError as exc:
        label = 'nearcade 查询失败' if nearcade and nearcade.uses_cloud_catalog else '排卡映射配置有误'
        yield event.plain_result(f'{label}：{exc}')
