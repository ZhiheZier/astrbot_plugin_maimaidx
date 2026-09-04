import copy

from .. import MessageSegment, get_botname
from .image import draw_text_with_font_fallback
from .maimai_best_50 import *
from .maimaidx_lxns import LxnsError
from .maimaidx_model import Song
from .maimaidx_music import mai


def newbestscore(song_id: str, lv: int, value: int, bestlist: List[ChartInfo]) -> int:
    for v in bestlist:
        if song_id == str(v.song_id) and lv == v.level_index:
            if value >= v.ra:
                return value - v.ra
            else:
                return 0
    return value - bestlist[-1].ra


def get_best_rating(ds: float) -> List[int]:
    last_item = achievementList[-1]
    ra = [computeRa(ds, r) for r in achievementList[-6:]]
    ra.append(computeRa(ds, last_item) + 1)
    return sorted(ra, reverse=True)


def format_fitting(fit_diff: Optional[float]) -> str:
    return f'擬 - {fit_diff:.2f}' if fit_diff is not None else '-'


def format_rating_gain(value: int, gain: Optional[int]) -> Union[int, str]:
    return value if not gain else f'{value}(↑{gain})'


async def draw_music_info(
    music: Song,
    qqid: Optional[int] = None, 
    user: Optional[UserInfo] = None
) -> MessageSegment:
    """
    查看谱面
    
    Params:
        `music`: 曲目模型
        `qqid`: QQID
        `user`: 用户模型
    Returns:
        `MessageSegment`
    """
    from .maimaidx_user import Theme, userstore

    calc = True
    isfull = True
    bestlist: List[ChartInfo] = []
    theme = userstore.get(qqid).theme if qqid else Theme.PRISM_PLUS
    try:
        if qqid:
            if user is None:
                from .maimaidx_source import get_player_b50_userinfo

                player = await get_player_b50_userinfo(qqid=qqid)
            else:
                player = user
            if music.version_str == list(plate_to_dx_version.values())[-1]:
                bestlist = player.charts.dx
                isfull = bool(len(bestlist) == 15)
            else:
                bestlist = player.charts.sd
                isfull = bool(len(bestlist) == 35)
        else:
            calc = False
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError):
        calc = False
    except Exception:
        calc = False

    # 宴会場曲目走专用模板
    if music.genre == '宴会場':
        return await draw_music_banquet_info(music)

    im = Image.open(themed_path(theme, 'chart_info.png')).convert('RGBA')
    dr = ImageDraw.Draw(im)
    mr = DrawText(dr, SIYUAN)
    tb = DrawText(dr, TBFONT)
    fn = DrawText(dr, FOTNEWRODIN)

    default_color = theme.color

    im.alpha_composite(Image.open(themed_path(theme, 'logo.png')).resize((249, 120)), (65, 25))
    if music.isnew:
        im.alpha_composite(Image.open(maimaidir / 'UI_CMN_TabTitle_NewSong.png').resize((249, 120)), (842, 100))
    songbg = Image.open(music_picture(music.song_id)).resize((242, 242))
    im.alpha_composite(songbg, (133, 197))
    im.alpha_composite(Image.open(maimaidir / f'{music.version_str}.png').resize((182, 90)), (800, 370))
    im.alpha_composite(Image.open(maimaidir / f'{music.type}.png').resize((80, 30)), (295, 410))

    title = music.song_name
    if coloumWidth(title) > 40:
        title = changeColumnWidth(title, 39) + '...'
    fn.draw(405, 220, 28, title, default_color, 'lm')
    artist = music.artist
    if coloumWidth(artist) > 50:
        artist = changeColumnWidth(artist, 49) + '...'
    fn.draw(407, 265, 20, artist, default_color, 'lm')
    fn.draw(460, 345, 24, music.bpm, default_color, 'lm')
    fn.draw(405, 435, 22, f'ID {music.song_id}', default_color, 'lm')
    mr.draw(665, 435, 24, music.genre, default_color, 'mm')

    for num, difficulty in enumerate(music.difficulties):
        if num == 4:
            color = (255, 255, 255, 255)
        else:
            color = (255, 255, 255, 255)
        spacing = 70 * num
        fn.draw(120, 590 + spacing, 22, f'{difficulty.level}({difficulty.level_value})', color, 'mm')
        fitting = format_fitting(
            difficulty.stats.fit_diff if difficulty.stats else None
        )
        fn.draw(120, 613 + spacing, 15, fitting, (255, 255, 255, 255), 'mm')
        charter = difficulty.note_designer
        if coloumWidth(charter) > 19:
            charter = changeColumnWidth(charter, 18) + '...'
        mr.draw(310, 590 + spacing, 20, charter, default_color, 'mm')
        notes = difficulty.notes
        note_values = [
            notes.total,
            notes.tap,
            notes.hold,
            notes.slide,
            notes.touch if music.type == 'DX' else '-',
            notes.brk,
        ]
        for n in range(6):
            fn.draw(480 + 122 * n, 590 + spacing, 25, note_values[n] if n < len(note_values) else '-', default_color, 'mm')
        if num > 1:
            ra = get_best_rating(difficulty.level_value)
            for _n, value in enumerate(ra):
                size = 22
                if not calc:
                    rating = value
                elif not isfull:
                    size = 17
                    rating = format_rating_gain(value, value)
                elif value > bestlist[-1].ra:
                    new = newbestscore(str(music.song_id), num, value, bestlist)
                    rating = format_rating_gain(value, new)
                    if new:
                        size = 17
                else:
                    rating = value
                fn.draw(295 + 125 * _n, 1017 + 46 * (num - 2), size, rating, default_color, 'mm')
    draw_text_with_font_fallback(
        dr, 600, 1220, 25,
        f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT',
        default_color, FOTNEWRODIN, SIYUAN, 'mm', 3, (255, 255, 255, 255),
    )
    return MessageSegment.image(image_to_base64(im))


async def draw_music_banquet_info(music: Song) -> MessageSegment:
    """绘制宴会場谱面信息"""
    from .maimaidx_user import Theme

    im = Image.open(maimaidir / 'chart_info_enkaijou.png')
    dr = ImageDraw.Draw(im)
    fn = DrawText(dr, FOTNEWRODIN)

    stroke_color = (210, 57, 174, 255)
    kanji_bg = Image.open(maimaidir / 'utg_kanji.png')

    im.alpha_composite(kanji_bg, (140, 660 if music.is_buddy else 730))
    if music.is_buddy:
        player_path = maimaidir / 'utg_2p.png'
        p_y = 715
        base_y = 820
        step_y = 100
        im.alpha_composite(Image.open(maimaidir / 'utg_buddy.png'), (255, 660))
    else:
        player_path = maimaidir / 'utg_1p.png'
        p_y = 785
        base_y = 890
        step_y = 0

    im.alpha_composite(Image.open(player_path).convert('RGBA'), (98, p_y))

    # logo
    im.alpha_composite(Image.open(themed_path(Theme.PRISM_PLUS, 'logo.png')).resize((249, 120)), (10, 35))
    # new
    if music.isnew:
        im.alpha_composite(Image.open(maimaidir / 'UI_CMN_TabTitle_NewSong.png').resize((249, 120)), (950, 165))
    # cover
    im.alpha_composite(Image.open(music_picture(music.song_id)).resize((242, 242)), (133, 246))
    # version
    im.alpha_composite(Image.open(maimaidir / f'{music.version_str}.png').resize((182, 90)), (800, 415))

    fn.draw(216, p_y - 28, 18, music.kanji or '', anchor='mm')
    title = music.song_name
    if coloumWidth(title) > 36:
        title = changeColumnWidth(title, 35) + '...'
    fn.draw(405, 265, 28, title, anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    artist = music.artist
    if coloumWidth(artist) > 50:
        artist = changeColumnWidth(artist, 49) + '...'
    fn.draw(407, 320, 20, artist, anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(460, 393, 24, music.bpm, anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(405, 475, 22, f'ID {music.song_id}', anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(680, 475, 22, music.genre, anchor='mm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(595, 595, 25, music.description or '', anchor='mm')
    fn.draw(180, p_y + 28, 24, f'Lv. {music.difficulties[0].level}', anchor='mm', stroke_width=3, stroke_fill=stroke_color)

    note_fields = ('total', 'tap', 'hold', 'slide', 'touch', 'brk')
    for idx, difficulty in enumerate(music.difficulties):
        notes = difficulty.notes
        note_vals = [
            notes.total,
            notes.tap,
            notes.hold,
            notes.slide,
            notes.touch if music.type == 'DX' else '-',
            notes.brk,
        ]
        for n, field in enumerate(note_fields):
            fn.draw(
                330 + 140 * n, base_y + step_y * idx, 25,
                note_vals[n] if n < len(note_vals) else '-',
                anchor='mm', stroke_width=3, stroke_fill=stroke_color,
            )
    draw_text_with_font_fallback(
        dr, 600, 1100, 25,
        f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT',
        stroke_color, FOTNEWRODIN, SIYUAN, 'mm', 3, (255, 255, 255, 255),
    )
    return MessageSegment.image(image_to_base64(im))


async def draw_music_play_data(qqid: int, music_id: str) -> Union[str, MessageSegment]:
    """
    谱面游玩
    
    Params:
        `qqid`: QQID
        `music_id`: 曲目ID
    Returns:
        `Union[str, MessageSegment]`
    """
    from .maimaidx_source import get_music_record, get_service
    from .maimaidx_user import userstore

    theme = userstore.get(qqid).theme
    try:
        data = await get_music_record(qqid, music_id)
        if not data:
            raise MusicNotPlayError

        music = mai.total_list.by_id(music_id)
        diff: List[Union[None, PlayInfoDev, PlayInfoDefault]] = [
            None for _ in music.difficulties
        ]
        for _d in data:
            if _d.level_index < len(diff):
                diff[_d.level_index] = _d
        if all(d is None for d in diff):
            raise MusicNotPlayError
        # OAuth/落雪/开发者接口均带精确字段，按 dev 路径绘制
        from .maimaidx_source import is_lxns

        dev = bool(
            maiApi.divingfish_oauth_configured
            or maiApi.token
            or is_lxns(qqid)
        )

        im = Image.open(themed_path(theme, 'play_info.png')).convert('RGBA')
    
        dr = ImageDraw.Draw(im)
        tb = DrawText(dr, TBFONT)
        mr = DrawText(dr, SIYUAN)

        im.alpha_composite(Image.open(themed_path(theme, 'logo.png')).resize((249, 120)), (0, 34))
        cover = Image.open(music_picture(music_id))
        im.alpha_composite(cover.resize((300, 300)), (100, 260))
        im.alpha_composite(Image.open(maimaidir / f'info_{category[music.genre]}.png'), (100, 260))
        im.alpha_composite(Image.open(maimaidir / f'{music.version_str}.png').resize((183, 90)), (295, 205))
        im.alpha_composite(Image.open(maimaidir / f'{music.type}.png').resize((55, 20)), (350, 560))
        
        color = theme.color
        artist = music.artist
        if coloumWidth(artist) > 58:
            artist = changeColumnWidth(artist, 57) + '...'
        mr.draw(255, 595, 12, artist, color, 'mm')
        title = music.song_name
        if coloumWidth(title) > 38:
            title = changeColumnWidth(title, 37) + '...'
        mr.draw(255, 622, 18, title, color, 'mm')
        tb.draw(160, 720, 22, music.song_id, color, 'mm')
        tb.draw(380, 720, 22, music.bpm, color, 'mm')
        tb.draw(1140, 737, 18, f'Data from {get_service(qqid).value}', color, 'rm')

        y = 100
        for num, info in enumerate(diff):
            im.alpha_composite(Image.open(maimaidir / f'd_{num}.png'), (650, 235 + y * num))
            if info:
                im.alpha_composite(Image.open(themed_path(theme, 'ra_dx.png')).resize((102, 44)), (850, 272 + y * num))
                if dev:
                    dxscore = info.dxScore
                    _dxscore = music.difficulties[num].dx_score
                    dxnum = dxScore(dxscore / _dxscore * 100)
                    rating, rate = info.ra, score_Rank_l[info.rate]
                    if dxnum != 0:
                        im.alpha_composite(
                            Image.open(maimaidir / f'UI_GAM_Gauge_DXScoreIcon_0{dxnum}.png').resize((32, 19)), 
                            (851, 296 + y * num)
                        )
                    tb.draw(916, 304 + y * num, 13, f'{dxscore}/{_dxscore}', color, 'mm')
                else:
                    rating, rate = computeRa(music.difficulties[num].level_value, info.achievements, israte=True)
                    
                im.alpha_composite(Image.open(maimaidir / 'fcfs.png'), (965, 265 + y * num))
                if info.fc:
                    im.alpha_composite(
                        Image.open(maimaidir / f'UI_CHR_PlayBonus_{fcl[info.fc]}.png').resize((65, 65)), 
                        (960, 261 + y * num)
                    )
                if info.fs:
                    im.alpha_composite(
                        Image.open(maimaidir / f'UI_CHR_PlayBonus_{fsl[info.fs]}.png').resize((65, 65)), 
                        (1025, 261 + y * num)
                    )
                im.alpha_composite(Image.open(themed_path(theme, 'ra.png')), (1350, 405 + y * num))
                im.alpha_composite(
                    Image.open(themed_path(theme, f'UI_TTR_Rank_{rate}.png')).resize((100, 45)), 
                    (737, 272 + y * num)
                )

                tb.draw(510, 292 + y * num, 42, f'{info.achievements:.4f}%', color, 'lm')
                tb.draw(685, 248 + y * num, 25, music.difficulties[num].level_value, anchor='mm')
                tb.draw(915, 283 + y * num, 18, rating, color, 'mm')
            else:
                tb.draw(685, 248 + y * num, 25, music.difficulties[num].level_value, anchor='mm')
                mr.draw(800, 302 + y * num, 30, '未游玩', color, 'mm')
        if len(diff) == 4:
            mr.draw(800, 302 + y * 4, 30, '没有该难度', color, 'mm')

        draw_text_with_font_fallback(
            dr, 600, 827, 25,
            f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT',
            color, FOTNEWRODIN, SIYUAN, 'mm',
        )
        msg = MessageSegment.image(image_to_base64(im))
        
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        MusicNotPlayError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
        LxnsError,
    ) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg


def calc_achievements_fc(scorelist: Union[List[float], List[str]], lvlist_num: int, isfc: bool = False) -> int:
    r = -1
    obj = range(4) if isfc else achievementList[-6:]
    for __f in obj:
        if len(list(filter(lambda x: x >= __f, scorelist))) == lvlist_num:
            r += 1
        else:
            break
    return r


def draw_rating(rating: str, path: Path) -> MessageSegment:
    """
    绘制指定定数表文字
    
    Params:
        `rating`: 定数
        `path`: 路径
    Returns:
        `MessageSegment`
    """
    im = Image.open(path)
    dr = ImageDraw.Draw(im)
    sy = DrawText(dr, SIYUAN)
    sy.draw(700, 100, 65, f'Level.{rating}   定数表', (124, 129, 255, 255), 'mm', 5, (255, 255, 255, 255))
    return MessageSegment.image(image_to_base64(im))


async def draw_rating_table(qqid: int, rating: str, isfc: bool = False) -> Union[MessageSegment, str]:
    """绘制定数表"""
    from .maimaidx_source import get_plate
    try:
        obj = await get_plate(qqid=qqid)
        
        stat_keys = ['clear', 's', 'sp', 'ss', 'ssp', 'sss', 'sssp',
                     'sync', 'fc', 'fcp', 'ap', 'app', 'fs', 'fsp', 'fsd', 'fsdp']
        statistics = {k: 0 for k in stat_keys}
        fromid = {}
        
        sp = score_Rank[-6:]
        for _d in obj:
            if _d.level != rating:
                continue
            if (id := str(_d.song_id)) not in fromid:
                fromid[id] = {}
            fromid[id][str(_d.level_index)] = {
                'achievements': _d.achievements,
                'fc': _d.fc,
                'level': _d.level
            }
            rate = computeRa(_d.ds, _d.achievements, onlyrate=True).lower()
            if _d.achievements >= 80:
                statistics['clear'] += 1
            if rate in sp:
                r_index = sp.index(rate)
                for _r in range(r_index + 1):
                    statistics[sp[_r]] += 1
            if _d.fc:
                fc_index = combo_rank.index(_d.fc)
                for _f in range(fc_index + 1):
                    statistics[combo_rank[_f]] += 1
            if _d.fs:
                if _d.fs == 'sync':
                    statistics[_d.fs] += 1
                else:
                    fs_index = sync_rank.index(_d.fs)
                    for _s in range(fs_index + 1):
                        statistics[sync_rank[_s]] += 1

        achievements_fc_list: List[Union[float, List[float]]] = []
        lvlist = mai.total_level_data[rating]
        lvnum = sum([len(v) for v in lvlist.values()])
        
        unfinished_bg = Image.open(maimaidir / 'unfinished_1.png')
        complete_bg = Image.open(maimaidir / 'complete_1.png')
        
        bg = ratingdir / f'{rating}.png'
        
        im = Image.open(bg).convert('RGBA')
        dr = ImageDraw.Draw(im)
        tb = DrawText(dr, TBFONT)
        fn = DrawText(dr, FOTNEWRODIN)
        font_color = (114, 188, 254, 255)
        
        # 标题
        fn.draw(495, 160, 70, 'Level.', font_color, 'ld', 8, (255, 255, 255, 255))
        fn.draw(750, 160, 100, rating, font_color, 'ld', 8, (255, 255, 255, 255))
        
        # 统计面板背景
        complete_panel = maimaidir / 'complete.png'
        if complete_panel.exists():
            im.alpha_composite(Image.open(complete_panel).convert('RGBA'), (251, 190))
        
        # 第一行统计
        stats_first_line_x, stats_first_line_y = 534, 238
        tb.draw(394, stats_first_line_y, 30, f"{statistics['clear']}/{lvnum}",
                (124, 129, 255, 255), 'mm', 5, (255, 255, 255, 255))
        for n in range(6):
            x = stats_first_line_x + n * 102
            tb.draw(x, stats_first_line_y, 30, statistics[stat_keys[1 + n]],
                    (124, 129, 255, 255), 'mm', 2, (255, 255, 255, 255))
        # 第二行统计
        stats_second_line_x, stats_second_line_y = 292, 323
        for n in range(9):
            x = stats_second_line_x + n * 102
            tb.draw(x, stats_second_line_y, 30, statistics[stat_keys[7 + n]],
                    (124, 129, 255, 255), 'mm', 2, (255, 255, 255, 255))
        
        # 曲绘叠加层
        START_Y = 450
        for ra, songs in lvlist.items():
            if not songs:
                continue
            for num, music in enumerate(songs):
                row, col = divmod(num, 14)
                x = 140 + col * 85
                cover_y = START_Y + row * 85
                if music.id in fromid and music.lv in fromid[music.id]:
                    if not isfc:
                        score = fromid[music.id][music.lv]['achievements']
                        achievements_fc_list.append(score)
                        rate = computeRa(music.ds, score, onlyrate=True)
                        rank = Image.open(themed_path(Theme.PRISM_PLUS, f'UI_TTR_Rank_{rate}.png')).resize((78, 35))
                        if score >= 100:
                            im.alpha_composite(complete_bg, (x + 1, cover_y + 1))
                        else:
                            im.alpha_composite(unfinished_bg, (x + 1, cover_y + 1))
                        im.alpha_composite(rank, (x, cover_y + 20))
                        continue
                    if _fc := fromid[music.id][music.lv]['fc']:
                        achievements_fc_list.append(combo_rank.index(_fc))
                        fc = Image.open(maimaidir / f'UI_MSS_MBase_Icon_{fcl[_fc]}.png').resize((50, 50))
                        im.alpha_composite(complete_bg, (x + 1, cover_y + 1))
                        im.alpha_composite(fc, (x + 15, cover_y + 13))
            rows = (len(songs) - 1) // 14 + 1
            START_Y += rows * 85 + 30

        if len(achievements_fc_list) == lvnum:
            r = calc_achievements_fc(achievements_fc_list, lvnum, isfc)
            if r != -1:
                pic = fcl[combo_rank[r]] if isfc else score_Rank_l[score_Rank[-6:][r]]
                im.alpha_composite(Image.open(maimaidir / f'UI_MSS_Allclear_Icon_{pic}.png'), (40, 40))
        
        final_im = im.resize((int(im.size[0] * 0.8), int(im.size[1] * 0.8)), Image.Resampling.LANCZOS)
        msg = MessageSegment.image(image_to_base64(final_im))
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
        LxnsError,
    ) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg


async def draw_plate_table(
    qqid: int,
    version: str,
    plan: str,
    page: int = 1,
) -> Union[MessageSegment, str]:
    """绘制版本牌、舞系或霸者完成表。"""
    try:
        if version in platecn:
            version = platecn[version]
        is_wu = version in ['舞', '霸']
        if is_wu and page not in (1, 2):
            return '舞系和霸者完成表仅支持第 1、2 页'

        version_info = version_map.get(version)
        if version_info is None:
            dx_version = plate_to_dx_version.get(version)
            if dx_version is None:
                return f'不支持「{version}」版本的完成表'
            version_info = ([dx_version], version)
        ver, version_name = version_info
        if is_wu:
            version_name = '舞'
        if version_name not in mai.total_plate_id_list:
            return f'「{version}」牌子数据尚未更新，暂时无法查询该牌子完成表'

        music_id_list = mai.total_plate_id_list[version_name]
        music = mai.total_list.by_id_list(music_id_list)
        remaster_ids = (
            {str(song_id) for song_id in mai.total_plate_id_list.get('舞ReMASTER', [])}
            if is_wu
            else set()
        )
        plate_total_num = len(music_id_list)

        def display_index(item: Song) -> int:
            return 4 if str(item.song_id) in remaster_ids and len(item.difficulties) > 4 else 3

        music.sort(
            key=lambda item: item.difficulties[display_index(item)].level_value,
            reverse=True,
        )
        level_by_id = {
            str(item.song_id): item.difficulties[display_index(item)].level
            for item in music
        }
        result_map: Dict[str, Dict[str, List[Optional[PlayInfoDefault]]]] = {
            level: {} for level in reversed(levelList)
        }
        for item in music:
            song_id = str(item.song_id)
            slot_count = 5 if song_id in remaster_ids else 4
            result_map[level_by_id[song_id]][song_id] = [None] * slot_count

        from .maimaidx_source import get_plate

        playerdata = await get_plate(qqid=qqid, version=ver)
        for play in playerdata:
            song_id = str(play.song_id)
            level = level_by_id.get(song_id)
            if level is None:
                continue
            slots = result_map[level][song_id]
            if play.level_index >= len(slots):
                continue
            item = mai.total_list.by_id(song_id)
            play.table_level = [difficulty.level for difficulty in item.difficulties]
            play.ds = item.difficulties[play.level_index].level_value
            slots[play.level_index] = play

        display_levels = list(result_map)
        if is_wu:
            split_index = display_levels.index('13') if '13' in display_levels else len(display_levels)
            display_levels = display_levels[:split_index] if page == 1 else display_levels[split_index:]
        display_level_set = set(display_levels)

        table_name = f'舞-{page}' if is_wu else version
        table_path = platedir / f'{table_name}.png'
        if not table_path.exists():
            return f'未找到「{table_name}」完成表底图，请先执行“更新完成表”'

        finished_bg = [Image.open(maimaidir / f't_{index}.png') for index in range(5)]
        complete_bg = Image.open(maimaidir / 'complete_2.png')
        progress_big = Image.open(maimaidir / 'progress_big.png')
        progress_bg_name = 'plate_progress_wu.png' if is_wu else 'plate_progress.png'
        progress_small_name = 'progress_small_wu.png' if is_wu else 'progress_small.png'
        progress_bg_img = Image.open(maimaidir / progress_bg_name)
        progress_small_img = Image.open(maimaidir / progress_small_name)

        im = Image.open(table_path).convert('RGBA')
        draw = ImageDraw.Draw(im)
        fn = DrawText(draw, FOTNEWRODIN)
        default_color = (124, 129, 255, 255)
        im.alpha_composite(progress_bg_img, (175, 20))

        plate_title = normalize_plate_filename(f'{version}{"極" if plan == "极" else plan}')
        plate_title_path = plate_version_dir / f'{plate_title}.png'
        if plate_title_path.exists():
            im.alpha_composite(Image.open(plate_title_path).resize((1000, 161)), (200, 45))
        else:
            log.warning(f'未找到牌子标题素材：{plate_title}')

        def is_qualified(play: Optional[PlayInfoDefault]) -> bool:
            if play is None:
                return False
            if plan in ['极', '極']:
                return bool(play.fc)
            if plan == '将':
                return play.achievements >= 100
            if plan == '者':
                return play.achievements >= 80
            if plan == '神':
                return play.fc in ['ap', 'app']
            if plan == '舞舞':
                return play.fs in ['fsd', 'fdx', 'fsdp', 'fdxp']
            return False

        def draw_result_icon(play: PlayInfoDefault, x: int, y: int) -> None:
            if plan in ['将', '者']:
                rate = computeRa(play.ds, play.achievements, onlyrate=True)
                icon = Image.open(themed_path(Theme.PRISM_PLUS, f'UI_TTR_Rank_{rate}.png')).resize((80, 36))
                im.alpha_composite(icon, (x, y + 22))
            elif plan in ['极', '極', '神']:
                icon = Image.open(maimaidir / f'UI_CHR_PlayBonus_{fcl[play.fc]}.png').resize((60, 60))
                im.alpha_composite(icon, (x + 10, y + 12))
            elif plan == '舞舞':
                icon = Image.open(maimaidir / f'UI_CHR_PlayBonus_{fsl[play.fs]}.png').resize((60, 60))
                im.alpha_composite(icon, (x + 10, y + 12))

        slot_num = 5 if is_wu else 4
        slot_finished: List[set[int]] = [set() for _ in range(slot_num)]
        finished_songs: set[int] = set()
        qualified_by_id: Dict[str, List[int]] = {}
        for songs in result_map.values():
            for song_id, results in songs.items():
                qualified_slots = [
                    index for index, play in enumerate(results) if is_qualified(play)
                ]
                qualified_by_id[song_id] = qualified_slots
                for index in qualified_slots:
                    slot_finished[index].add(int(song_id))
                if len(qualified_slots) == len(results):
                    finished_songs.add(int(song_id))

        start_y = 490
        for level, songs in result_map.items():
            if not songs:
                continue
            if level not in display_level_set:
                continue
            max_row = 0
            for num, (song_id, results) in enumerate(songs.items()):
                row, col = divmod(num, 12)
                max_row = max(max_row, row)
                x = 180 + col * 96
                y = start_y + row * 96
                qualified_slots = qualified_by_id[song_id]
                for index in qualified_slots:
                    marker = finished_bg[index]
                    if is_wu and len(results) == 5:
                        marker = marker.resize((14, 14))
                        im.alpha_composite(marker, (x + 1 + 16 * index, y + 64))
                    else:
                        im.alpha_composite(marker, (x + 4 + 19 * index, y + 63))
                last_index = len(results) - 1
                if last_index in qualified_slots:
                    im.alpha_composite(complete_bg, (x + 1, y + 1))
                    draw_result_icon(results[last_index], x, y)
            start_y += (max_row + 1) * 96 + 30

        complete_count = len(finished_songs)
        progress = complete_count / plate_total_num if plate_total_num else 0
        if progress:
            im.alpha_composite(progress_big.crop((0, 0, int(993 * progress), 92)), (204, 219))
        complete_text = 'COMPLETED!!!' if complete_count == plate_total_num else f'{complete_count}/{plate_total_num}'
        fn.draw(700, 240, 30, complete_text, default_color, 'mm', 3, (255, 255, 255, 255))
        fn.draw(1190, 240, 30, f'{round(progress * 100, 2)}%', default_color, 'rm', 3, (255, 255, 255, 255))

        stats_start_x = 292 if is_wu else 320
        stats_gap_x = 204 if is_wu else 253
        progress_width = 176 if is_wu else 230
        progress_offset = 88 if is_wu else 115
        stats_color = ScoreBaseImage.id_color.copy()
        remaster_count = len(remaster_ids)
        for index in range(slot_num):
            x = stats_start_x + index * stats_gap_x
            complete_sum = len(slot_finished[index])
            plate_count = remaster_count if index == 4 else plate_total_num
            slot_progress = complete_sum / plate_count if plate_count else 0
            if slot_progress:
                bar = progress_small_img.crop((0, 0, int(progress_width * slot_progress), 46))
                im.alpha_composite(bar, (x - progress_offset, 326))
            fn.draw(x, 300, 40, complete_sum, stats_color[index], 'mm', 4, (255, 255, 255, 255))
            fn.draw(x + progress_offset, 320, 14, f'/{plate_count}', stats_color[index], 'rd', 3, (255, 255, 255, 255))
            fn.draw(x + progress_offset, 343, 20, f'{round(slot_progress * 100, 2)}%', default_color, 'rm', 2, (255, 255, 255, 255))

        return MessageSegment.image(image_to_base64(im))
    except (
        UserNotFoundError,
        UserNotExistsError,
        UserDisabledQueryError,
        TokenError,
        TokenDisableError,
        TokenNotFoundError,
        LxnsError,
    ) as e:
        return str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        return f'未知错误：{type(e)}\n请联系Bot管理员'
