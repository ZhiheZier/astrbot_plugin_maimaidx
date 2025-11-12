import asyncio
import json
import re
import tempfile
import traceback
from re import Match
from textwrap import dedent
from typing import List

import aiohttp
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import SONGS_PER_PAGE, UUID, log, public_addr
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import ServerError
from ..libraries.maimaidx_model import Alias, PushAliasStatus
from ..libraries.maimaidx_music import alias, mai, update_local_alias
from ..libraries.maimaidx_music_info import draw_music_info
from .mai_base import convert_message_segment_to_chain


def _text_to_image_chain(text: str):
    """将文本转换为图片并返回消息链"""
    img = text_to_image(text)
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    img.save(temp_file.name, 'PNG')
    temp_file.close()
    return [Comp.Image.fromFileSystem(temp_file.name)]


async def update_alias_handler(event: AstrMessageEvent, superusers: list = None):
    """更新别名库"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    try:
        await mai.get_music_alias()
        log.info('手动更新别名库成功')
        yield event.plain_result('手动更新别名库成功')
    except Exception as e:
        log.error(f'手动更新别名库失败: {e}')
        yield event.plain_result('手动更新别名库失败')


async def alias_switch_on_handler(event: AstrMessageEvent, superusers: list = None):
    """全局开启别名推送"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    try:
        client = event.bot
        response = await client.api.call_action("get_group_list", {"no_cache": False})
        group_id = [g['group_id'] for g in response]
        await alias.alias_global_change(True, group_id)
        yield event.plain_result('已全局开启maimai别名推送')
    except Exception as e:
        log.error(f'全局开启别名推送失败: {e}')
        yield event.plain_result('全局开启别名推送失败')


async def alias_switch_off_handler(event: AstrMessageEvent, superusers: list = None):
    """全局关闭别名推送"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    try:
        client = event.bot
        response = await client.api.call_action("get_group_list", {"no_cache": False})
        group_id = [g['group_id'] for g in response]
        await alias.alias_global_change(False, group_id)
        yield event.plain_result('已全局关闭maimai别名推送')
    except Exception as e:
        log.error(f'全局关闭别名推送失败: {e}')
        yield event.plain_result('全局关闭别名推送失败')


async def alias_local_apply_handler(event: AstrMessageEvent):
    """添加本地别名/添加本地别称"""
    message_str = event.message_str.strip()
    # 移除命令前缀
    args_str = message_str.replace('添加本地别名', '').replace('添加本地别称', '').strip()
    args: List[str] = args_str.split()
    
    if len(args) != 2:
        yield event.plain_result('参数错误')
        return
    
    song_id, alias_name = args
    if not mai.total_list.by_id(song_id):
        yield event.plain_result(f'未找到ID为「{song_id}」的曲目')
        return
    
    server_exist = await maiApi.get_songs_alias(song_id)
    if isinstance(server_exist, Alias) and alias_name.lower() in server_exist.Alias:
        yield event.plain_result(f'该曲目的别名「{alias_name}」已存在别名服务器')
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
    """添加别名/增加别名/增添别名/添加别称"""
    try:
        message_str = event.message_str.strip()
        # 移除命令前缀
        for prefix in ['添加别名', '增加别名', '增添别名', '添加别称']:
            message_str = message_str.replace(prefix, '', 1).strip()
        
        args: List[str] = message_str.split()
        if len(args) < 2:
            yield event.plain_result('参数错误')
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
        
        group_id = event.message_obj.group_id  # 私聊时为空字符串
        msg = await maiApi.post_alias(song_id, alias_name, event.get_sender_id(), group_id)
        yield event.plain_result(msg)
    except (ServerError, ValueError) as e:
        log.error(traceback.format_exc())
        yield event.plain_result(str(e))


async def alias_agree_handler(event: AstrMessageEvent):
    """同意别名/同意别称"""
    try:
        message_str = event.message_str.strip()
        # 移除命令前缀
        tag: str = message_str.replace('同意别名', '').replace('同意别称', '').strip().upper()
        
        status = await maiApi.post_agree_user(tag, event.get_sender_id())
        yield event.plain_result(status)
    except ValueError as e:
        yield event.plain_result(str(e))


async def alias_status_handler(event: AstrMessageEvent):
    """当前投票/当前别名投票/当前别称投票"""
    try:
        message_str = event.message_str.strip()
        # 移除命令前缀
        for prefix in ['当前投票', '当前别名投票', '当前别称投票']:
            message_str = message_str.replace(prefix, '', 1).strip()
        
        args: str = message_str
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
                    ''')
                )
        result.append(f'第「{page}」页，共「{len(status) // SONGS_PER_PAGE + 1}」页')
        
        chain = _text_to_image_chain('\n'.join(result))
        yield event.chain_result(chain)
    except (ServerError, ValueError) as e:
        log.error(traceback.format_exc())
        yield event.plain_result(str(e))


async def alias_song_handler(event: AstrMessageEvent):
    """查询歌曲别名"""
    message_str = event.message_str.strip()
    match = re.match(r'^(id)?\s?(.+)\s?有什么别[名称]$', message_str, re.IGNORECASE)
    
    if not match:
        return
    
    findid = bool(match.group(1))
    name = match.group(2)
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
    """开启别名推送/关闭别名推送"""
    message_str = event.message_str.strip().lower()
    args = message_str.replace('开启别名推送', '').replace('关闭别名推送', '').replace('开启别称推送', '').replace('关闭别称推送', '').strip()
    
    group_id = event.message_obj.group_id if hasattr(event.message_obj, 'group_id') else None
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    if '开启' in message_str:
        msg = await alias.on(group_id)
    elif '关闭' in message_str:
        msg = await alias.off(group_id)
    else:
        yield event.plain_result('指令错误')
        return
    
    yield event.plain_result(msg)


async def push_alias(push: PushAliasStatus, bot_client):
    """
    推送别名消息
    注意：这个函数需要 bot_client 参数，因为无法使用 get_bot()
    """
    song_id = str(push.Status.SongID)
    alias_name = push.Status.ApplyAlias
    music = mai.total_list.by_id(song_id)
    
    if push.Type == 'Approved':
        result_msg = await draw_music_info(music)
        chain = convert_message_segment_to_chain(result_msg)
        chain.insert(0, Comp.At(qq=push.Status.ApplyUID))
        chain.insert(1, Comp.Plain('\n' + dedent(f'''\
            您申请的别名已通过审核
            =================
            {push.Status.Tag}：
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
            =================
            请使用指令「同意别名 {push.Status.Tag}」进行投票
        ''').strip()))
        
        await bot_client.api.call_action("send_group_msg", {
            "group_id": push.Status.GroupID,
            "message": chain
        })
        return
    
    if push.Type == 'Reject':
        result_msg = await draw_music_info(music)
        chain = convert_message_segment_to_chain(result_msg)
        chain.insert(0, Comp.At(qq=push.Status.ApplyUID))
        chain.insert(1, Comp.Plain('\n' + dedent(f'''\
            您申请的别名被拒绝
            =================
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
        ''').strip()))
        
        await bot_client.api.call_action("send_group_msg", {
            "group_id": push.Status.GroupID,
            "message": chain
        })
        return
    
    if not maiApi.config.maimaidxaliaspush:
        await mai.get_music_alias()
        return
    
    response = await bot_client.api.call_action("get_group_list", {"no_cache": False})
    group_list = response
    
    message_chain = None
    if push.Type == 'Apply':
        result_msg = await draw_music_info(music)
        chain = convert_message_segment_to_chain(result_msg)
        chain.insert(0, Comp.Plain(dedent(f'''\
            检测到新的别名申请
            =================
            {push.Status.Tag}：
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
            浏览{public_addr}查看详情
        ''').strip()))
        message_chain = chain
    
    if push.Type == 'End':
        result_msg = await draw_music_info(music)
        chain = convert_message_segment_to_chain(result_msg)
        chain.insert(0, Comp.Plain(dedent(f'''\
            检测到新增别名
            =================
            ID：{song_id}
            标题：{music.title}
            别名：{alias_name}
        ''').strip()))
        message_chain = chain
    
    if message_chain:
        for group in group_list:
            gid: int = group['group_id']
            if gid in alias.push.disable:
                continue
            try:
                await bot_client.api.call_action("send_group_msg", {
                    "group_id": gid,
                    "message": message_chain
                })
                await asyncio.sleep(5)
            except Exception:
                continue


async def ws_alias_server(bot_client=None):
    """
    别名推送服务器连接
    注意：需要传入 bot_client，因为无法使用 get_bot()
    """
    log.info('正在连接别名推送服务器')
    if maiApi.config.maimaidxaliasproxy:
        wsapi = 'proxy.yuzuchan.xyz/maimaidxaliases'
    else:
        wsapi = 'www.yuzuchan.moe/api/maimaidx'
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f'wss://{wsapi}/ws/{UUID}') as ws:
                    try:
                        log.info('别名推送服务器连接成功')
                        while True:
                            data = await ws.receive_str()
                            if data == 'Hello':
                                log.info('别名推送服务器正常运行')
                            try:
                                newdata = json.loads(data)
                                status = PushAliasStatus.model_validate(newdata)
                                if bot_client:
                                    await push_alias(status, bot_client)
                                else:
                                    # 如果没有 bot_client，记录错误
                                    log.warning('别名推送需要 bot_client，但未提供')
                            except Exception:
                                continue
                    except aiohttp.WSServerHandshakeError:
                        log.warning('别名推送服务器已断开连接，将在1分钟后重新尝试连接')
                        await asyncio.sleep(60)
                    except aiohttp.WebSocketError:
                        log.error('别名推送服务器连接失败，将在1分钟后重试')
                        await asyncio.sleep(60)
                        log.info('正在尝试重新连接别名推送服务器')
        except Exception as e:
            log.error(f'别名推送服务器连接失败，将在1分钟后重试: {e}')
            await asyncio.sleep(60)
            log.info('正在尝试重新连接别名推送服务器')
