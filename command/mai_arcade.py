import re
import tempfile
import time
from typing import List, Match
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import log, loga
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_arcade import (
    arcade,
    download_arcade_info,
    subscribe,
    update_alias,
    update_person,
    updata_arcade,
)
from .mai_base import convert_message_segment_to_chain


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


async def arcade_help_handler(event: AstrMessageEvent):
    """帮助maimaiDX排卡"""
    try:
        img_base64 = image_to_base64(text_to_image(sv_help))
        if img_base64.startswith('base64://'):
            import base64
            base64_data = img_base64.replace('base64://', '')
            img_data = base64.b64decode(base64_data)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
            temp_file.write(img_data)
            temp_file.close()
            
            chain = [Comp.Image.fromFileSystem(temp_file.name)]
            yield event.chain_result(chain)
        else:
            yield event.plain_result(sv_help)
    except Exception as e:
        log.error(f'生成帮助图片失败: {e}')
        yield event.plain_result(sv_help)


async def add_arcade_handler(event: AstrMessageEvent, superusers: list = None):
    """添加机厅"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许主人添加机厅\n请使用 来杯咖啡+内容 联系主人')
        return
    
    message_str = event.message_str.strip()
    args = message_str.replace('添加机厅', '').replace('新增机厅', '').strip().split()
    
    if len(args) == 1 and args[0] in ['帮助', 'help', '指令帮助']:
        msg = '添加机厅指令格式：添加机厅 <店名> <位置> <机台数量> <别称1> <别称2> ...'
        yield event.plain_result(msg)
        return
    
    if len(args) >= 3:
        if not args[2].isdigit():
            msg = '格式错误：添加机厅 <店名> <地址> <机台数量> [别称1] [别称2] ...'
        else:
            if not arcade.total.search_fullname(args[0]):
                aid = sorted(arcade.idList, reverse=True)
                if (sid := aid[0]) >= 10000:
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
                msg = f'机厅：{args[0]} 添加成功'
            else:
                msg = f'机厅：{args[0]} 已存在，无法添加机厅'
    else:
        msg = '格式错误：添加机厅 <店名> <地址> <机台数量> [别称1] [别称2] ...'
    
    yield event.plain_result(msg)


async def delete_arcade_handler(event: AstrMessageEvent, superusers: list = None):
    """删除机厅"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许主人删除机厅\n请使用 来杯咖啡+内容 联系主人')
        return
    
    message_str = event.message_str.strip()
    name = message_str.replace('删除机厅', '').replace('移除机厅', '').strip()
    
    if not name:
        msg = '格式错误：删除机厅 <店名>，店名需全名'
    else:
        if not arcade.total.search_fullname(name):
            msg = f'未找到机厅：{name}'
        else:
            arcade.total.del_arcade(name)
            await arcade.total.save_arcade()
            msg = f'机厅：{name} 删除成功'
    
    yield event.plain_result(msg)


async def arcade_alias_handler(event: AstrMessageEvent, superusers: list = None):
    """添加/删除机厅别名"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许主人操作机厅别名')
        return
    
    message_str = event.message_str.strip()
    is_add = '添加机厅别名' in message_str
    args = message_str.replace('添加机厅别名', '').replace('删除机厅别名', '').strip().split()
    
    if len(args) != 2:
        msg = '格式错误：添加/删除机厅别名 <店名> <别名>'
    elif not args[0].isdigit() and len(_arc := arcade.total.search_fullname(args[0])) > 1:
        msg = '找到多个相同店名的机厅，请使用店铺ID更改机厅别名\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
    else:
        msg = await update_alias(args[0], args[1], is_add)
    
    yield event.plain_result(msg)


async def modify_arcade_handler(event: AstrMessageEvent):
    """修改机厅"""
    from ..utils.permission import is_admin
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员修改机厅信息')
        return
    
    message_str = event.message_str.strip()
    args = message_str.replace('修改机厅', '').replace('编辑机厅', '').strip().split()
    
    if not args[0].isdigit() and len(_arc := arcade.total.search_fullname(args[0])) > 1:
        msg = '找到多个相同店名的机厅，请使用店铺ID修改机厅\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
    elif args[1] == '数量' and len(args) == 3 and args[2].isdigit():
        msg = await updata_arcade(args[0], args[2])
    else:
        msg = '格式错误：修改机厅 <店名> [数量] <数量>'
    
    yield event.plain_result(msg)


async def subscribe_arcade_handler(event: AstrMessageEvent):
    """订阅/取消订阅机厅"""
    from ..utils.permission import is_admin
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员订阅和取消订阅')
        return
    
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
    is_subscribe = '订阅机厅' in message_str
    name = message_str.replace('订阅机厅', '').replace('取消订阅机厅', '').replace('取消订阅', '').strip()
    
    if not name:
        msg = '格式错误：订阅机厅 <店名> 或 取消订阅机厅 <店名>'
    elif not name.isdigit() and len(_arc := arcade.total.search_fullname(name)) > 1:
        msg = f'找到多个相同店名的机厅，请使用店铺ID订阅\n' + '\n'.join([f'{_.id}：{_.name}' for _ in _arc])
    else:
        msg = await subscribe(int(group_id), name, is_subscribe)
    
    yield event.plain_result(msg)


async def check_subscribe_handler(event: AstrMessageEvent):
    """查看订阅"""
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
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
    else:
        msg = '该群未订阅任何机厅'
    
    yield event.plain_result(msg)


async def search_arcade_handler(event: AstrMessageEvent):
    """查找机厅"""
    message_str = event.message_str.strip()
    name = message_str.replace('查找机厅', '').replace('查询机厅', '').replace('机厅查找', '').replace('机厅查询', '').replace('搜素机厅', '').replace('机厅搜素', '').strip()
    
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
            try:
                img_base64 = image_to_base64(text_to_image('\n'.join(result)))
                if img_base64.startswith('base64://'):
                    import base64
                    base64_data = img_base64.replace('base64://', '')
                    img_data = base64.b64decode(base64_data)
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                    temp_file.write(img_data)
                    temp_file.close()
                    
                    chain = [Comp.Image.fromFileSystem(temp_file.name)]
                    yield event.chain_result(chain)
                else:
                    yield event.plain_result('\n'.join(result))
            except Exception as e:
                log.error(f'生成图片失败: {e}')
                yield event.plain_result('\n'.join(result))
    else:
        yield event.plain_result('没有这样的机厅哦')


async def arcade_person_handler(event: AstrMessageEvent):
    """操作排卡人数"""
    try:
        # 只处理群消息
        if event.is_private_chat():
            return
        
        message_str = event.message_str.strip()
        # 匹配正则: ^(.+)?\s?(设置|设定|＝|=|增加|添加|加|＋|\+|减少|降低|减|－|-)\s?([0-9]+|＋|\+|－|-)(人|卡)?$
        pattern = r'^(.+)?\s?(设置|设定|＝|=|增加|添加|加|＋|\+|减少|降低|减|－|-)\s?([0-9]+|＋|\+|－|-)(人|卡)?$'
        match = re.match(pattern, message_str)
        
        if not match:
            return
        
        group_id = event.message_obj.group_id  # 私聊时为空字符串
        if not group_id:
            return
        
        # 获取发送者昵称
        nickname = '用户'
        try:
            info = await event.bot.get_group_member_info(
                group_id=int(group_id),
                user_id=int(event.get_sender_id()),
                no_cache=True
            )
            nickname = info.get('card') or info.get('nickname', '用户')
        except:
            pass
        
        person_str = match.group(3)
        if not person_str.isdigit() and person_str not in ['＋', '+', '－', '-']:
            yield event.plain_result('请输入正确的数字')
            return
        
        arcade_list = arcade.total.group_subscribe_arcade(group_id=int(group_id))
        if not arcade_list:
            yield event.plain_result('该群未订阅机厅，无法更改机厅人数')
            return
        
        value = match.group(2)
        person = int(person_str) if person_str.isdigit() else 0
        
        if match.group(1):
            arcadeName = match.group(1)
            if '人数' in arcadeName:
                arcadeName = arcadeName[:-2]
            elif '卡' in arcadeName:
                arcadeName = arcadeName[:-1]
            arcadeName = arcadeName.strip()
            
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
            else:
                msg = await update_person(_arcade, nickname, value, person)
                yield event.plain_result(msg)
    except Exception as e:
        log.error(f'操作排卡人数失败: {e}', exc_info=True)


async def arcade_query_multiple_handler(event: AstrMessageEvent):
    """机厅几人 - 查看已订阅机厅排卡人数"""
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    arcade_list = arcade.total.group_subscribe_arcade(int(group_id))
    if arcade_list:
        result = arcade.total.arcade_to_msg(arcade_list)
        yield event.plain_result('\n'.join(result))
    else:
        yield event.plain_result('该群未订阅任何机厅')


async def arcade_query_person_handler(event: AstrMessageEvent):
    """查询排卡人数"""
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
    # 移除后缀
    name = message_str.replace('有多少人', '').replace('有几人', '').replace('有几卡', '').replace('多少人', '').replace('多少卡', '').replace('几人', '').replace('jr', '').replace('几卡', '').strip().lower()
    
    if name:
        arcade_list = arcade.total.search_name(name)
        if not arcade_list:
            yield event.plain_result('没有这样的机厅哦')
            return
        result = arcade.total.arcade_to_msg(arcade_list)
        yield event.plain_result('\n'.join(result))
    else:
        arcade_list = arcade.total.group_subscribe_arcade(int(group_id))
        if arcade_list:
            result = arcade.total.arcade_to_msg(arcade_list)
            yield event.plain_result('\n'.join(result))
        else:
            yield event.plain_result('该群未订阅任何机厅，请使用 订阅机厅 <名称> 指令订阅机厅')


async def arcade_daily_update():
    """机厅数据每日更新 - 每天凌晨3点执行"""
    try:
        await download_arcade_info()
        for _ in arcade.total:
            _.person = 0
            _.by = '自动清零'
            _.time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        await arcade.total.save_arcade()
        loga.info('maimaiDX排卡数据更新完毕')
    except Exception as e:
        loga.error(f'机厅数据更新失败: {e}', exc_info=True)

