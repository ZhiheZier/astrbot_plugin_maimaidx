import random
import re
from re import Match
from PIL import Image
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import BOTNAME, Root, log
from ..libraries.image import image_to_base64, music_picture
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_info
from ..libraries.maimaidx_player_score import rating_ranking_data
from ..libraries.tool import qqhash


def convert_message_segment_to_chain(msg):
    """将 MessageSegment 转换为 astrbot 的 MessageChain"""
    if isinstance(msg, str):
        return [Comp.Plain(msg)]
    
    # 如果是 MessageSegment 对象
    if hasattr(msg, 'type') and hasattr(msg, 'data'):
        if msg.type == 'image':
            # 处理图片
            file_data = msg.data.get('file', '')
            if file_data.startswith('base64://'):
                # base64 图片，需要保存到临时文件
                import base64
                import tempfile
                base64_data = file_data.replace('base64://', '')
                img_data = base64.b64decode(base64_data)
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                temp_file.write(img_data)
                temp_file.close()
                return [Comp.Image.fromFileSystem(temp_file.name)]
            elif file_data.startswith('http://') or file_data.startswith('https://'):
                return [Comp.Image.fromURL(file_data)]
            else:
                # 文件路径
                return [Comp.Image.fromFileSystem(file_data)]
        elif msg.type == 'text':
            return [Comp.Plain(msg.data.get('text', ''))]
    
    # 如果是列表，递归处理
    if isinstance(msg, list):
        chain = []
        for item in msg:
            chain.extend(convert_message_segment_to_chain(item))
        return chain
    
    # 默认返回文本
    return [Comp.Plain(str(msg))]


async def update_data_handler(event: AstrMessageEvent, superusers: list = None):
    """更新maimai数据"""
    sender_id = event.get_sender_id()
    if superusers and str(sender_id) not in superusers:
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
    yield event.plain_result('项目地址：https://github.com/ZhiheZier/astrbot_plugin_maimaidx\n求star，求宣传~')


async def mai_today_handler(event: AstrMessageEvent):
    """今日mai/今日舞萌/今日运势"""
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
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
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
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
    # 检查数据是否加载
    if not hasattr(mai, 'total_list') or not mai.total_list:
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    
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
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        yield event.plain_result(str(e))
