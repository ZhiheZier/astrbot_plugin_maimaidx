"""Command catalogue and the single-image help renderer."""

import asyncio
import re
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from packaging.version import InvalidVersion, Version

from .. import SHANGGUMONO, SIYUAN, MessageSegment, __version__, get_botname
from .image import image_to_base64


@dataclass(frozen=True)
class HelpEntry:
    handlers: tuple[str, ...]
    command: str
    description: str
    prefix: bool = False
    admin: bool = False
    group_admin: bool = False
    new_in: str = ''


@dataclass(frozen=True)
class HelpSection:
    title: str
    color: str
    entries: tuple[HelpEntry, ...]


HELP_COLUMNS = (
    (
        HelpSection('查歌与谱面', '#168681', (
            HelpEntry(('search_music',), '查歌 <关键词> [页码]', '曲名搜索；也可用 search。'),
            HelpEntry(('search_base',), '定数查歌 <定数> / <下限> <上限> [页码]', '单值或范围筛选，如：定数查歌 13.7、定数查歌 13.6 13.9 1；整数范围需同时填页码。'),
            HelpEntry(('search_bpm',), 'bpm查歌 <BPM> / <下限> <上限> [页码]', '单值或范围筛选，如：bpm查歌 180、bpm查歌 180 220；末尾可加页码。'),
            HelpEntry(('search_artist',), '曲师查歌 <曲师名> [页码]', '按作曲者查询曲目。'),
            HelpEntry(('search_charter',), '谱师查歌 <谱师名> [页码]', '按谱面制作者查询曲目。'),
            HelpEntry(('query_chart',), 'id <歌曲ID>', '查看封面、物量、定数和 Rating。'),
            HelpEntry(('search_alias_song',), '<别名>是什么歌', '也可用「<别名>是啥歌」。', prefix=True),
            HelpEntry(('random_song',), '来个[dx/sd][难度]<等级>', '随机选曲，例如：来个dx紫13+。'),
        )),
        HelpSection('容错与 Rating', '#bc4768', (
            HelpEntry(('tolerance',), '容错计算 <难度><歌曲> [模式]',
                      '歌曲支持ID、别名、名称；难度后可加空格。模式101-（默认）/100-/0+放在末尾。例：容错计算 紫799 0+。', new_in='1.5.1'),
            HelpEntry(('score',), '分数线 <难度><歌曲> <目标>',
                      '目标可用分数或等级，例：分数线 紫 799 sss+。返回各音符GREAT/GOOD/MISS的独立容错个数。', new_in='1.5.1'),
            HelpEntry(('calculate_score',), '<定数>的<达成率>是多少分', '例如：13.2的100.5是多少分。', prefix=True),
        )),
        HelpSection('牌子、等级与进度', '#7e58ac', (
            HelpEntry(('rating_table',), '<等级>定数表', '例如：13+定数表；支持7至15级。', prefix=True),
            HelpEntry(('table_pfm',), '<等级>[fc/ap/fcp/app]完成表 [玩家]', '例如：14完成表、14fc完成表 Alice。', prefix=True),
            HelpEntry(('table_pfm',), '<版本><牌子>完成表 [1/2] [玩家]', '例如：晓将完成表 Alice；舞系、霸者支持分页，例：舞神完成表 1 Alice。', prefix=True),
            HelpEntry(('plate_process',), '<版本><牌子>进度表 [玩家]', '例如：晓将进度表 Alice，也可使用QQ号或@群成员。', prefix=True),
            HelpEntry(('level_process',), '<等级><评价>[分类]进度 [页码] [玩家]', '例如：14sss未完成进度 1 Alice；分类可选已完成/未完成/未游玩。', prefix=True),
            HelpEntry(('level_achievement_list',), '<等级/定数>分数列表 [页码] [玩家]', '例如：14分数列表 1 Alice；也可直接填写QQ号或@群成员。', prefix=True),
            HelpEntry(('plate_condition',), '牌子条件', '查看牌子完成条件。'),
            HelpEntry(('rise_score',), '我要在<等级>上<分数>分 [玩家]', '例如：我要在14上10分；也可用「我要上10分」获取上分推荐。'),
        )),
    ),
    (
        HelpSection('Best 50 与成绩分析', '#3478a4', (
            HelpEntry(('best50',), 'b50 [玩家]', '省略查询自己；玩家可用QQ号、@玩家或用户名。'),
            HelpEntry(('all_songs_best50',), '全曲b50 / allb50 / ab50 [玩家]', '不区分新旧曲，按单曲Rating取前50。'),
            HelpEntry(('ap50',), 'ap50 [玩家]', '仅统计AP成绩。'),
            HelpEntry(('ap_plus50',), 'ap+50 / 理论b50 [玩家]', '仅统计实际AP+成绩。'),
            HelpEntry(('dx_star_best50',), '一星b50～五星b50 [玩家]', '按最低DX SCORE星级筛选。', prefix=True),
            HelpEntry(('fitted_best50',), '拟合b50 / f50 [玩家]', '按拟合定数重新计算Rating。'),
            HelpEntry(('under_s_best50',), '越级50 / a50 [玩家]', '筛选达成率低于97%的成绩。'),
            HelpEntry(('near_best50',), '寸50 / c50 [玩家]', '筛选略低于SSS或SSS+的成绩。'),
            HelpEntry(('lock_best50',), '锁血50 / s50 [玩家]', '筛选略高于SSS或SSS+的成绩。'),
            HelpEntry(('difficulty_best50',), '绿/黄/红/紫/白谱b50 [玩家]', '例如：紫谱b50，按谱面难度筛选。', prefix=True),
            HelpEntry(('gold_analysis',), '含金量分析 / 含金量 [玩家]', '查看拟合定数高于官方定数最多的谱面。'),
            HelpEntry(('water_analysis',), '水分分析 / 含水量 [玩家]', '查看官方定数高于拟合定数最多的谱面。'),
            HelpEntry(('minfo',), 'minfo / info <歌曲> [玩家]', '例如：minfo 799 Alice；歌曲支持ID、名称、别名，有歧义时使用 minfo 歌曲 | 玩家。'),
            HelpEntry(('ginfo',), 'ginfo [难度]<歌曲>', '查询谱面统计、达成率分布；默认紫谱。'),
            HelpEntry(('rating_ranking', 'my_rating_ranking'), '查看排名 / 我的排名', '查询水鱼Rating排行榜及个人排名。'),
            HelpEntry(('ranking_membership',), '加入排行榜 / 退出排行榜', '自愿参与本群单曲排名，各群名单独立；使用当前查分器。', new_in='1.5.4'),
            HelpEntry(('song_ranking',), 'wmrk <歌曲> [难度]', 'wmrk后可不加空格，歌曲支持ID、名称或别名，多曲列出候选；默认最高难度，末尾可指定绿/黄/红/紫/白或英文难度，仅展示本群已加入且有成绩的玩家，每20人分页。', new_in='1.5.4'),
        )),
        HelpSection('查分器绑定与设置', '#168681', (
            HelpEntry(('data_source',), '数据源 [水鱼/落雪]', '不带参数查看当前数据源；也可用0/1切换。'),
            HelpEntry(('bind_divingfish',), '绑定水鱼 / dfbind', '群聊和私聊均可；打开链接授权后回填确认码。'),
            HelpEntry(('auth_code',), '水鱼授权码 XXXX-XXXX-XXXX', '完成水鱼绑定，避免只点击链接就绑定。'),
            HelpEntry(('bind_lxns',), '绑定落雪 / lxbind', '群聊和私聊均可；网页授权后回填授权码。'),
            HelpEntry(('auth_code',), '落雪授权码 XXXX-XXXX-XXXX', '完成落雪绑定；不要与水鱼确认码混用。'),
            HelpEntry(('auth_code',), '授权码 / code XXXX-XXXX-XXXX', '仅有一个待绑定查分器时自动识别。'),
            HelpEntry(('theme',), '主题 [序号] / theme [序号]', '查看或切换主题：0 prism_plus，1 circle。'),
        )),
        HelpSection('日常与猜歌', '#bb713b', (
            HelpEntry(('mai_today', 'mai_what'), '今日mai / 今日舞萌 / mai什么', '查看今日运势或随机推荐。'),
            HelpEntry(('guess_music', 'guess_pic'), '猜歌 / 猜曲绘', '群聊游戏；回复歌曲ID、名称或别名作答，提示自动发送。'),
            HelpEntry(('reset_guess',), '重置猜歌', '重置当前群的猜歌或猜曲绘进度。', group_admin=True),
            HelpEntry(('guess_on_off',), '开启mai猜歌 / 关闭mai猜歌', '开启或关闭当前群的猜歌功能。', prefix=True, group_admin=True),
        )),
    ),
    (
        HelpSection('机厅查询与排卡', '#bc4768', (
            HelpEntry(('toggle_arcade',), '开启排卡 / 关闭排卡', '排卡功能默认关闭，按当前群切换。', prefix=True, group_admin=True),
            HelpEntry(('search_arcade',), '查找机厅 <关键词/ID>', '无需开启排卡；可选本地或nearcade数据源，云端无需映射。开启位置查询后，直接发送QQ位置卡可查附近舞萌DX机厅。', new_in='1.5.2'),
            HelpEntry(('subscribe_arcade',), '订阅机厅 / 取消订阅机厅 <店名/ID>', '管理本群订阅；两种数据源独立保存，云端模式使用nearcade ID。', group_admin=True, new_in='1.5.2'),
            HelpEntry(('check_subscribe', 'arcade_query_multiple'), '查看订阅 / 机厅几人', '总览也可用 jtj；按机厅分组，云端游戏记录逐行列出，长列表折叠为聊天记录，失败标明本地。', new_in='1.5.2'),
            HelpEntry(('arcade_query_person',), '<机厅> [游戏]几人', '后缀也可用几、j、jr等，如14871几人、14871mai几；ID按当前数据源填写，nearcade按游戏名与version逐条展示，失败回退本地。', prefix=True, new_in='1.5.3'),
            HelpEntry(('arcade_person',), '<机厅> [游戏/机台别名]=人数 / +人数 / -人数', '机厅可填名称、别名或ID。可报机厅3、机厅mai3或mai3chu2；两种数据源均可配置mml等机台别名。=-1闭店。', prefix=True, new_in='1.5.3'),
            HelpEntry(('add_arcade',), '添加机厅 <店名> <地址> <机台数量> [别名]', '添加机厅信息；可填写多个别名，用空格分隔。', admin=True),
            HelpEntry(('delete_arcade',), '删除机厅 <完整店名>', '管理员删除机厅信息，需填写完整店名。', admin=True),
            HelpEntry(('modify_arcade',), '修改机厅 <店名/ID> [游戏] 数量 <数量>', '本地模式如修改机厅 123 chu 数量 2；云端模式的店铺及机台信息请在nearcade网站维护。', group_admin=True, new_in='1.5.2'),
            HelpEntry(('arcade_alias',), '添加机厅别名 / 删除机厅别名 <店名/ID> <别名>', '维护机厅的查询别名；本地模式使用本地ID，nearcade模式使用云端ID。'),
            HelpEntry(('group_arcade_alias',), '添加群机厅别名 / 删除群机厅别名 <店名/ID> <别名>', '添加前需本群订阅该机厅；别名仅当前群生效，可用于查找、订阅、查询和报人数，不改全局别名。', group_admin=True, new_in='1.5.2'),
            HelpEntry(('dx_arcade_help',), '排卡帮助', '查看排卡专用帮助。'),
        )),
        HelpSection('歌曲别名与投票', '#3478a4', (
            HelpEntry(('alias_song',), '<歌曲ID/名称/别名>有什么别名', '查看歌曲当前别名，例如：id799有什么别名。', prefix=True),
            HelpEntry(('alias_apply',), '添加别名 <歌曲ID> <别名>', '向公共别名服务提交申请。'),
            HelpEntry(('alias_agree',), '同意别名 <Tag>', 'Tag为当前投票或别名推送中的申请标识。'),
            HelpEntry(('alias_status',), '当前投票 [页码]', '查看正在进行的别名申请。'),
            HelpEntry(('alias_local_apply', 'alias_local_delete'), '添加本地别名 / 删除本地别名 <ID> <别名>', '只维护本BOT本地别名库。'),
            HelpEntry(('alias_switch',), '开启别名推送 / 关闭别名推送', '设置本群推送；仍受全局开关和白名单影响。', prefix=True),
        )),
        HelpSection('管理与其他', '#7e58ac', (
            HelpEntry(('update_data', 'update_alias'), '更新maimai数据 / 更新别名库', '管理员刷新曲库或别名数据。', admin=True),
            HelpEntry(('update_table', 'update_plate'), '更新定数表 / 更新完成表', '管理员重新生成表格素材。', admin=True),
            HelpEntry(('alias_switch_on_off',), '全局开启别名推送 / 全局关闭别名推送', '管理员控制全部群推送，独立于插件配置项。', prefix=True, admin=True),
            HelpEntry(('toggle_maimai_feature',), '开启舞萌功能 / 关闭舞萌功能', '管理员开关当前群的舞萌功能；白名单模式仅放行已开启的群。', prefix=True, admin=True),
            HelpEntry(('maihelp',), 'maihelp', '查看此完整帮助图。'),
            HelpEntry(('maimaidxhelp',), '帮助maimaidx / helpmaimaidx', '查看旧版帮助图。'),
            HelpEntry(('maimaidxrepo',), '项目地址maimaiDX / 项目地址maimaidx', '查看项目仓库。'),
        )),
    ),
)

IMAGE_WIDTH = 2160
MARGIN = 40
COLUMN_GAP = 28
COLUMN_PADDING = 24
COMMAND_SIZE = 27
DESCRIPTION_SIZE = 23
COMMAND_LINE = 39
DESCRIPTION_LINE = 34
NEW_BADGE_WIDTH = 38
NEW_BADGE_SPACE = 52
_help_lock = asyncio.Lock()
HELP_NOTES = (
    '※ 前置参数或开关指令，如「799是什么歌」「13+定数表」「开启别名推送」；标记本身不用输入。',
    '# 管理员专用；☆ 群管理员可用（群主或群内管理员），管理员也可使用所有☆指令。',
    '< > 为必填参数，[ ] 为可选参数；[玩家]可填QQ号、@群成员或水鱼用户名，省略查自己。',
    '难度对应：绿=Basic，黄=Advanced，红=Expert，紫=Master，白=Re:Master。',
    '英文难度仅为容错计算、分数线的可选写法，如「容错计算 Master799」等同「容错计算 紫799」。',
)


def is_new_feature(introduced: str, current: str) -> bool:
    if not introduced:
        return False
    try:
        added, version = Version(introduced), Version(current)
    except InvalidVersion:
        return False
    return (added.major, added.minor) == (version.major, version.minor) and added <= version


def help_command_label(entry: HelpEntry) -> str:
    permission = '☆' if entry.group_admin else '#' if entry.admin else ''
    markers = (' ※' if entry.prefix else '') + (f' {permission}' if permission else '')
    return entry.command + markers


def wrap_help_text(text: str, font, width: int) -> tuple[str, ...]:
    """Wrap without shrinking the font or dropping any characters."""
    lines = []
    for paragraph in text.split('\n'):
        tokens = []
        for token in re.findall(r'\[[^\]\n]*\]|<[^>\n]*>|[A-Za-z0-9_+%.\-]+|.', paragraph):
            if token in '，。；：、？！）》】〉]>' and tokens:
                tokens[-1] += token
            else:
                tokens.append(token)
        current = ''
        for token in tokens:
            if font.getlength(current + token) > width and current:
                lines.append(current)
                current = ''
            for char in token:
                if font.getlength(char) > width:
                    raise ValueError('帮助图文字区域过窄')
                if font.getlength(current + char) > width and current:
                    lines.append(current)
                    current = ''
                current += char
        lines.append(current)
    return tuple(lines)


def _help_background(height: int) -> Image.Image:
    width = IMAGE_WIDTH // 4
    small_height = (height + 3) // 4
    background = Image.new('RGB', (width, small_height), '#faf9f7')
    draw = ImageDraw.Draw(background)
    draw.polygon(((-90, 0), (20, 0), (width - 110, small_height),
                  (width - 260, small_height)), fill='#ffd25e')
    draw.polygon(((100, 0), (180, 0), (width + 60, small_height),
                  (width - 30, small_height)), fill='#ff6596')
    draw.polygon(((width - 120, 0), (width - 45, 0), (40, small_height),
                  (-60, small_height)), fill='#ffb0d9')
    draw.polygon(((0, int(small_height * .72)), (width, int(small_height * .86)),
                  (width, small_height), (0, small_height)), fill='#167db0')
    draw.rectangle((0, small_height - 25, width, small_height), fill='#164c7b')
    return background.filter(ImageFilter.GaussianBlur(20)).resize(
        (IMAGE_WIDTH, height), Image.Resampling.BICUBIC,
    ).convert('RGBA')


def render_help_image(bot_name: str, font_path: str, columns=HELP_COLUMNS, version: str = __version__) -> Image.Image:
    fonts = {size: ImageFont.truetype(font_path, size) for size in (60, 29, COMMAND_SIZE, DESCRIPTION_SIZE, 26, 22, 20)}
    column_width = (IMAGE_WIDTH - MARGIN * 2 - COLUMN_GAP * (len(columns) - 1)) // len(columns)
    text_width = column_width - COLUMN_PADDING * 2
    notes = [line for note in HELP_NOTES for line in wrap_help_text(note, fonts[22], IMAGE_WIDTH - MARGIN * 2 - 40)]
    notes_top = 126
    content_top = notes_top + len(notes) * 34 + 58
    layouts = []
    for column in columns:
        sections = []
        total_height = 0
        for section in column:
            entries = []
            height = 70
            for entry in section.entries:
                new = is_new_feature(entry.new_in, version)
                command = wrap_help_text(help_command_label(entry), fonts[COMMAND_SIZE],
                                         text_width - NEW_BADGE_SPACE if new else text_width)
                description = wrap_help_text(entry.description, fonts[DESCRIPTION_SIZE], text_width)
                entries.append((command, description, new))
                height += len(command) * COMMAND_LINE + len(description) * DESCRIPTION_LINE + 19
            sections.append((section, entries, height))
            total_height += height + 26
        layouts.append((sections, total_height))

    content_bottom = content_top + max(height for _, height in layouts)
    footer_font = fonts[22]
    footer = wrap_help_text(f'Generated by {bot_name} BOT', footer_font, IMAGE_WIDTH - MARGIN * 2)
    image = _help_background(content_bottom + 65 + len(footer) * 34)
    panels = Image.new('RGBA', image.size)
    panel_draw = ImageDraw.Draw(panels)
    shadow = Image.new('RGBA', image.size)
    shadow_draw = ImageDraw.Draw(shadow)
    for column_index in range(len(columns)):
        left = MARGIN + column_index * (column_width + COLUMN_GAP)
        shadow_draw.rounded_rectangle((left + 5, content_top - 9, left + column_width + 5, content_bottom + 14),
                                      radius=26, fill=(0, 0, 0, 42))
        panel_draw.rounded_rectangle((left, content_top - 14, left + column_width, content_bottom + 8),
                                     radius=26, fill=(255, 255, 255, 239))
    panel_draw.rounded_rectangle((MARGIN, notes_top - 15, IMAGE_WIDTH - MARGIN, content_top - 31),
                                 radius=22, fill=(255, 255, 255, 224))
    image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(6)))
    image = Image.alpha_composite(image, panels)
    draw = ImageDraw.Draw(image)
    title, version_label = 'maimaiDX 使用说明', f'v{version}'
    title_width = fonts[60].getlength(title)
    title_left = (IMAGE_WIDTH - title_width - 20 - fonts[26].getlength(version_label)) / 2
    draw.text((title_left + 3, 85), title, font=fonts[60], fill='#d7c5c7', anchor='ls')
    draw.text((title_left, 82), title, font=fonts[60], fill='#ff5688', anchor='ls')
    draw.text((title_left + title_width + 20, 82), version_label, font=fonts[26], fill='#bf4c73', anchor='ls')
    for index, line in enumerate(notes):
        draw.text((MARGIN + 20, notes_top + index * 34), line, font=fonts[22], fill='#42424c')

    for column_index, (sections, _) in enumerate(layouts):
        left = MARGIN + column_index * (column_width + COLUMN_GAP)
        y = content_top
        for section, entries, height in sections:
            draw.text((left + COLUMN_PADDING, y), section.title, font=fonts[29], fill=section.color)
            draw.line((left + COLUMN_PADDING, y + 53, left + column_width - COLUMN_PADDING, y + 53),
                      fill='#e3dce2', width=2)
            entry_y = y + 70
            for command, description, new in entries:
                for line in command:
                    draw.text((left + COLUMN_PADDING, entry_y),
                              line, font=fonts[COMMAND_SIZE], fill='#292933')
                    entry_y += COMMAND_LINE
                if new:
                    badge_left = left + COLUMN_PADDING + fonts[COMMAND_SIZE].getlength(command[-1]) + 12
                    badge_top = entry_y - COMMAND_LINE + 4
                    draw.rounded_rectangle((badge_left, badge_top, badge_left + NEW_BADGE_WIDTH, badge_top + 30),
                                           radius=4, fill='#f43d71')
                    draw.text((badge_left + NEW_BADGE_WIDTH / 2, badge_top + 15), '新',
                              font=fonts[20], fill='#ffffff', anchor='mm')
                for line in description:
                    draw.text((left + COLUMN_PADDING, entry_y), line, font=fonts[DESCRIPTION_SIZE], fill='#595967')
                    entry_y += DESCRIPTION_LINE
                entry_y += 19
            y += height + 26

    for index, line in enumerate(footer):
        draw.text((IMAGE_WIDTH // 2, content_bottom + 35 + index * 34), line,
                  font=footer_font, fill='#ffffff', anchor='mt')
    return image.convert('RGB')


@lru_cache(maxsize=2)
def _cached_help_image(bot_name: str, font_path: str, font_mtime: int, font_size: int, columns,
                       version: str = __version__) -> str:
    return image_to_base64(render_help_image(bot_name, font_path, columns, version))


async def draw_help_image() -> MessageSegment:
    font_path = next((path for path in (SIYUAN, SHANGGUMONO) if path.is_file()), SIYUAN)
    stat = font_path.stat()
    # Serialize cache misses and keep Pillow rendering off the message event loop.
    async with _help_lock:
        picture = await asyncio.to_thread(
            _cached_help_image, get_botname(), str(font_path), stat.st_mtime_ns, stat.st_size, HELP_COLUMNS, __version__,
        )
    return MessageSegment.image(picture)
