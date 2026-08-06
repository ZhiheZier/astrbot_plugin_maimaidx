import random
import re
from re import Match
from PIL import Image
import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

from .. import Root, log, get_botname
from ..libraries.image import image_to_base64, music_picture
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import *
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_music_info import draw_music_info
from ..libraries.maimaidx_player_score import rating_ranking_data
from ..libraries.tool import qqhash


def extract_at_qqid(event: AstrMessageEvent):
    """
    从消息中提取 @ 的 QQ ID
    
    Args:
        event: AstrMessageEvent 对象
    
    Returns:
        被 @ 的 QQ ID（字符串），如果没有 @ 消息则返回 None
    """
    if not event.message_obj or not event.message_obj.message:
        return None
    
    # 遍历消息链，查找 At 组件
    for component in event.message_obj.message:
        # 检查是否是 At 组件
        # Comp.At 组件可能有 qq 属性，或者通过 type 和 data 访问
        if hasattr(component, 'qq'):
            qq_id = component.qq
            if qq_id:
                return str(qq_id)
        elif hasattr(component, 'type') and component.type == 'at':
            # 通过 data 字典访问
            if hasattr(component, 'data') and 'qq' in component.data:
                qq_id = component.data['qq']
                if qq_id:
                    return str(qq_id)
            # 或者直接有 qq 属性
            elif hasattr(component, 'qq'):
                qq_id = component.qq
                if qq_id:
                    return str(qq_id)
    
    return None


# 查分图成功返回后的提示（主题 / 数据源）
TIP_THEME_SOURCE = (
    '可使用「主题」指令更换主题，「数据源」指令更换指定查分器。'
)


def convert_message_segment_to_chain(msg):
    """将 MessageSegment 转换为 astrbot 的 MessageChain"""
    if isinstance(msg, str):
        # 避免出现“只回复了引用/At，但正文为空”的情况
        text = msg if msg.strip() else '发生错误：返回内容为空'
        return [Comp.Plain(text)]
    
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


def append_theme_source_tip(chain, result):
    """图片成功返回时，在图片后追加主题 / 数据源提示。"""
    if isinstance(result, str):
        return chain
    if hasattr(result, 'type') and result.type == 'image':
        chain.append(Comp.Plain(TIP_THEME_SOURCE))
    return chain


async def update_data_handler(event: AstrMessageEvent, superusers: list = None):
    """更新maimai数据"""
    sender_id = event.get_sender_id()
    if superusers and str(sender_id) not in superusers:
        yield event.plain_result('仅允许管理员执行此操作')
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
    
    from .. import FORTUNE

    uid = event.get_sender_id()
    # 确保 uid 是整数类型
    try:
        uid_int = int(uid) if uid else 0
    except (ValueError, TypeError):
        uid_int = 0
    h = qqhash(uid_int)
    rp = h % 100
    wm_value = []
    for i in range(11):
        wm_value.append(h & 3)
        h >>= 2
    msg = f'\n今日人品值：{rp}\n'
    for i in range(11):
        if wm_value[i] == 3:
            msg += f'宜 {FORTUNE[i]}\n'
        elif wm_value[i] == 0:
            msg += f'忌 {FORTUNE[i]}\n'
    music = mai.total_list[h % len(mai.total_list)]
    ds = '/'.join([str(_) for _ in music.ds])
    # 动态获取 BOTNAME，确保获取最新值
    from .. import get_botname
    botname = get_botname()
    msg += f'{botname} Bot提醒您：打机时不要大力拍打或滑动哦\n今日推荐歌曲：\n'
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


# ============================ 数据源 / 主题 / 落雪绑定 ============================
CODE_PATTERN = re.compile(r'^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$')
LXNS_ERROR = 'BOT 管理员尚未配置落雪查分器相关信息'

# 数据源中文别名 -> 索引
_SOURCE_ALIAS = {
    '水鱼': '0', 'diving-fish': '0', 'divingfish': '0', 'df': '0',
    '落雪': '1', 'lxns': '1', 'lxns-network': '1', 'lx': '1',
}


def _lxns_configured() -> bool:
    """落雪是否可用（配置了开发者 Token 或 OAuth 应用）"""
    cfg = maiApi.config
    return bool(cfg.lxns_dev_token) or bool(cfg.lx_client_id and cfg.lx_redirect_uri)


def _authorize_url() -> str:
    cfg = maiApi.config
    return (
        'https://maimai.lxns.net/oauth/authorize'
        '?response_type=code'
        f'&client_id={cfg.lx_client_id}'
        f'&redirect_uri={cfg.lx_redirect_uri}'
        '&scope=read_player+read_user_profile+write_player'
    )


async def source_handler(event: AstrMessageEvent):
    """数据源 切换查分器"""
    from ..libraries.maimaidx_user import ServiceName, userstore

    args = event.message_str.strip().replace('数据源', '', 1).strip().lower()
    if not args:
        try:
            current = userstore.get(int(event.get_sender_id())).service.label
        except (ValueError, TypeError):
            current = ServiceName.DIVINGFISH.label
        yield event.plain_result(
            f'当前数据源：「{current}」\n'
            f'可使用「数据源 序号」进行切换：\n{ServiceName.get_help()}'
        )
        return

    index = _SOURCE_ALIAS.get(args, args)
    source_ = ServiceName.get_by_index(index)
    if source_ is None:
        yield event.plain_result(f'未找到该数据源：\n{ServiceName.get_help()}')
        return

    qqid = event.get_sender_id()
    if source_ == ServiceName.LXNS and not _lxns_configured():
        await userstore.update(int(qqid), service=ServiceName.DIVINGFISH)
        yield event.plain_result(
            LXNS_ERROR + '。为防止无法查询成绩，已强制将数据源切换为水鱼查分器。'
        )
        return

    await userstore.update(int(qqid), service=source_)
    tip = ''
    if source_ == ServiceName.LXNS:
        tip = (
            '\n※ 若未进行 OAuth 授权，请确保已在落雪查分器绑定 QQ 号，'
            '并在「隐私设置」中允许第三方读取成绩。'
        )
    yield event.plain_result(f'数据源已切换为：「{source_.label}」{tip}')


async def theme_handler(event: AstrMessageEvent):
    """主题 切换成绩图主题"""
    from ..libraries.maimaidx_user import Theme, userstore

    args = event.message_str.strip()
    for p in ('主题', 'theme'):
        if args.lower().startswith(p):
            args = args[len(p):].strip()
            break
    if not args:
        try:
            current = userstore.get(int(event.get_sender_id())).theme.value
        except (ValueError, TypeError):
            current = Theme.PRISM_PLUS.value
        yield event.plain_result(
            f'当前主题：「{current}」\n可使用「主题 序号」进行切换：\n{Theme.get_help()}'
        )
        return

    theme_ = Theme.get_by_index(args)
    if theme_ is None:
        yield event.plain_result(f'未找到该主题：\n{Theme.get_help()}')
        return
    await userstore.update(int(event.get_sender_id()), theme=theme_)
    yield event.plain_result(f'主题已切换为：「{theme_.value}」')


async def bind_lxns_handler(event: AstrMessageEvent):
    """绑定落雪/lxbind 引导 OAuth 授权"""
    cfg = maiApi.config
    if not cfg.lx_client_id or not cfg.lx_redirect_uri:
        yield event.plain_result(
            LXNS_ERROR + '，无法进行 OAuth 绑定授权。\n'
            '（如管理员已配置开发者 Token，你只需在落雪绑定 QQ 号后使用「数据源 落雪」即可）'
        )
        return
    from textwrap import dedent
    botname = get_botname()
    msg = dedent(f'''
        请点击以下链接进行授权，
        允许「{botname} BOT」访问你的落雪查分器数据：
        =======================
        {_authorize_url()}
        =======================
        授权后你会得到一个格式为「XXXX-XXXX-XXXX」的授权码，
        请复制它并发送「授权码 XXXX-XXXX-XXXX」完成绑定。
        =======================
        注意：请在落雪查分器「账号设置 -> 隐私设置」中
        开启允许读取成绩，否则 BOT 无法查询你的成绩。
    ''').strip()
    yield event.plain_result(msg)


async def authcode_handler(event: AstrMessageEvent):
    """授权码/code 使用授权码完成落雪 OAuth 绑定"""
    from ..libraries.maimaidx_lxns import LxnsAPI, LxnsError
    from ..libraries.maimaidx_user import ServiceName, userstore

    args = event.message_str.strip()
    for p in ('授权码', 'code'):
        if args.lower().startswith(p):
            args = args[len(p):].strip()
            break
    code = args.strip()
    if not CODE_PATTERN.fullmatch(code):
        yield event.plain_result('授权码格式错误，请重新发送。格式：授权码 XXXX-XXXX-XXXX')
        return

    cfg = maiApi.config
    if not cfg.lx_client_id or not cfg.lx_client_secret or not cfg.lx_redirect_uri:
        yield event.plain_result(LXNS_ERROR + '，无法完成 OAuth 绑定。')
        return

    qqid = event.get_sender_id()
    try:
        api = LxnsAPI(qqid=int(qqid))
        token = await api.oauth_fetch_token(code)
        api.access_token = token.access_token
        player = await api.player_personal()
        await userstore.update(
            int(qqid),
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            friend_code=player.friend_code,
            service=ServiceName.LXNS,
        )
        yield event.plain_result(
            f'授权完成！已绑定落雪账号「{player.name}」，数据源已切换为落雪。'
        )
    except LxnsError as e:
        yield event.plain_result(f'授权失败：{e}')
    except Exception as e:
        log.error(f'落雪授权失败: {e}')
        yield event.plain_result('授权失败，请检查授权码是否正确或稍后重试。')
