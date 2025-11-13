import re
from re import Match
from textwrap import dedent
from typing import List, Tuple

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import SONGS_PER_PAGE, diffs, log
from ..command.mai_base import convert_message_segment_to_chain
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_model import AliasStatus
from ..libraries.maimaidx_music import guess, mai
from ..libraries.maimaidx_music_info import draw_music_info


def song_level(ds1: float, ds2: float) -> List[Tuple[str, str, float, str]]:
    """
    查询定数范围内的乐曲
    
    Params:
        `ds1`: 定数下限
        `ds2`: 定数上限
    Return:
        `result`: 查询结果
    """
    result: List[Tuple[str, str, float, str]] = []
    music_data = mai.total_list.filter(ds=(ds1, ds2))
    for music in sorted(music_data, key=lambda x: int(x.id)):
        if int(music.id) >= 100000:
            continue
        for i in music.diff:
            result.append((music.id, music.title, music.ds[i], diffs[i]))
    return result


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
    
    page = 1
    if not name:
        yield event.plain_result('请输入关键词')
        return
    
    result = mai.total_list.filter(title_search=name)
    if len(result) == 0:
        yield event.plain_result('没有找到这样的乐曲。\n※ 如果是别名请使用「xxx是什么歌」指令来查询哦。')
        return
    
    if len(result) == 1:
        pic = await draw_music_info(result.random(), event.get_sender_id())
        chain = convert_message_segment_to_chain(pic)
        yield event.chain_result(chain)
        return
        
    search_result = ''
    result.sort(key=lambda i: int(i.id))
    for i, music in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{music.id}」":<7} {music.title}\n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    img = text_to_image(search_result)
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
    result = song_level(float(ds1), float(ds2))
    if not result:
        yield event.plain_result('没有找到这样的乐曲。')
        return
    
    search_result = ''
    for i, _result in enumerate(result):
        id, title, ds, diff = _result
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{id}」":<7}{f"「{diff}」":<11}{f"「{ds}」"} {title}\n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    img = text_to_image(search_result)
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


async def search_bpm_handler(event: AstrMessageEvent):
    """bpm查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    if group_id:
        try:
            group_id_int = int(group_id)
            if group_id_int in guess.Group:
                yield event.plain_result('本群正在猜歌，不要作弊哦~')
                return
        except (ValueError, TypeError):
            pass
    
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
    
    search_result = ''
    page = max(min(page, len(result) // SONGS_PER_PAGE + 1), 1)
    result.sort(key=lambda x: int(x.basic_info.bpm))
    
    for i, m in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{m.id}」":<7}{f"「BPM {m.basic_info.bpm}」":<9} {m.title} \n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    img = text_to_image(search_result)
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


async def search_artist_handler(event: AstrMessageEvent):
    """曲师查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    if group_id:
        try:
            group_id_int = int(group_id)
            if group_id_int in guess.Group:
                yield event.plain_result('本群正在猜歌，不要作弊哦~')
                return
        except (ValueError, TypeError):
            pass
    
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
    
    search_result = ''
    page = max(min(page, len(result) // SONGS_PER_PAGE + 1), 1)
    for i, m in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            search_result += f'{f"「{m.id}」":<7}{f"「{m.basic_info.artist}」"} - {m.title}\n'
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    img = text_to_image(search_result)
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


async def search_charter_handler(event: AstrMessageEvent):
    """谱师查歌命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    group_id = event.message_obj.group_id
    if group_id:
        try:
            group_id_int = int(group_id)
            if group_id_int in guess.Group:
                yield event.plain_result('本群正在猜歌，不要作弊哦~')
                return
        except (ValueError, TypeError):
            pass
    
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
    
    search_result = ''
    page = max(min(page, len(result) // SONGS_PER_PAGE + 1), 1)
    for i, m in enumerate(result):
        if (page - 1) * SONGS_PER_PAGE <= i < page * SONGS_PER_PAGE:
            diff_charter = zip([diffs[d] for d in m.diff], [m.charts[d].charter for d in m.diff])
            diff_parts = [
                f"{f'「{d}」':<9}{f'「{c}」'}"
                for d, c in diff_charter
            ]
            diff_str = " ".join(diff_parts)
            line = f"{f'「{m.id}」':<7}{diff_str} {m.title}\n"
            search_result += line
    search_result += (
        f'第「{page}」页，'
        f'共「{len(result) // SONGS_PER_PAGE + 1}」页。'
        '请使用「id xxxxx」查询指定曲目。'
    )
    img = text_to_image(search_result)
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
        except AliasesNotFoundError:
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
        yield event.chain_result(chain)
        return
    
    if search_id := re.search(r'^id([0-9]*)$', name, re.IGNORECASE):
        music = mai.total_list.by_id(search_id.group(1))
        if music:
            pic = await draw_music_info(music, event.get_sender_id())
            chain = convert_message_segment_to_chain(pic)
            chain.insert(0, Comp.Plain('您要找的是不是：'))
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
        yield event.chain_result(chain)
        return
    elif len(result) < 50:
        msg = f'未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n'
        for music in sorted(result, key=lambda x: int(x.id)):
            msg += f'{f"「{music.id}」":<7} {music.title}\n'
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
    yield event.chain_result(chain)