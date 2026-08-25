import re
from textwrap import dedent
from typing import List

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import is_reply_enabled
from ..command.mai_base import append_theme_source_tip, convert_message_segment_to_chain
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_model import AliasStatus
from ..libraries.maimaidx_music import SongList, guess, mai
from ..libraries.maimaidx_music_info import draw_music_info
from ..libraries.maimaidx_song_list import draw_song_list


def song_level(ds1: float, ds2: float) -> SongList:
    """
    查询定数范围内的乐曲
    
    Params:
        `ds1`: 定数下限
        `ds2`: 定数上限
    Return:
        `result`: 查询结果
    """
    music_data = mai.total_list.filter(ds=(ds1, ds2))
    return SongList(
        music
        for music in sorted(music_data, key=lambda song: song.song_id)
        if music.song_id < 100000
    )


def song_list_chain(event: AstrMessageEvent, songs, page: int, title: str):
    image = draw_song_list(songs, page, title=title)
    chain = convert_message_segment_to_chain(image)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    return chain


async def search_music_handler(event: AstrMessageEvent):
    """查歌/search 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['查歌', 'search']:
        if message_str.lower().startswith(prefix.lower()):
            name = message_str[len(prefix):].strip()
            break
    else:
        name = message_str
    
    if not name:
        yield event.plain_result('请输入关键词')
        return

    page = 1
    parts = name.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        page = int(parts[-1])
        name = ' '.join(parts[:-1])
    
    result = mai.total_list.filter(title_search=name)
    if len(result) == 0:
        yield event.plain_result('没有找到这样的乐曲。\n※ 如果是别名请使用「xxx是什么歌」指令来查询哦。')
        return
    
    if len(result) == 1:
        pic = await draw_music_info(result.random(), event.get_sender_id())
        chain = convert_message_segment_to_chain(pic)
        if is_reply_enabled():
            chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
        yield event.chain_result(chain)
        return
        
    result.sort(key=lambda song: song.song_id)
    yield event.chain_result(song_list_chain(event, result, page, '曲目搜索'))


async def search_base_handler(event: AstrMessageEvent):
    """定数查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['定数查歌', 'search base']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    if len(args) > 3 or len(args) == 0:
        yield event.plain_result(dedent('''
                命令格式：
                定数查歌 「定数」「页数」
                定数查歌 「定数下限」「定数上限」「页数」
            ''').strip())
        return
    
    page = 1
    if len(args) == 1:
        ds1, ds2 = args[0], args[0]
    elif len(args) == 2:
        if '.' in args[1]:
            ds1, ds2 = args
        else:
            ds1, ds2 = args[0], args[0]
            page = args[1]
    else:
        ds1, ds2, page = args
    page = int(page)
    # 处理 "13+" 格式：13.6 ~ 13.9
    if ds1.endswith('+') and ds1[:-1].replace('.', '').isdigit():
        ds1 = ds1[:-1] + '.6'
    if ds2.endswith('+') and ds2[:-1].replace('.', '').isdigit():
        ds2 = ds2[:-1] + '.9'
    try:
        result = song_level(float(ds1), float(ds2))
    except ValueError:
        yield event.plain_result('命令格式错误，请使用纯数字定数，如 13 或 13.7')
        return
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    yield event.chain_result(song_list_chain(event, result, page, '定数搜索'))


async def search_bpm_handler(event: AstrMessageEvent):
    """bpm查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    # group_id 在 AstrBotMessage 中已经是字符串，直接使用
    if group_id and group_id in guess.Group:
        yield event.plain_result('本群正在猜歌，不要作弊哦~')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['bpm查歌', 'search bpm']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args = args_str.split()
    page = 1
    if len(args) == 1:
        result = mai.total_list.filter(bpm=int(args[0]))
    elif len(args) == 2:
        if (bpm := int(args[0])) > int(args[1]):
            page = int(args[1])
            result = mai.total_list.filter(bpm=bpm)
        else:
            result = mai.total_list.filter(bpm=(bpm, int(args[1])))
    elif len(args) == 3:
        result = mai.total_list.filter(bpm=(int(args[0]), int(args[1])))
        page = int(args[2])
    else:
        yield event.plain_result('命令格式：\nbpm查歌 「bpm」\nbpm查歌 「bpm下限」「bpm上限」「页数」')
        return
    
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    result.sort(key=lambda song: song.bpm)
    yield event.chain_result(song_list_chain(event, result, page, 'BPM 搜索'))


async def search_artist_handler(event: AstrMessageEvent):
    """曲师查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    # group_id 在 AstrBotMessage 中已经是字符串，直接使用
    if group_id and group_id in guess.Group:
        yield event.plain_result('本群正在猜歌，不要作弊哦~')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['曲师查歌', 'search artist']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    page = 1
    if len(args) == 1:
        name: str = args[0]
    elif len(args) == 2:
        name: str = args[0]
        if args[1].isdigit():
            page = int(args[1])
        else:
            yield event.plain_result('命令格式：\n曲师查歌「曲师名称」「页数」')
            return
    else:
        yield event.plain_result('命令格式：\n曲师查歌「曲师名称」「页数」')
        return
    
    result = mai.total_list.filter(artist_search=name)
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    yield event.chain_result(song_list_chain(event, result, page, '曲师搜索'))


async def search_charter_handler(event: AstrMessageEvent):
    """谱师查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    # group_id 在 AstrBotMessage 中已经是字符串，直接使用
    if group_id and group_id in guess.Group:
        yield event.plain_result('本群正在猜歌，不要作弊哦~')
        return
    
    message_str = event.message_str.strip()
    # 移除命令前缀
    for prefix in ['谱师查歌', 'search charter']:
        if message_str.lower().startswith(prefix.lower()):
            args_str = message_str[len(prefix):].strip()
            break
    else:
        args_str = message_str
    
    args: List[str] = args_str.split()
    page = 1
    if len(args) == 1:
        name: str = args[0]
    elif len(args) == 2:
        name: str = args[0]
        if args[1].isdigit():
            page = int(args[1])
        else:
            yield event.plain_result('命令格式：\n谱师查歌「谱师名称」「页数」')
            return
    else:
        yield event.plain_result('命令格式：\n谱师查歌「谱师名称」「页数」')
        return
    
    result = mai.total_list.filter(charter_search=name)
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    yield event.chain_result(song_list_chain(event, result, page, '谱师搜索'))


async def search_alias_song_handler(event: AstrMessageEvent):
    """是什么歌/是啥歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip().lower()
    # 移除后缀
    for suffix in ['是什么歌', '是啥歌']:
        if message_str.endswith(suffix):
            name = message_str[:-len(suffix)].strip()
            break
    else:
        name = message_str
    
    error_msg = (
        f'未找到别名为「{name}」的歌曲\n'
        '※ 可以使用「添加别名」指令给该乐曲添加别名\n'
        '※ 如果是歌名的一部分，请使用「查歌」指令查询哦。'
    )
    
    # 别名
    if not hasattr(mai, 'total_alias_list') or not mai.total_alias_list:
        alias_data = None
    else:
        alias_data = mai.total_alias_list.by_alias(name)
    
    if not alias_data:
        try:
            obj = await maiApi.get_songs(name)
            if obj:
                if type(obj[0]) == AliasStatus:
                    msg = f'未找到别名为「{name}」的歌曲，但找到与此相同别名的投票：\n'
                    for _s in obj:
                        msg += f'- {_s.Tag}\n    ID {_s.SongID}: {name}\n'
                    msg += f'※ 可以使用指令「同意别名 {_s.Tag}」进行投票'
                    yield event.plain_result(msg.strip())
                    return
                else:
                    alias_data = obj
        except Exception:
            pass
    
    if alias_data:
        if len(alias_data) != 1:
            msg = f'找到{len(alias_data)}个相同别名的曲目：\n'
            for songs in alias_data:
                msg += f'{songs.SongID}：{songs.Name}\n'
            msg += '※ 请使用「id xxxxx」查询指定曲目'
            yield event.plain_result(msg.strip())
            return
        else:
            music = mai.total_list.by_id(str(alias_data[0].SongID))
            if music:
                pic = await draw_music_info(music, event.get_sender_id())
                chain = convert_message_segment_to_chain(pic)
                chain.insert(0, Comp.Plain('您要找的是不是：'))
                if is_reply_enabled():
                    chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
                yield event.chain_result(chain)
                return
            else:
                yield event.plain_result(error_msg)
                return
    
    # id
    if name.isdigit() and (music := mai.total_list.by_id(name)):
        pic = await draw_music_info(music, event.get_sender_id())
        chain = convert_message_segment_to_chain(pic)
        chain.insert(0, Comp.Plain('您要找的是不是：'))
        if is_reply_enabled():
            chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
        yield event.chain_result(chain)
        return
    
    if search_id := re.search(r'^id([0-9]*)$', name, re.IGNORECASE):
        music = mai.total_list.by_id(search_id.group(1))
        if music:
            pic = await draw_music_info(music, event.get_sender_id())
            chain = convert_message_segment_to_chain(pic)
            chain.insert(0, Comp.Plain('您要找的是不是：'))
            if is_reply_enabled():
                chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
            yield event.chain_result(chain)
            return
    
    # 标题
    result = mai.total_list.filter(title_search=name)
    if len(result) == 0:
        yield event.plain_result(error_msg)
        return
    elif len(result) == 1:
        pic = await draw_music_info(result.random(), event.get_sender_id())
        chain = convert_message_segment_to_chain(pic)
        chain.insert(0, Comp.Plain('您要找的是不是：'))
        if is_reply_enabled():
            chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
        yield event.chain_result(chain)
        return
    elif len(result) < 50:
        msg = f'未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n'
        for music in sorted(result, key=lambda song: song.song_id):
            msg += f'{f"「{music.song_id}」":<7} {music.song_name}\n'
        msg += '请使用「id xxxxx」查询指定曲目。'
        yield event.plain_result(msg.strip())
        return
    else:
        yield event.plain_result(f'结果过多「{len(result)}」条，请缩小查询范围。')
        return


async def query_chart_handler(event: AstrMessageEvent):
    """id 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = event.message_str.strip()
    # 匹配 id xxxxx 格式
    match = re.match(r'^id\s?([0-9]+)$', message_str, re.IGNORECASE)
    if not match:
        return  # 不匹配则不处理
    
    id = match.group(1)
    music = mai.total_list.by_id(id)
    if not music:
        yield event.plain_result(f'未找到ID为「{id}」的乐曲')
        return
    
    pic = await draw_music_info(music, event.get_sender_id())
    chain = convert_message_segment_to_chain(pic)
    append_theme_source_tip(chain, pic)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)
