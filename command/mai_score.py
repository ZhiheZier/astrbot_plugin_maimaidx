import re
import tempfile
from textwrap import dedent
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import log
from ..libraries.image import image_to_base64, text_to_image
from ..libraries.maimai_best_50 import generate
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_play_data
from ..libraries.maimaidx_player_score import music_global_data
from .mai_base import convert_message_segment_to_chain


# extract_at_qqid 函数已移至 mai_base.py，这里从 mai_base 导入
from .mai_base import extract_at_qqid


async def best50_handler(event: AstrMessageEvent):
    """b50/B50 - 查询最佳50首"""
    message_str = event.message_str.strip()
    # 移除命令前缀
    args = message_str.replace('b50', '').replace('B50', '').strip()
    username = args if args else None
    
    qqid = extract_at_qqid(event)
    
    result = await generate(qqid, username)
    chain = convert_message_segment_to_chain(result)
    yield event.chain_result(chain)


async def minfo_handler(event: AstrMessageEvent):
    """minfo/Minfo/MINFO/info/Info/INFO - 查询谱面信息"""
    message_str = event.message_str.strip().lower()
    # 移除命令前缀
    args = message_str.replace('minfo', '').replace('info', '').strip()
    
    if not args:
        yield event.plain_result('请输入曲目id或曲名')
        return
    
    qqid = extract_at_qqid(event)
    
    # 查找曲目
    if mai.total_list.by_id(args):
        songs = args
    elif by_t := mai.total_list.by_title(args):
        songs = by_t.id
    else:
        alias = mai.total_alias_list.by_alias(args)
        if not alias:
            yield event.plain_result('未找到曲目')
            return
        elif len(alias) != 1:
            msg = f'找到相同别名的曲目，请使用以下ID查询：\n'
            for songs in alias:
                msg += f'{songs.SongID}：{songs.Name}\n'
            yield event.plain_result(msg.strip())
            return
        else:
            songs = str(alias[0].SongID)
    
    pic = await draw_music_play_data(qqid, songs)
    chain = convert_message_segment_to_chain(pic)
    yield event.chain_result(chain)


async def ginfo_handler(event: AstrMessageEvent):
    """ginfo/Ginfo/GINFO - 查询全局统计信息"""
    message_str = event.message_str.strip().lower()
    # 移除命令前缀
    args = message_str.replace('ginfo', '').replace('ginfo', '').strip()
    
    if not args:
        yield event.plain_result('请输入曲目id或曲名')
        return
    
    # 解析难度
    level_index = 3  # 默认 Master
    if args[0] in '绿黄红紫白':
        level_index = '绿黄红紫白'.index(args[0])
        args = args[1:].strip()
        if not args:
            yield event.plain_result('请输入曲目id或曲名')
            return
    
    # 查找曲目
    if mai.total_list.by_id(args):
        id = args
    elif by_t := mai.total_list.by_title(args):
        id = by_t.id
    else:
        alias = mai.total_alias_list.by_alias(args)
        if not alias:
            yield event.plain_result('未找到曲目')
            return
        elif len(alias) != 1:
            msg = f'找到相同别名的曲目，请使用以下ID查询：\n'
            for songs in alias:
                msg += f'{songs.SongID}：{songs.Name}\n'
            yield event.plain_result(msg.strip())
            return
        else:
            id = str(alias[0].SongID)
    
    music = mai.total_list.by_id(id)
    if not music.stats:
        yield event.plain_result('该乐曲还没有统计信息')
        return
    if len(music.ds) == 4 and level_index == 4:
        yield event.plain_result('该乐曲没有这个等级')
        return
    if not music.stats[level_index]:
        yield event.plain_result('该等级没有统计信息')
        return
    
    stats = music.stats[level_index]
    info = dedent(f'''\
        游玩次数：{round(stats.cnt)}
        拟合难度：{stats.fit_diff:.2f}
        平均达成率：{stats.avg:.2f}%
        平均 DX 分数：{stats.avg_dx:.1f}
        谱面成绩标准差：{stats.std_dev:.2f}''')
    
    result = await music_global_data(music, level_index)
    chain = convert_message_segment_to_chain(result)
    chain.append(Comp.Plain(info))
    yield event.chain_result(chain)


async def score_handler(event: AstrMessageEvent):
    """分数线 - 查询分数线"""
    message_str = event.message_str.strip()
    # 移除命令前缀
    args = message_str.replace('分数线', '').strip()
    
    if args == '帮助':
        msg = dedent('''\
            此功能为查找某首歌分数线设计。
            命令格式：分数线「难度+歌曲id」「分数线」
            例如：分数线 紫799 100
            命令将返回分数线允许的「TAP」「GREAT」容错，
            以及「BREAK」50落等价的「TAP」「GREAT」数。
            以下为「TAP」「GREAT」的对应表：
                    GREAT / GOOD / MISS
            TAP         1 / 2.5  / 5
            HOLD        2 / 5    / 10
            SLIDE       3 / 7.5  / 15
            TOUCH       1 / 2.5  / 5
            BREAK       5 / 12.5 / 25 (外加200落)
        ''').strip()
        
        # 将文本转换为图片
        img = text_to_image(msg)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        img.save(temp_file.name, 'PNG')
        temp_file.close()
        
        chain = [Comp.Image.fromFileSystem(temp_file.name)]
        yield event.chain_result(chain)
        return
    
    try:
        pro = args.split()
        result = re.search(r'([绿黄红紫白])\s?([0-9]+)', args)
        level_labels = ['绿', '黄', '红', '紫', '白']
        level_labels2 = ['Basic', 'Advanced', 'Expert', 'Master', 'Re:MASTER']
        level_index = level_labels.index(result.group(1))
        chart_id = result.group(2)
        line = float(pro[-1])
        music = mai.total_list.by_id(chart_id)
        chart = music.charts[level_index]
        tap = int(chart.notes.tap)
        slide = int(chart.notes.slide)
        hold = int(chart.notes.hold)
        touch = int(chart.notes.touch) if len(chart.notes) == 5 else 0
        brk = int(chart.notes.brk)
        total_score = tap * 500 + slide * 1500 + hold * 1000 + touch * 500 + brk * 2500
        break_bonus = 0.01 / brk
        break_50_reduce = total_score * break_bonus / 4
        reduce = 101 - line
        if reduce <= 0 or reduce >= 101:
            raise ValueError
        msg = dedent(f'''\
            {music.title}「{level_labels2[level_index]}」
            分数线「{line}%」
            允许的最多「TAP」「GREAT」数量为 
            「{(total_score * reduce / 10000):.2f}」(每个-{10000 / total_score:.4f}%),
            「BREAK」50落(一共「{brk}」个)
            等价于「{(break_50_reduce / 100):.3f}」个「TAP」「GREAT」(-{break_50_reduce / total_score * 100:.4f}%)
        ''').strip()
        yield event.plain_result(msg)
    except (AttributeError, ValueError) as e:
        log.exception(e)
        yield event.plain_result('格式错误，输入"分数线 帮助"以查看帮助信息')
