import random
import re
import tempfile
from pathlib import Path
from PIL import Image
import astrbot.api.message_components as Comp

from astrbot.api.event import filter, AstrMessageEvent

from .. import BOTNAME, Root, log
from ..libraries.image import image_to_base64, music_picture
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_info
from ..libraries.maimaidx_player_score import rating_ranking_data
from ..libraries.tool import qqhash


def extract_at_qqid(event: AstrMessageEvent) -> int:
    """从消息链中提取 @ 的 QQ ID，如果没有则返回发送者 ID"""
    # 使用 event.message_obj.message 获取消息链
    for msg in event.message_obj.message:
        # 检查是否是 At 消息组件
        if isinstance(msg, Comp.At):
            qq = msg.qq
            if qq and str(qq) != event.get_self_id() and qq != 'all':
                return int(qq)
    return event.get_sender_id()


def convert_message_segment_to_chain(msg_seg):
    """
    将 Hoshino/NoneBot 的 MessageSegment 转换为 astrbot 的 MessageChain
    支持图片和文本类型
    """
    chain = []
    
    # 检查是否是 MessageSegment 类型（Hoshino/NoneBot）
    if hasattr(msg_seg, 'type'):
        if msg_seg.type == 'image':
            # 提取图片数据
            if hasattr(msg_seg.data, 'get'):
                image_data = msg_seg.data.get('file') or msg_seg.data.get('url')
            else:
                image_data = getattr(msg_seg.data, 'file', None) or getattr(msg_seg.data, 'url', None)
            
            if image_data:
                # 如果是 base64 格式，需要保存为临时文件
                if image_data.startswith('base64://'):
                    # 提取 base64 数据
                    base64_data = image_data.replace('base64://', '')
                    import base64
                    from io import BytesIO
                    
                    # 解码并保存为临时文件
                    img_data = base64.b64decode(base64_data)
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                    temp_file.write(img_data)
                    temp_file.close()
                    chain.append(Comp.Image.fromFileSystem(temp_file.name))
                elif Path(image_data).exists():
                    # 如果是文件路径
                    chain.append(Comp.Image.fromFileSystem(image_data))
                else:
                    # 如果是 URL
                    chain.append(Comp.Image.fromURL(image_data))
        elif msg_seg.type == 'text':
            text = msg_seg.data.get('text', '') if hasattr(msg_seg.data, 'get') else getattr(msg_seg.data, 'text', '')
            if text:
                chain.append(Comp.Plain(text))
    elif isinstance(msg_seg, str):
        # 如果是字符串，直接作为文本
        chain.append(Comp.Plain(msg_seg))
    
    return chain if chain else [Comp.Plain(str(msg_seg))]


# 这些函数将在主插件类中注册为方法
# 注意：权限检查需要根据 astrbot 的权限系统进行适配

async def update_data_handler(event: AstrMessageEvent, superusers: list = None):
    """更新maimai数据"""
    from ..utils.permission import is_superuser
    
    if not await is_superuser(event, superusers):
        yield event.plain_result('仅允许超级管理员执行此操作')
        return
    
    await mai.get_music()
    await mai.get_music_alias()
    yield event.plain_result('maimai数据更新完成')


async def maimaidxhelp_handler(event: AstrMessageEvent):
    """帮助maimaiDX"""
    help_image_path = Root / 'maimaidxhelp.png'
    if help_image_path.exists():
        chain = [
            Comp.Image.fromFileSystem(str(help_image_path))
        ]
        yield event.chain_result(chain)
    else:
        yield event.plain_result('帮助图片未找到')


async def maimaidxrepo_handler(event: AstrMessageEvent):
    """项目地址maimaiDX"""
    yield event.plain_result('项目地址：https://github.com/Yuri-YuzuChaN/maimaiDX\n求star，求宣传~')


async def mai_today_handler(event: AstrMessageEvent):
    """今日mai/今日舞萌/今日运势"""
    wm_list = [
        '拼机', 
        '推分', 
        '越级', 
        '下埋', 
        '夜勤', 
        '练底力', 
        '练手法', 
        '打旧框', 
        '干饭', 
        '抓绝赞', 
        '收歌'
    ]
    uid = event.get_sender_id()
    h = qqhash(uid)
    rp = h % 100
    wm_value = []
    for i in range(11):
        wm_value.append(h & 3)
        h >>= 2
    msg = f'\n今日人品值：{rp}\n'
    for i in range(11):
        if wm_value[i] == 3:
            msg += f'宜 {wm_list[i]}\n'
        elif wm_value[i] == 0:
            msg += f'忌 {wm_list[i]}\n'
    music = mai.total_list[h % len(mai.total_list)]
    ds = '/'.join([str(_) for _ in music.ds])
    msg += f'{BOTNAME} Bot提醒您：打机时不要大力拍打或滑动哦\n今日推荐歌曲：\n'
    msg += f'ID.{music.id} - {music.title}\n'
    msg += ds
    
    # 构建消息链：文本 + 图片
    chain = [Comp.Plain(msg)]
    
    # 添加图片
    music_img_path = music_picture(music.id)
    if music_img_path.exists():
        chain.append(Comp.Image.fromFileSystem(str(music_img_path)))
    
    yield event.chain_result(chain)


async def mai_what_handler(event: AstrMessageEvent):
    """mai什么"""
    message_str = event.message_str
    match = re.search(r'.*mai.*什么(.+)?', message_str, re.IGNORECASE)
    
    music = mai.total_list.random()
    user = None
    if match and match.group(1):
        point = match.group(1)
        if '推分' in point or '上分' in point or '加分' in point:
            try:
                user = await maiApi.query_user_b50(qqid=event.get_sender_id())
                r = random.randint(0, 1)
                _ra = 0
                ignore = []
                if r == 0:
                    if sd := user.charts.sd:
                        ignore = [m.song_id for m in sd if m.achievements < 100.5]
                        _ra = sd[-1].ra
                else:
                    if dx := user.charts.dx:
                        ignore = [m.song_id for m in dx if m.achievements < 100.5]
                        _ra = dx[-1].ra
                if _ra != 0:
                    ds = round(_ra / 22.4, 1)
                    musiclist = mai.total_list.filter(ds=(ds, ds + 1))
                    for _m in musiclist:
                        if int(_m.id) in ignore:
                            musiclist.remove(_m)
                    music = musiclist.random()
            except (UserNotFoundError, UserDisabledQueryError):
                pass
    
    result = await draw_music_info(music, event.get_sender_id(), user)
    # 将 MessageSegment 转换为 MessageChain
    chain = convert_message_segment_to_chain(result)
    yield event.chain_result(chain)


async def random_song_handler(event: AstrMessageEvent):
    """随机歌曲"""
    message_str = event.message_str
    match = re.match(r'^[来随给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$', message_str)
    
    try:
        if not match:
            yield event.plain_result('随机命令错误，请检查语法')
            return
            
        diff = match.group(1)
        if diff == 'dx':
            tp = ['DX']
        elif diff == 'sd' or diff == '标准':
            tp = ['SD']
        else:
            tp = ['SD', 'DX']
        level = match.group(3)
        if match.group(2) == '':
            music_data = mai.total_list.filter(level=level, type=tp)
        else:
            music_data = mai.total_list.filter(level=level, diff=['绿黄红紫白'.index(match.group(2))], type=tp)
        if len(music_data) == 0:
            msg = '没有这样的乐曲哦。'
            yield event.plain_result(msg)
        else:
            result = await draw_music_info(music_data.random(), event.get_sender_id())
            # 将 MessageSegment 转换为 MessageChain
            chain = convert_message_segment_to_chain(result)
            yield event.chain_result(chain)
    except Exception as e:
        log.error(f'随机命令错误: {e}')
        yield event.plain_result('随机命令错误，请检查语法')


async def rating_ranking_handler(event: AstrMessageEvent):
    """查看排名/查看排行"""
    message_str = event.message_str.strip()
    # 移除命令前缀
    args = message_str.replace('查看排名', '').replace('查看排行', '').strip()
    
    page = 1
    name = ''
    if args.isdigit():
        page = int(args)
    else:
        name = args.lower()
    
    pic = await rating_ranking_data(name, page)
    # 将 MessageSegment 转换为 MessageChain
    chain = convert_message_segment_to_chain(pic)
    yield event.chain_result(chain)


async def my_rating_ranking_handler(event: AstrMessageEvent):
    """我的排名"""
    try:
        user = await maiApi.query_user_b50(qqid=event.get_sender_id())
        rank_data = await maiApi.rating_ranking()
        for num, rank in enumerate(rank_data):
            if rank.username == user.username:
                result = f'您的Rating为「{rank.ra}」，排名第「{num + 1}」名'
                yield event.plain_result(result)
                return
        yield event.plain_result('未找到您的排名信息')
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        yield event.plain_result(str(e))
