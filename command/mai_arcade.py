import re
import time
from re import Match
from typing import List

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import MessageSegment, loga
from ..command.mai_base import convert_message_segment_to_chain
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_arcade import (
    arcade,
    subscribe,
    update_alias,
    update_person,
    updata_arcade,
)


sv_help = """排卡指令如下：
添加机厅 <店名> <地址> <机台数量> 添加机厅信息
删除机厅 <店名> 删除机厅信息
修改机厅 <店名> 数量 <数量> ... 修改机厅信息
添加机厅别名 <店名> <别名>
订阅机厅 <店名> 订阅机厅，简化后续指令
查看订阅 查看群组订阅机厅的信息
取消订阅机厅 <店名> 取消群组机厅订阅
查找机厅,查询机厅,机厅查找,机厅查询 <关键词> 查询对应机厅信息
<店名/别名>人数设置,设定,=,增加,加,+,减少,减,-<人数> 操作排卡人数
<店名/别名>有多少人,有几人,有几卡,几人,几卡 查看排卡人数
机厅几人 查看已订阅机厅排卡人数"""


async def is_admin(event: AstrMessageEvent) -> bool:
    """检查用户是否是群管理员或群主"""
    group_id = event.message_obj.group_id
    if not group_id:
        return False
    
    try:
        sender_id = event.get_sender_id()
        member_info = await event.bot.get_group_member_info(group_id=group_id, user_id=sender_id)
        if member_info:
            role = member_info.get('role', '')
            return role in ['owner', 'admin']
    except Exception as e:
        loga.warning(f'检查管理员权限失败: {e}')
    return False


def is_superuser(event: AstrMessageEvent, superusers: list = None) -> bool:
    """检查用户是否是超级管理员"""
    if not superusers:
        return False
    sender_id = event.get_sender_id()
    return str(sender_id) in superusers


async def dx_arcade_help_handler(event: AstrMessageEvent):
    """帮助maimaiDX排卡"""
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


async def add_arcade_handler(event: AstrMessageEvent, superusers: list = None):
    """添加机厅"""
    if not is_superuser(event, superusers):
        yield event.plain_result('仅允许主人添加机厅\n请使用 来杯咖啡+内容 联系主人')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['添加机厅', '新增机厅']:
        if message_str.startswith(prefix):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    if args_str in ['帮助', 'help', '指令帮助']:
        yield event.plain_result('添加机厅指令格式：添加机厅 <店名> <位置> <机台数量> <别称1> <别称2> ...')
        return
    
    args: List[str] = args_str.split()
    if len(args) < 3:
        yield event.plain_result('格式错误：添加机厅 <店名> <地址> <机台数量> [别称1] [别称2] ...')
        return
    
    if not args[2].isdigit():
        yield event.plain_result('格式错误：添加机厅 <店名> <地址> <机台数量> [别称1] [别称2] ...')
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


async def delete_arcade_handler(event: AstrMessageEvent, superusers: list = None):
    """删除机厅"""
    if not is_superuser(event, superusers):
        yield event.plain_result('仅允许主人删除机厅\n请使用 来杯咖啡+内容 联系主人')
        return
    
    message_str = event.message_str.strip()
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


async def arcade_alias_handler(event: AstrMessageEvent):
    """添加/删除机厅别名"""
    message_str = event.message_str.strip()
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


async def modify_arcade_handler(event: AstrMessageEvent):
    """修改机厅"""
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员修改机厅信息')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['修改机厅', '编辑机厅']:
        if message_str.startswith(prefix):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    if not args[0].isdigit() and len(_arc := arcade.total.search_fullname(args[0])) > 1:
        msg = '找到多个相同店名的机厅，请使用店铺ID修改机厅\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
        yield event.plain_result(msg)
        return
    
    if args[1] == '数量' and len(args) == 3 and args[2].isdigit():
        msg = await updata_arcade(args[0], args[2])
        yield event.plain_result(msg)
        return
    
    yield event.plain_result('格式错误：修改机厅 <店名> [数量] <数量>')


async def subscribe_arcade_handler(event: AstrMessageEvent):
    """订阅/取消订阅机厅"""
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员订阅和取消订阅')
        return
    
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('订阅功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
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


async def check_subscribe_handler(event: AstrMessageEvent):
    """查看订阅"""
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
    - 别名：{alias}''')
        msg = '\n'.join(result)
        yield event.plain_result(msg)
    else:
        yield event.plain_result('该群未订阅任何机厅')


async def search_arcade_handler(event: AstrMessageEvent):
    """查找机厅"""
    message_str = event.message_str.strip()
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
    
    arcade_list = arcade.total.search_name(name)
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


async def arcade_person_handler(event: AstrMessageEvent):
    """操作排卡人数"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('排卡功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
    match = re.match(r'^(.+)?\s?(设置|设定|＝|=|增加|添加|加|＋|\+|减少|降低|减|－|-)\s?([0-9]+|＋|\+|－|-)(人|卡)?$', message_str)
    if not match:
        return
    
    person_str = match.group(3)
    if not person_str.isdigit() and person_str not in ['＋', '+', '－', '-']:
        yield event.plain_result('请输入正确的数字')
        return
    
    arcade_list = arcade.total.group_subscribe_arcade(group_id=int(group_id))
    if not arcade_list:
        yield event.plain_result('该群未订阅机厅，无法更改机厅人数')
        return
    
    value = match.group(2)
    person = int(person_str) if person_str.isdigit() else 1
    
    if match.group(1):
        if '人数' in match.group(1) or '卡' in match.group(1):
            arcadeName = match.group(1)[:-2] if '人数' in match.group(1) else match.group(1)[:-1]
        else:
            arcadeName = match.group(1)
        _arcade = []
        for _a in arcade_list:
            if arcadeName == _a.name:
                _arcade.append(_a)
                break
            if arcadeName in _a.alias:
                _arcade.append(_a)
                break
        if not _arcade:
            yield event.plain_result('已订阅的机厅中未找到该机厅')
            return
        
        try:
            sender_info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=event.get_sender_id())
            nickname = sender_info.get('nickname', '') if sender_info else ''
            msg = await update_person(_arcade, nickname, value, person)
            yield event.plain_result(msg)
        except Exception as e:
            loga.error(f'更新机厅人数失败: {e}')
            yield event.plain_result('更新机厅人数失败')


async def arcade_query_multiple_handler(event: AstrMessageEvent):
    """机厅几人"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('查询功能仅在群聊中可用')
        return
    
    gid = int(group_id)
    arcade_list = arcade.total.group_subscribe_arcade(gid)
    if arcade_list:
        result = arcade.total.arcade_to_msg(arcade_list)
        yield event.plain_result('\n'.join(result))
    else:
        yield event.plain_result('该群未订阅任何机厅')


async def arcade_query_person_handler(event: AstrMessageEvent):
    """有多少人/有几人/有几卡"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('查询功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
    # 移除后缀
    for suffix in ['有多少人', '有几人', '有几卡', '多少人', '多少卡', '几人', 'jr', '几卡']:
        if message_str.endswith(suffix):
            name = message_str[:-len(suffix)].strip().lower()
            break
    else:
        name = message_str.strip().lower()
    
    if name:
        arcade_list = arcade.total.search_name(name)
        if not arcade_list:
            yield event.plain_result('没有这样的机厅哦')
            return
        result = arcade.total.arcade_to_msg(arcade_list)
        yield event.plain_result('\n'.join(result))
    else:
        gid = int(group_id)
        arcade_list = arcade.total.group_subscribe_arcade(gid)
        if arcade_list:
            result = arcade.total.arcade_to_msg(arcade_list)
            yield event.plain_result('\n'.join(result))
        else:
            yield event.plain_result('该群未订阅任何机厅，请使用 订阅机厅 <名称> 指令订阅机厅')

