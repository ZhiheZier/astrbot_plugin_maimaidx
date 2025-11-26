import asyncio
import json
import re
import traceback
from re import Match
from textwrap import dedent
from typing import Any, List

import aiohttp
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import SONGS_PER_PAGE, UUID, log, public_addr
from ..command.mai_base import convert_message_segment_to_chain
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import ServerError
from ..libraries.maimaidx_model import Alias, PushAliasStatus
from ..libraries.maimaidx_music import alias, mai, update_local_alias
from ..libraries.maimaidx_music_info import draw_music_info


async def convert_chain_to_onebot_format(chain: List[Any]):
    """将消息链转换为 OneBot 协议格式（字典列表）"""
    result = []
    for item in chain:
        if isinstance(item, str):
            # 字符串直接转换为文本消息
            if item.strip():
                result.append({"type": "text", "data": {"text": item}})
        elif isinstance(item, Comp.Plain):
            # Plain 组件转换为文本消息
            text = item.text if hasattr(item, 'text') else str(item)
            if text.strip():
                result.append({"type": "text", "data": {"text": text}})
        elif isinstance(item, Comp.Image):
            # Image 组件转换为图片消息
            try:
                # 使用 convert_to_base64 方法获取 base64 字符串
                base64_str = await item.convert_to_base64()
                result.append({"type": "image", "data": {"file": f"base64://{base64_str}"}})
            except Exception as e:
                log.error(f'转换图片失败: {e}')
        elif isinstance(item, Comp.At):
            # At 组件转换为 at 消息
            result.append({"type": "at", "data": {"qq": str(item.qq)}})
    return result


async def update_alias_handler(event: AstrMessageEvent, superusers: list = None):
    """更新别名库命令处理"""
    sender_id = event.get_sender_id()
    if superusers and str(sender_id) not in superusers:
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    try:
        await mai.get_music_alias()
        log.info('手动更新别名库成功')
        yield event.plain_result('手动更新别名库成功')
    except Exception as e:
        log.error(f'手动更新别名库失败: {e}')
        log.error(traceback.format_exc())
        yield event.plain_result('手动更新别名库失败')
        

async def alias_switch_on_off_handler(event: AstrMessageEvent, superusers: list = None):
    """全局开启/关闭别名推送命令处理"""
    sender_id = event.get_sender_id()
    if superusers and str(sender_id) not in superusers:
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    try:
        # 获取所有群组列表
        group_list = await event.bot.get_group_list()
        group_id = [g['group_id'] for g in group_list]
        
        message_str = event.message_str.strip()
        if message_str == '全局关闭别名推送':
            await alias.alias_global_change(False, group_id)
            yield event.plain_result('已全局关闭maimai别名推送')
        elif message_str == '全局开启别名推送':
            await alias.alias_global_change(True, group_id)
            yield event.plain_result('已全局开启maimai别名推送')
        else:
            yield event.plain_result('命令格式错误')
    except Exception as e:
        log.error(f'全局开关别名推送失败: {e}')
        log.error(traceback.format_exc())
        yield event.plain_result('操作失败')


async def alias_local_apply_handler(event: AstrMessageEvent):
    """添加本地别名命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['添加本地别名', '添加本地别称']:
        if message_str.startswith(prefix):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    if len(args) != 2:
        yield event.plain_result('参数错误，格式：添加本地别名 <歌曲ID> <别名>')
        return
    
    song_id, alias_name = args
    if not mai.total_list.by_id(song_id):
        yield event.plain_result(f'未找到ID为「{song_id}」的曲目')
        return
    
    server_exist = await maiApi.get_songs_alias(song_id)
    if isinstance(server_exist, Alias) and alias_name.lower() in server_exist.Alias:
        yield event.plain_result(f'该曲目的别名「{alias_name}」已存在别名服务器')
        return
    
    if not hasattr(mai, 'total_alias_list') or not mai.total_alias_list:
        yield event.plain_result('别名数据未加载，请稍后再试或联系管理员')
        return
    
    local_exist = mai.total_alias_list.by_id(song_id)
    if local_exist and alias_name.lower() in local_exist[0].Alias:
        yield event.plain_result('本地别名库已存在该别名')
        return
    
    issave = await update_local_alias(song_id, alias_name)
    if not issave:
        msg = '添加本地别名失败'
    else:
        msg = f'已成功为ID「{song_id}」添加别名「{alias_name}」到本地别名库'
    yield event.plain_result(msg)


async def alias_apply_handler(event: AstrMessageEvent):
    """添加别名命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    try:
        message_str = event.message_str.strip()
        # 移除命令前缀
        for prefix in ['添加别名', '增加别名', '增添别名', '添加别称']:
            if message_str.startswith(prefix):
                args_str = message_str[len(prefix):].strip()
                break
        else:
            args_str = message_str
        
        args: List[str] = args_str.split()
        if len(args) < 2:
            yield event.plain_result('参数错误，格式：添加别名 <歌曲ID> <别名>')
            return
        
        song_id = args[0]
        if not song_id.isdigit():
            yield event.plain_result('请输入正确的ID')
            return
        
        alias_name = ' '.join(args[1:])
        if not mai.total_list.by_id(song_id):
            yield event.plain_result(f'未找到ID为「{song_id}」的曲目')
            return
        
        isexist = await maiApi.get_songs_alias(song_id)
        if isinstance(isexist, Alias) and alias_name.lower() in isexist.Alias:
            yield event.plain_result(f'该曲目的别名「{alias_name}」已存在别名服务器')
            return
            
        group_id = event.message_obj.group_id or 0
        msg = await maiApi.post_alias(song_id, alias_name, event.get_sender_id(), int(group_id) if group_id else 0)
        yield event.plain_result(msg)
    except (ServerError, ValueError) as e:
        log.error(traceback.format_exc())
        yield event.plain_result(str(e))


async def alias_agree_handler(event: AstrMessageEvent):
    """同意别名命令处理"""
    try:
        message_str = event.message_str.strip()
        # 移除命令前缀
        for prefix in ['同意别名', '同意别称']:
            if message_str.startswith(prefix):
                tag = message_str[len(prefix):].strip().upper()
                break
        else:
            tag = message_str.strip().upper()
        
        if not tag:
            yield event.plain_result('参数错误，格式：同意别名 <Tag>')
            return
        
        status = await maiApi.post_agree_user(tag, event.get_sender_id())
        yield event.plain_result(status)
    except ValueError as e:
        yield event.plain_result(str(e))


async def alias_status_handler(event: AstrMessageEvent):
    """当前投票命令处理"""
    try:
        message_str = event.message_str.strip()
        # 移除命令前缀
        for prefix in ['当前投票', '当前别名投票', '当前别称投票']:
            if message_str.startswith(prefix):
                args = message_str[len(prefix):].strip()
                break
        else:
            args = ''
        
        status = await maiApi.get_alias_status()
        if not status:
            yield event.plain_result('未查询到正在进行的别名投票')
            return
        
        page = max(min(int(args), len(status) // SONGS_PER_PAGE + 1), 1) if args and args.isdigit() else 1
        result = []
        for num, _s in enumerate(status):
            if (page - 1) * SONGS_PER_PAGE <= num < page * SONGS_PER_PAGE:
                apply_alias = _s.ApplyAlias
                if len(_s.ApplyAlias) > 15:
                    apply_alias = _s.ApplyAlias[:15] + '...'
                result.append(
                    dedent(f'''\
                        - {_s.Tag}：
                        - ID：{_s.SongID}
                        - 别名：{apply_alias}
                        - 票数：{_s.AgreeVotes}/{_s.Votes}
                    ''').strip()
                )
        result.append(f'第「{page}」页，共「{len(status) // SONGS_PER_PAGE + 1}」页')
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
    except (ServerError, ValueError) as e:
        log.error(traceback.format_exc())
        yield event.plain_result(str(e))


async def alias_song_handler(event: AstrMessageEvent):
    """有什么别名命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_alias_list') or not mai.total_alias_list:
        yield event.plain_result('别名数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 匹配正则表达式
    match = re.match(r'^(id)?\s?(.+)\s?有什么别[名称]$', message_str, re.IGNORECASE)
    if not match:
        return  # 不匹配则不处理
    
    findid = bool(match.group(1))
    name = match.group(2).strip()
    aliases = None
    
    if findid and name.isdigit():
        alias_id = mai.total_alias_list.by_id(name)
        if not alias_id:
            yield event.plain_result('未找到此歌曲\n可以使用「添加别名」指令给该乐曲添加别名')
            return
        else:
            aliases = alias_id
    else:
        aliases = mai.total_alias_list.by_alias(name)
        if not aliases:
            if name.isdigit():
                alias_id = mai.total_alias_list.by_id(name)
                if not alias_id:
                    yield event.plain_result('未找到此歌曲\n可以使用「添加别名」指令给该乐曲添加别名')
                    return
                else:
                    aliases = alias_id
            else:
                yield event.plain_result('未找到此歌曲\n可以使用「添加别名」指令给该乐曲添加别名')
                return
    
    if len(aliases) != 1:
        msg = []
        for songs in aliases:
            alias_list = '\n'.join(songs.Alias)
            msg.append(f'ID：{songs.SongID}\n{alias_list}')
        yield event.plain_result(f'找到{len(aliases)}个相同别名的曲目：\n' + '\n======\n'.join(msg))
        return
    
    if len(aliases[0].Alias) == 1:
        yield event.plain_result('该曲目没有别名')
        return

    msg = f'该曲目有以下别名：\nID：{aliases[0].SongID}\n'
    msg += '\n'.join(aliases[0].Alias)
    yield event.plain_result(msg)


async def alias_switch_handler(event: AstrMessageEvent):
    """别名推送开关命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('别名推送开关功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
    # 移除后缀
    for suffix in ['别名推送', '别称推送']:
        if message_str.endswith(suffix):
            args = message_str[:-len(suffix)].strip().lower()
            break
    else:
        args = message_str.strip().lower()
    
    if args == '开启':
        msg = await alias.on(str(group_id))
    elif args == '关闭':
        msg = await alias.off(str(group_id))
    else:
        yield event.plain_result('命令格式错误，请使用「开启别名推送」或「关闭别名推送」')
        return
    
    yield event.plain_result(msg)


async def push_alias(push: PushAliasStatus, context=None):
    """
    推送别名通知
    context: astrbot 的 Context 对象，用于获取 bot client
    """
    if not context:
        log.warning('push_alias: context 未提供，无法发送别名推送消息')
        return
    
    # 获取 bot client
    bot_client = None
    try:
        from astrbot.api.event import filter
        platform = context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
        if platform:
            bot_client = platform.get_client()
    except Exception as e:
        log.error(f'获取 bot client 失败: {e}')
        log.error(traceback.format_exc())
        return
    
    if not bot_client:
        log.warning('无法获取 bot_client，跳过别名推送')
        return
    
    song_id = str(push.Status.SongID)
    alias_name = push.Status.ApplyAlias
    music = mai.total_list.by_id(song_id)
    
    if push.Type == 'Approved':
        text_msg = dedent(f'''\
            您申请的别名已通过审核
            =================
            {push.Status.Tag}：
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
            =================
            请使用指令「同意别名 {push.Status.Tag}」进行投票
        ''').strip()
        pic = await draw_music_info(music)
        chain = convert_message_segment_to_chain(pic)
        chain.insert(0, Comp.At(qq=push.Status.ApplyUID))
        # 直接使用字符串而不是 Comp.Plain，避免 JSON 序列化问题
        chain.insert(1, '\n' + text_msg)
        try:
            # 将消息链转换为 OneBot 格式
            onebot_chain = await convert_chain_to_onebot_format(chain)
            await bot_client.send_group_msg(group_id=push.Status.GroupID, message=onebot_chain)
        except Exception as e:
            log.error(f'发送别名审核通过消息失败: {e}')
        return
    
    if push.Type == 'Reject':
        text_msg = dedent(f'''\
            您申请的别名被拒绝
            =================
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
        ''').strip()
        pic = await draw_music_info(music)
        chain = convert_message_segment_to_chain(pic)
        chain.insert(0, Comp.At(qq=push.Status.ApplyUID))
        # 直接使用字符串而不是 Comp.Plain，避免 JSON 序列化问题
        chain.insert(1, '\n' + text_msg)
        try:
            # 将消息链转换为 OneBot 格式
            onebot_chain = await convert_chain_to_onebot_format(chain)
            await bot_client.send_group_msg(group_id=push.Status.GroupID, message=onebot_chain)
        except Exception as e:
            log.error(f'发送别名拒绝消息失败: {e}')
        return
    
    if not maiApi.config.maimaidxaliaspush:
        await mai.get_music_alias()
        return
    
    # 获取群组列表
    try:
        group_list = await bot_client.get_group_list()
        # 去重，避免重复推送别名
        group_ids = list({g['group_id'] for g in group_list})
    except Exception as e:
        log.error(f'获取群组列表失败: {e}')
        return
    
    message_chain = None
    if push.Type == 'Apply':
        text_msg = dedent(f'''\
            检测到新的别名申请
            =================
            {push.Status.Tag}：
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
            浏览{public_addr}查看详情
        ''').strip()
        pic = await draw_music_info(music)
        chain = convert_message_segment_to_chain(pic)
        # 直接使用字符串而不是 Comp.Plain，避免 JSON 序列化问题
        chain.insert(0, text_msg + '\n')
        message_chain = chain
    elif push.Type == 'End':
        text_msg = dedent(f'''\
            检测到新增别名
            =================
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
        ''').strip()
        pic = await draw_music_info(music)
        chain = convert_message_segment_to_chain(pic)
        # 直接使用字符串而不是 Comp.Plain，避免 JSON 序列化问题
        chain.insert(0, text_msg + '\n')
        message_chain = chain
    
    if not message_chain:
        return
    
    for gid in group_ids:
        if str(gid) in alias.push.disable:
            continue
        try:
            # 将消息链转换为 OneBot 格式
            onebot_message = await convert_chain_to_onebot_format(message_chain)
            await bot_client.send_group_msg(group_id=gid, message=onebot_message)
            await asyncio.sleep(5)
        except Exception as e:
            log.warning(f'发送别名推送消息到群 {gid} 失败: {e}')
            continue


async def ws_alias_server(context=None):
    """
    别名推送 WebSocket 服务器
    context: astrbot 的 Context 对象，用于获取 bot client
    """
    log.info('正在连接别名推送服务器')
    if maiApi.config.maimaidxaliasproxy:
        wsapi = 'proxy.yuzuchan.site/maimaidxaliases'
    else:
        wsapi = 'www.yuzuchan.moe/api/maimaidx'
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f'wss://{wsapi}/ws/{UUID}') as ws:
                    log.info('别名推送服务器连接成功')
                    while True:
                        data = await ws.receive_str()
                        # 处理 WebSocket 心跳消息
                        if data == 'Hello':
                            log.info('别名推送服务器正常运行')
                            continue
                        if data == 'ping' or data == 'pong':
                            # WebSocket 心跳消息，忽略
                            continue
                        if not data or not data.strip():
                            continue
                        try:
                            newdata = json.loads(data)
                            status = PushAliasStatus.model_validate(newdata)
                            await push_alias(status, context)
                        except json.JSONDecodeError as e:
                            # 如果不是已知的控制消息，才记录警告
                            if data not in ['ping', 'pong', 'Hello']:
                                log.warning(f'别名推送数据 JSON 解析失败: {e}, 数据: {data[:100] if len(data) > 100 else data}')
                            continue
                        except Exception as e:
                            log.warning(f'处理别名推送数据失败: {e}')
                            log.debug(traceback.format_exc())
                            continue
        except (aiohttp.WSServerHandshakeError, aiohttp.WebSocketError) as e:
            log.warning(f'连接断开或异常: {e}，将在 60 秒后重连')
            await asyncio.sleep(60)
            continue
        except Exception as e:
            log.error(f'别名推送服务器连接失败: {e}，将在 60 秒后重试')
            await asyncio.sleep(60)
            continue