import re
from re import Match
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import levelList, combo_rank, scoreRank, syncRank, ratingdir, platecn, log
from ..libraries.maimaidx_music_info import (
    draw_plate_table,
    draw_rating,
    draw_rating_table,
)
from ..libraries.maimaidx_player_score import (
    level_achievement_list_data,
    level_process_data,
    player_plate_data,
    rise_score_data,
)
from ..libraries.maimaidx_update_table import update_plate_table, update_rating_table
from .mai_base import convert_message_segment_to_chain, extract_at_qqid


async def update_table_handler(event: AstrMessageEvent, superusers: list = None):
    """更新定数表"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    result = await update_rating_table()
    chain = convert_message_segment_to_chain(result)
    yield event.chain_result(chain)


async def update_plate_handler(event: AstrMessageEvent, superusers: list = None):
    """更新完成表"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    result = await update_plate_table()
    chain = convert_message_segment_to_chain(result)
    yield event.chain_result(chain)


async def rating_table_handler(event: AstrMessageEvent):
    """定数表 - 查询定数表"""
    message_str = event.message_str.strip()
    # 移除后缀
    args = message_str.replace('定数表', '').strip()
    
    if args in levelList[:6]:
        yield event.plain_result('只支持查询lv7-15的定数表')
        return
    elif args in levelList[6:]:
        path = ratingdir / f'{args}.png'
        pic = draw_rating(args, path)
        chain = convert_message_segment_to_chain(pic)
        yield event.chain_result(chain)
    else:
        yield event.plain_result('无法识别的定数')


async def table_pfm_handler(event: AstrMessageEvent):
    """完成表 - 查询完成表"""
    qqid = extract_at_qqid(event)
    message_str = event.message_str.strip()
    # 移除后缀
    args = message_str.replace('完成表', '').strip()
    
    rating = re.search(r'^([0-9]+\+?)(app|fcp|ap|fc)?', args, re.IGNORECASE)
    plate = re.search(r'^([真超檄橙暁晓桃櫻樱紫菫堇白雪輝辉熊華华爽煌舞霸宙星祭祝双宴镜])([極极将舞神者]舞?)$', args)
    
    if rating:
        ra = rating.group(1)
        plan = rating.group(2)
        if args in levelList[:5]:
            yield event.plain_result('只支持查询lv6-15的完成表')
            return
        elif ra in levelList[5:]:
            pic = await draw_rating_table(qqid, ra, True if plan and plan.lower() in combo_rank else False)
            chain = convert_message_segment_to_chain(pic)
            yield event.chain_result(chain)
        else:
            yield event.plain_result('无法识别的表格')
    elif plate:
        ver = plate.group(1)
        plan = plate.group(2)
        if ver in platecn:
            ver = platecn[ver]
        if ver in ['舞', '霸']:
            yield event.plain_result('暂不支持查询「舞」系和「霸者」的牌子')
            return
        if f'{ver}{plan}' == '真将':
            yield event.plain_result('真系没有真将哦')
            return
        pic = await draw_plate_table(qqid, ver, plan)
        chain = convert_message_segment_to_chain(pic)
        yield event.chain_result(chain)
    else:
        yield event.plain_result('无法识别的表格')


async def rise_score_handler(event: AstrMessageEvent):
    """我要在xxx上加xxx分 - 查询上分数据"""
    qqid = extract_at_qqid(event)
    message_str = event.message_str.strip()
    match = re.match(r'^我要在?([0-9]+\+?)?[上加\+]([0-9]+)?分\s?(.+)?', message_str)
    
    username = None
    score = 0
    
    if not match:
        rating = None
        score = None
    else:
        rating = match.group(1)
        if match.group(2):
            score = int(match.group(2))
    
    if rating and rating not in levelList:
        yield event.plain_result('无此等级')
        return
    
    if match and match.group(3):
        username = match.group(3).strip()
    
    if username:
        qqid = None
    
    data = await rise_score_data(qqid, username, rating, score)
    chain = convert_message_segment_to_chain(data)
    yield event.chain_result(chain)


async def plate_process_handler(event: AstrMessageEvent):
    """牌子进度 - 查询牌子进度"""
    qqid = extract_at_qqid(event)
    message_str = event.message_str.strip()
    match = re.match(r'^([真超檄橙暁晓桃櫻樱紫菫堇白雪輝辉舞霸熊華华爽煌星宙祭祝双宴镜])([極极将舞神者]舞?)进度\s?(.+)?', message_str)
    
    if not match:
        return
    
    username = ''
    ver = match.group(1)
    plan = match.group(2)
    
    if f'{ver}{plan}' == '真将':
        yield event.plain_result('真系没有真将哦')
        return
    
    if match.group(3):
        username = match.group(3).strip()
    
    if username:
        qqid = None
    
    data = await player_plate_data(qqid, username, ver, plan)
    chain = convert_message_segment_to_chain(data)
    yield event.chain_result(chain)


async def level_process_handler(event: AstrMessageEvent):
    """等级进度 - 查询等级进度"""
    qqid = extract_at_qqid(event)
    message_str = event.message_str.strip()
    match = re.match(r'^([0-9]+\+?)\s?([abcdsfxp\+]+)\s?([\u4e00-\u9fa5]+)?进度\s?([0-9]+)?\s?(.+)?', message_str)
    
    if not match:
        return
    
    username = ''
    level = match.group(1)
    plan = match.group(2)
    category = match.group(3)
    page = match.group(4)
    username = match.group(5)
    
    if level not in levelList:
        yield event.plain_result('无此等级')
        return
    
    if plan.lower() not in scoreRank + comboRank + syncRank:
        yield event.plain_result('无此评价等级')
        return
    
    if levelList.index(level) < 11 or (plan.lower() in scoreRank and scoreRank.index(plan.lower()) < 8):
        yield event.plain_result('兄啊，有点志向好不好')
        return
    
    if category:
        if category in ['已完成', '未完成', '未开始', '未游玩']:
            _c = {
                '已完成': 'completed',
                '未完成': 'unfinished',
                '未开始': 'notstarted',
                '未游玩': 'notstarted'
            }
            category = _c[category]
        else:
            yield event.plain_result(f'无法指定查询「{category}」')
            return
    else:
        category = 'default'
    
    if username:
        qqid = None
    
    data = await level_process_data(qqid, username, level, plan, category, int(page) if page else 1)
    chain = convert_message_segment_to_chain(data)
    yield event.chain_result(chain)


async def level_achievement_list_handler(event: AstrMessageEvent):
    """分数列表 - 查询分数列表"""
    qqid = extract_at_qqid(event)
    message_str = event.message_str.strip()
    match = re.match(r'^([0-9]+\.?[0-9]?\+?)分数列表\s?([0-9]+)?\s?(.+)?', message_str)
    
    if not match:
        return
    
    username = ''
    rating = match.group(1)
    page = match.group(2)
    username = match.group(3)
    
    try:
        if '.' in rating:
            rating = round(float(rating), 1)
        elif rating not in levelList:
            yield event.plain_result('无此等级')
            return
    except ValueError:
        if rating not in levelList:
            yield event.plain_result('无此等级')
            return
    
    if username:
        qqid = None
    
    data = await level_achievement_list_data(qqid, username, rating, int(page) if page else 1)
    chain = convert_message_segment_to_chain(data)
    yield event.chain_result(chain)
