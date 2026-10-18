import re
from textwrap import dedent
from typing import Any, List
import math
from fractions import Fraction

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import ACHIEVEMENT_LIST, RANK_SP, log, is_reply_enabled
from .mai_tolerance import parse_chart_request, resolve_song_chart
from ..command.mai_base import (
    append_theme_source_tip,
    convert_message_segment_to_chain,
    extract_at_qqid,
    get_plain_message_text,
    parse_player_target,
)
from ..libraries.maimaidx_tolerance import draw_score_line_table
from ..libraries.maimai_best_50 import generate
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_play_data
from ..libraries.maimaidx_player_score import music_global_data


ACHIEVEMENT_COMMAND_PATTERNS = {
    'under_s': r'^/?(?i:越级b50|越级50|a50)',
    'near': r'^/?(?i:寸b50|寸50|c50)',
    'lock': r'^/?(?i:锁血b50|锁血50|s50)',
}
DIFFICULTY_B50_COMMAND_PATTERN = r'^/?[白紫红黄绿]谱(?i:b50)'
ALL_SONGS_B50_COMMAND_PATTERN = r'^/?(?i:全曲b50|allb50|ab50)'


async def best50_handler(
    event: AstrMessageEvent,
    all_perfect: bool = False,
    min_dx_star: int | None = None,
    fitted: bool = False,
    all_perfect_plus: bool = False,
    achievement_mode: str | None = None,
    difficulty_index: int | None = None,
    all_songs: bool = False,
):
    """普通、筛选及全曲 B50 命令处理。"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    qqid = event.get_sender_id()
    message_str = get_plain_message_text(event)
    # 移除命令前缀
    # 检查是否有 @ 消息
    at_qqid = extract_at_qqid(event)
    if not at_qqid:
        if all_songs:
            cleaned = re.sub(
                ALL_SONGS_B50_COMMAND_PATTERN,
                '',
                message_str.strip(),
            )
        elif achievement_mode is not None:
            if achievement_mode not in ACHIEVEMENT_COMMAND_PATTERNS:
                raise ValueError(f'未知的达成率筛选模式：{achievement_mode}')
            cleaned = re.sub(
                ACHIEVEMENT_COMMAND_PATTERNS[achievement_mode],
                '',
                message_str.strip(),
            )
        elif difficulty_index is not None:
            if not 0 <= difficulty_index <= 4:
                raise ValueError('谱面难度索引必须在 0 到 4 之间')
            cleaned = re.sub(
                DIFFICULTY_B50_COMMAND_PATTERN,
                '',
                message_str.strip(),
            )
        elif all_perfect_plus:
            cleaned = re.sub(
                r'^/?(?i:ap\+50|理论b50)', '', message_str.strip()
            )
        elif fitted:
            cleaned = re.sub(
                r'^/?(?i:拟合b50|f50)', '', message_str.strip()
            )
        elif min_dx_star is not None:
            cleaned = re.sub(
                r'^/?[一二三四五]星(?i:b50)', '', message_str.strip()
            )
        else:
            cleaned = re.sub(
                r'(?i)^/?(?:ap50|b50)', '', message_str.strip()
            )
        qqid, username = parse_player_target(event, cleaned)
    else:
        username = ''
        qqid = at_qqid
    
    result = await generate(
        qqid,
        username,
        all_perfect=all_perfect,
        min_dx_star=min_dx_star,
        fitted=fitted,
        all_perfect_plus=all_perfect_plus,
        achievement_mode=achievement_mode,
        difficulty_index=difficulty_index,
        all_songs=all_songs,
    )
    chain: List[Any] = convert_message_segment_to_chain(result)
    append_theme_source_tip(chain, result)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)


async def gold_analysis_handler(event: AstrMessageEvent):
    """含金量分析：展示当前 B50 中拟合定数高于官方定数最多的谱面。"""
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return

    message_str = get_plain_message_text(event)
    cleaned = re.sub(r'^/?(?:含金量分析|含金量)', '', message_str)
    qqid, username = parse_player_target(event, cleaned)

    from ..libraries.maimaidx_analysis import generate_gold_analysis

    result = await generate_gold_analysis(qqid=qqid, username=username)
    chain: List[Any] = convert_message_segment_to_chain(result)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)


async def water_analysis_handler(event: AstrMessageEvent):
    """水分分析：展示当前 B50 中官方定数高于拟合定数最多的谱面。"""
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return

    message_str = get_plain_message_text(event)
    cleaned = re.sub(r'^/?(?:水分分析|含水量分析|含水量)', '', message_str)
    qqid, username = parse_player_target(event, cleaned)

    from ..libraries.maimaidx_analysis import generate_water_analysis

    result = await generate_water_analysis(qqid=qqid, username=username)
    chain: List[Any] = convert_message_segment_to_chain(result)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)
    
    
def _minfo_songs(query: str):
    if query.isascii() and query.isdigit():
        song = mai.total_list.by_id(query)
        return [song] if song else []
    songs = [song for song in mai.total_list if song.song_name.casefold() == query.casefold()]
    if songs:
        return songs
    aliases = getattr(mai, 'total_alias_list', None)
    matches = aliases.by_alias(query.lower()) if aliases else []
    return list({song.song_id: song for item in (matches or [])
                 if (song := mai.total_list.by_id(item.SongID)) is not None}.values())


def parse_minfo_target(argument: str):
    # Resolve the complete title first so spaces and literal pipes remain meaningful.
    songs = _minfo_songs(argument)
    if songs:
        return songs, ''
    if '|' in argument:
        song_query, player = map(str.strip, argument.rsplit('|', 1))
        return _minfo_songs(song_query), player
    parts = argument.rsplit(maxsplit=1)
    if len(parts) == 2:
        return _minfo_songs(parts[0]), parts[1]
    return [], ''


async def minfo_handler(event: AstrMessageEvent):
    """minfo/info 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = get_plain_message_text(event).removeprefix('/')
    # 移除命令前缀
    for prefix in ['minfo', 'info']:
        if message_str.lower().startswith(prefix):
            args = message_str[len(prefix):].strip()
            break
    else:
        args = message_str
    
    if not args:
        yield event.plain_result('请输入曲目id或曲名')
        return

    songs, player = parse_minfo_target(args)
    if not songs:
        yield event.plain_result('未找到曲目；格式：minfo 歌曲 [玩家]，有歧义时使用 minfo 歌曲 | 玩家')
        return
    if len(songs) > 1:
        candidates = '\n'.join(f'{song.song_id}：{song.song_name}' for song in songs)
        yield event.plain_result(f'找到多个相同名称或别名的曲目，请使用歌曲ID：\n{candidates}')
        return
    qqid, username = parse_player_target(event, player)
    pic = await draw_music_play_data(qqid, str(songs[0].song_id), username=username or None)
    chain = convert_message_segment_to_chain(pic)
    append_theme_source_tip(chain, pic)
    if is_reply_enabled():
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    yield event.chain_result(chain)


async def ginfo_handler(event: AstrMessageEvent):
    """ginfo 命令处理"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
    message_str = get_plain_message_text(event).lower()
    # 移除命令前缀
    for prefix in ['ginfo']:
        if message_str.startswith(prefix):
            args = message_str[len(prefix):].strip()
            break
    else:
        args = message_str
    
    if not args:
        yield event.plain_result('请输入曲目id或曲名')
        return
    
    if args[0] not in '绿黄红紫白':
        level_index = 3
    else:
        level_index = '绿黄红紫白'.index(args[0])
        args = args[1:].strip()
        if not args:
            yield event.plain_result('请输入曲目id或曲名')
            return
    
    if mai.total_list.by_id(args):
        id = args
    elif by_t := mai.total_list.by_title(args):
        id = str(by_t.song_id)
    else:
        if not hasattr(mai, 'total_alias_list') or not mai.total_alias_list:
            yield event.plain_result('别名数据未加载，请稍后再试或联系管理员')
            return
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
    difficulty = mai.get_difficulty(id, level_index)
    if difficulty is None:
        yield event.plain_result('该乐曲没有这个等级')
        return
    if not difficulty.stats:
        yield event.plain_result('该等级没有统计信息')
        return
    stats = difficulty.stats
    info = dedent(f'''\
        游玩次数：{round(stats.cnt)}
        拟合难度：{stats.fit_diff:.2f}
        平均达成率：{stats.avg:.2f}%
        平均 DX 分数：{stats.avg_dx:.1f}
        谱面成绩标准差：{stats.std_dev:.2f}''')
    pic = await music_global_data(music, level_index)
    chain = convert_message_segment_to_chain(pic)
    # 添加引用回复
    if not chain or not isinstance(chain[0], Comp.Reply):
        chain.insert(0, Comp.Reply(id=event.message_obj.message_id))
    chain.append(Comp.Plain(info))
    yield event.chain_result(chain)
    
    
def parse_score_line_request(text: str) -> tuple[int, str, Fraction] | None:
    request = parse_chart_request(text, '分数线')
    if request is None:
        return None
    level_index, payload = request
    parts = payload.rsplit(maxsplit=1)
    if len(parts) != 2:
        return None
    query, target = parts
    ranks = dict(zip(RANK_SP, (0, *ACHIEVEMENT_LIST)))
    rank = target.lower().replace('+', 'p')
    if rank in ranks:
        line = Fraction(str(ranks[rank]))
    elif re.fullmatch(r'[0-9]+(?:\.[0-9]+)?%?', target):
        line = Fraction(target.removesuffix('%'))
    else:
        return None
    return level_index, query, line


async def score_handler(event: AstrMessageEvent):
    """查询各音符类型、判定等级的独立容错上限。"""
    text = get_plain_message_text(event)
    help_text = (
        '格式：分数线 难度 歌曲ID/别名/名称 分数或等级\n'
        '例如：分数线 紫799 100.5、分数线 紫 799 sss+、分数线 Master 歌名 ss\n'
        '难度与歌曲之间可省略空格，目标分数需用空格分隔。\n'
        '图片横向为音符类型，纵向为 GREAT、GOOD、MISS，个数为其余音符均为 CP 时的独立上限。'
    )
    if text.strip().lstrip('/') in ('分数线 帮助', '分数线帮助'):
        yield event.plain_result(help_text)
        return
    request = parse_score_line_request(text)
    if request is None:
        yield event.plain_result('格式错误，输入"分数线 帮助"以查看帮助信息')
        return
    level_index, query, target = request
    if not 0 <= target <= 101:
        yield event.plain_result('分数线应在 0% 至 101% 之间')
        return
    chart = await resolve_song_chart(level_index, query)
    if isinstance(chart, str):
        yield event.plain_result(chart)
        return
    song, difficulty = chart
    try:
        picture = await draw_score_line_table(song, difficulty, target)
    except ValueError as exc:
        yield event.plain_result(str(exc))
        return
    yield event.chain_result(convert_message_segment_to_chain(picture))

async def mai_score_calculate_handler(event: AstrMessageEvent):
    """计算指定定数和达成率的分数"""
    message_str = get_plain_message_text(event)
    # 1. 匹配字符串提取 a (定数) 和 b (达成率)
    pattern = r'^([0-9]*\.?[0-9]+)的([0-9]*\.?[0-9]+)是多少分$'
    match = re.match(pattern, message_str)
    
    if not match:
        return

    a = float(match.group(1))
    b_raw = float(match.group(2))
    
    # 2. 达成率限制：100.5以上的都记为100.5
    b = min(b_raw, 100.5)

    # 3. 定义评级系数查找逻辑 (左闭右开)
    def get_coefficient(val):
        # 处理最高点的特殊情况
        if val >= 100.5 and val <= 101: return 0.224
        
        # 定义区间配置 (下限, 上限, 系数)
        thresholds = [
            (10, 20, 0.016), (20, 30, 0.032), (30, 40, 0.048),
            (40, 50, 0.064), (50, 60, 0.08), (60, 70, 0.096),
            (70, 75, 0.112), (75, 80, 0.128), (80, 90, 0.136),
            (90, 94, 0.152), (94, 97, 0.168), (97, 98, 0.2),
            (98, 99, 0.203), (99, 99.5, 0.208), (99.5, 100, 0.211),
            (100, 100.5, 0.216)
        ]
        
        for low, high, coef in thresholds:
            if low <= val < high:
                return coef
        return 0 # 不在区间内返回0

    coef = get_coefficient(b)

    # 4. 计算分数：定数 * 达成率 * 系数，然后向下取整
    # 注意：计算时达成率通常作为数值直接相乘（如 13.2 * 100.5 * 0.224）
    score = math.floor(a * b * coef)
    if score:
        yield event.plain_result(f"{a}的{b_raw}是{score}分")
        return
    else:
        yield event.plain_result("输入错误，可能是格式不正确或达成率或定数输入不正确。正确格式：{定数}的{达成率}是多少分")
        return
