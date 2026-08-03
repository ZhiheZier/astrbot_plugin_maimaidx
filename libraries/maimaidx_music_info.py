import copy

from .. import MessageSegment, get_botname
from .maimai_best_50 import *
from .maimaidx_lxns import LxnsError
from .maimaidx_music import Music, mai


def newbestscore(song_id: str, lv: int, value: int, bestlist: List[ChartInfo]) -> int:
    for v in bestlist:
        if song_id == str(v.song_id) and lv == v.level_index:
            if value >= v.ra:
                return value - v.ra
            else:
                return 0
    return value - bestlist[-1].ra


async def draw_music_info(
    music: Music, 
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
    theme = userstore.get(int(qqid)).theme if qqid else Theme.PRISM_PLUS
    try:
        if qqid:
            if user is None:
                from .maimaidx_source import get_player_b50_userinfo

                player = await get_player_b50_userinfo(qqid=qqid)
            else:
                player = user
            if music.basic_info.version == list(plate_to_dx_version.values())[-1]:
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
    if music.basic_info.genre == '宴会場':
        return await draw_music_banquet_info(music)

    im = Image.open(themed_path(theme, 'chart_info.png')).convert('RGBA')
    dr = ImageDraw.Draw(im)
    mr = DrawText(dr, SIYUAN)
    tb = DrawText(dr, TBFONT)
    fn = DrawText(dr, FOTNEWRODIN)

    default_color = theme.color

    im.alpha_composite(Image.open(themed_path(theme, 'logo.png')).resize((249, 120)), (65, 25))
    if music.basic_info.is_new:
        im.alpha_composite(Image.open(maimaidir / 'UI_CMN_TabTitle_NewSong.png').resize((249, 120)), (842, 100))
    songbg = Image.open(music_picture(music.id)).resize((242, 242))
    im.alpha_composite(songbg, (133, 197))
    im.alpha_composite(Image.open(maimaidir / f'{music.basic_info.version}.png').resize((182, 90)), (800, 370))
    im.alpha_composite(Image.open(maimaidir / f'{music.type}.png').resize((80, 30)), (295, 410))

    title = music.title
    if coloumWidth(title) > 40:
        title = changeColumnWidth(title, 39) + '...'
    fn.draw(405, 220, 28, title, default_color, 'lm')
    artist = music.basic_info.artist
    if coloumWidth(artist) > 50:
        artist = changeColumnWidth(artist, 49) + '...'
    fn.draw(407, 265, 20, artist, default_color, 'lm')
    fn.draw(460, 345, 24, music.basic_info.bpm, default_color, 'lm')
    fn.draw(405, 435, 22, f'ID {music.id}', default_color, 'lm')
    mr.draw(665, 435, 24, music.basic_info.genre, default_color, 'mm')

    for num, v in enumerate(music.charts):
        if num == 4:
            color = (255, 255, 255, 255)
        else:
            color = (255, 255, 255, 255)
        spacing = 70 * num
        fn.draw(120, 590 + spacing, 22, f'{music.level[num]}({music.ds[num]})', color, 'mm')
        fitting = f'{round(music.stats[num].fit_diff, 2):.2f}' if music.stats and music.stats[num] else '-'
        fn.draw(120, 613 + spacing, 15, fitting, default_color, 'mm')
        charter = music.charts[num].charter
        if coloumWidth(charter) > 19:
            charter = changeColumnWidth(charter, 18) + '...'
        mr.draw(310, 590 + spacing, 20, charter, default_color, 'mm')
        notes = list(music.charts[num].notes)
        note_values = [sum(notes)] + list(notes)
        if len(notes) == 4:
            note_values.insert(4, '-')
        for n in range(6):
            fn.draw(480 + 122 * n, 590 + spacing, 25, note_values[n] if n < len(note_values) else '-', default_color, 'mm')
        if num > 1:
            ra = sorted([computeRa(music.ds[num], r) for r in achievementList[-6:]], reverse=True)
            for _n, value in enumerate(ra):
                size = 22
                if not calc:
                    rating = value
                elif not isfull:
                    size = 17
                    rating = f'{value}(+{value})'
                elif value > bestlist[-1].ra:
                    new = newbestscore(music.id, num, value, bestlist)
                    if new == 0:
                        rating = value
                    else:
                        size = 17
                        rating = f'{value}(+{new})'
                else:
                    rating = value
                fn.draw(295 + 125 * _n, 1017 + 46 * (num - 2), size, rating, default_color, 'mm')
    fn.draw(600, 1220, 25, f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT', default_color, 'mm', 3, (255, 255, 255, 255))
    return MessageSegment.image(image_to_base64(im))


async def draw_music_banquet_info(music: Music) -> MessageSegment:
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
    if music.basic_info.is_new:
        im.alpha_composite(Image.open(maimaidir / 'UI_CMN_TabTitle_NewSong.png').resize((249, 120)), (950, 165))
    # cover
    im.alpha_composite(Image.open(music_picture(music.id)).resize((242, 242)), (133, 246))
    # version
    im.alpha_composite(Image.open(maimaidir / f'{music.basic_info.version}.png').resize((182, 90)), (800, 415))

    fn.draw(216, p_y - 28, 18, music.kanji or '', anchor='mm')
    title = music.title
    if coloumWidth(title) > 36:
        title = changeColumnWidth(title, 35) + '...'
    fn.draw(405, 265, 28, title, anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    artist = music.basic_info.artist
    if coloumWidth(artist) > 50:
        artist = changeColumnWidth(artist, 49) + '...'
    fn.draw(407, 320, 20, artist, anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(460, 393, 24, music.basic_info.bpm, anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(405, 475, 22, f'ID {music.id}', anchor='lm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(680, 475, 22, music.basic_info.genre, anchor='mm', stroke_width=3, stroke_fill=stroke_color)
    fn.draw(595, 595, 25, music.description or '', anchor='mm')
    fn.draw(180, p_y + 28, 24, f'Lv. {music.level[0]}', anchor='mm', stroke_width=3, stroke_fill=stroke_color)

    note_fields = ('total', 'tap', 'hold', 'slide', 'touch', 'brk')
    for idx, chart in enumerate(music.charts):
        notes = list(chart.notes)
        note_vals = [sum(notes)] + notes
        if len(notes) == 4:
            note_vals.insert(4, '-')
        for n, field in enumerate(note_fields):
            fn.draw(
                330 + 140 * n, base_y + step_y * idx, 25,
                note_vals[n] if n < len(note_vals) else '-',
                anchor='mm', stroke_width=3, stroke_fill=stroke_color,
            )
    fn.draw(600, 1100, 25,
            f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} BOT',
            stroke_color, 'mm', 3, (255, 255, 255, 255))
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
    from .maimaidx_source import get_music_record
    from .maimaidx_user import userstore

    theme = userstore.get(int(qqid)).theme
    try:
        data = await get_music_record(qqid, music_id)
        if not data:
            raise MusicNotPlayError

        music = mai.total_list.by_id(music_id)
        diff: List[Union[None, PlayInfoDev, PlayInfoDefault]] = [None for _ in music.ds]
        for _d in data:
            if _d.level_index < len(diff):
                diff[_d.level_index] = _d
        if all(d is None for d in diff):
            raise MusicNotPlayError
        # OAuth/落雪/开发者接口均带精确字段，按 dev 路径绘制
        from .maimaidx_source import is_lxns

        dev = bool(maiApi.token or is_lxns(qqid))

        im = Image.open(themed_path(theme, 'play_info.png')).convert('RGBA')
    
        dr = ImageDraw.Draw(im)
        tb = DrawText(dr, TBFONT)
        mr = DrawText(dr, SIYUAN)

        im.alpha_composite(Image.open(themed_path(theme, 'logo.png')).resize((249, 120)), (0, 34))
        cover = Image.open(music_picture(music_id))
        im.alpha_composite(cover.resize((300, 300)), (100, 260))
        im.alpha_composite(Image.open(maimaidir / f'info_{category[music.basic_info.genre]}.png'), (100, 260))
        im.alpha_composite(Image.open(maimaidir / f'{music.basic_info.version}.png').resize((183, 90)), (295, 205))
        im.alpha_composite(Image.open(maimaidir / f'{music.type}.png').resize((55, 20)), (350, 560))
        
        color = theme.color
        artist = music.basic_info.artist
        if coloumWidth(artist) > 58:
            artist = changeColumnWidth(artist, 57) + '...'
        mr.draw(255, 595, 12, artist, color, 'mm')
        title = music.title
        if coloumWidth(title) > 38:
            title = changeColumnWidth(title, 37) + '...'
        mr.draw(255, 622, 18, title, color, 'mm')
        tb.draw(160, 720, 22, music.id, color, 'mm')
        tb.draw(380, 720, 22, music.basic_info.bpm, color, 'mm')

        y = 100
        for num, info in enumerate(diff):
            im.alpha_composite(Image.open(maimaidir / f'd_{num}.png'), (650, 235 + y * num))
            if info:
                im.alpha_composite(Image.open(themed_path(theme, 'ra_dx.png')).resize((102, 44)), (850, 272 + y * num))
                if dev:
                    dxscore = info.dxScore
                    _dxscore = sum(music.charts[num].notes) * 3
                    dxnum = dxScore(dxscore / _dxscore * 100)
                    rating, rate = info.ra, score_Rank_l[info.rate]
                    if dxnum != 0:
                        im.alpha_composite(
                            Image.open(maimaidir / f'UI_GAM_Gauge_DXScoreIcon_0{dxnum}.png').resize((32, 19)), 
                            (851, 296 + y * num)
                        )
                    tb.draw(916, 304 + y * num, 13, f'{dxscore}/{_dxscore}', color, 'mm')
                else:
                    rating, rate = computeRa(music.ds[num], info.achievements, israte=True)
                    
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
                tb.draw(685, 248 + y * num, 25, music.ds[num], anchor='mm')
                tb.draw(915, 283 + y * num, 18, rating, color, 'mm')
            else:
                tb.draw(685, 248 + y * num, 25, music.ds[num], anchor='mm')
                mr.draw(800, 302 + y * num, 30, '未游玩', color, 'mm')
        if len(diff) == 4:
            mr.draw(800, 302 + y * 4, 30, '没有该难度', color, 'mm')

        mr.draw(600, 827, 22, f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {get_botname()} Bot', color, 'mm')
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


async def draw_plate_table(qqid: int, version: str, plan: str) -> Union[MessageSegment, str]:
    """
    绘制完成表
    
    Params:
        `qqid`: QQID
        `version`: 版本
        `plan`: 计划
    Returns:
        `Union[MessageSegment, str]`
    """
    try:
        if version in platecn:
            version = platecn[version]
        ver, _ver = version_map.get(version, ([plate_to_dx_version[version]], version))

        if _ver not in mai.total_plate_id_list:
            return f'「{version}」牌子数据尚未更新，暂时无法查询该牌子完成表'
        music_id_list = mai.total_plate_id_list[_ver]
        music = mai.total_list.by_id_list(music_id_list)
        plate_total_num = len(music_id_list)
        playerdata: List[PlayInfoDefault] = []
        
        from .maimaidx_source import get_plate
        obj = await get_plate(qqid=qqid, version=ver)
        for _d in obj:
            if _d.song_id not in music_id_list:
                continue
            _music = mai.total_list.by_id(_d.song_id)
            _d.table_level = _music.level
            _d.ds = _music.ds[_d.level_index]
            playerdata.append(_d)

        ra: Dict[str, Dict[str, List[Optional[PlayInfoDefault]]]] = {}
        """
        {
            "14+": {
                "365": [None, None, None, PlayInfoDefault, None],
                ...
            },
            "14": {
                ...
            }
        }
        """
        music.sort(key=lambda x: x.ds[3], reverse=True)
        number = 4 if version not in ['霸', '舞'] else 5
        for _m in music:
            if _m.level[3] not in ra:
                ra[_m.level[3]] = {}
            ra[_m.level[3]][_m.id] = [None for _ in range(number)]
        for _d in playerdata:
            if number == 4 and _d.level_index == 4:
                continue
            ra[_d.table_level[3]][str(_d.song_id)][_d.level_index] = _d
        
        finished_bg = [Image.open(maimaidir / f't_{_}.png') for _ in range(5)]
        unfinished_bg = Image.open(maimaidir / 'unfinished_2.png')
        complete_bg = Image.open(maimaidir / 'complete_2.png')
        progress_big = Image.open(maimaidir / 'progress_big.png')
        progress_bg_img = Image.open(maimaidir / 'plate_progress.png') if (maimaidir / 'plate_progress.png').exists() else None
        progress_small_img = Image.open(maimaidir / 'progress_small.png') if (maimaidir / 'progress_small.png').exists() else None

        im = Image.open(platedir / f'{version}.png')
        draw = ImageDraw.Draw(im)
        mr = DrawText(draw, SIYUAN)
        fn = DrawText(draw, FOTNEWRODIN)
        default_color = (124, 129, 255, 255)
        
        # 进度面板背景
        if progress_bg_img:
            im.alpha_composite(progress_bg_img, (175, 20))
        plate_title = normalize_plate_filename(f'{version}{"極" if plan == "极" else plan}')
        plate_title_path = plate_version_dir / f'{plate_title}.png'
        if not plate_title_path.exists():
            plate_title_path = plate_version_dir / f'{version}{"極" if plan == "极" else plan}.png'
        if plate_title_path.exists():
            im.alpha_composite(Image.open(plate_title_path).resize((1000, 161)), (200, 45))
        else:
            log.warning(f'未找到牌子标题素材：{plate_title}')
        
        lv: List[set[int]] = [set() for _ in range(number)]
        finished_songs: set[int] = set()
        START_Y = 490
        if plan == '极' or plan == '極':
            for level in reversed(levelList):
                if level not in ra:
                    continue
                songs = ra[level]
                max_row = 0
                for num, _id in enumerate(songs):
                    row, col = divmod(num, 12)
                    max_row = max(max_row, row)
                    x = 180 + col * 96
                    cover_y = START_Y + row * 96
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or not play.fc: continue
                        if n == 3:
                            finished_songs.add(int(_id))
                            im.alpha_composite(complete_bg, (x + 1, cover_y + 1))
                            fc = Image.open(maimaidir / f'UI_CHR_PlayBonus_{fcl[play.fc]}.png').resize((60, 60))
                            im.alpha_composite(fc, (x + 10, cover_y + 12))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 4 + 19 * n, cover_y + 63))
                START_Y += (max_row + 1) * 96 + 30
        if plan == '将':
            for level in reversed(levelList):
                if level not in ra:
                    continue
                songs = ra[level]
                max_row = 0
                for num, _id in enumerate(songs):
                    row, col = divmod(num, 12)
                    max_row = max(max_row, row)
                    x = 180 + col * 96
                    cover_y = START_Y + row * 96
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or play.achievements < 100: continue
                        if n == 3:
                            finished_songs.add(int(_id))
                            im.alpha_composite(complete_bg if play.achievements >= 100 else unfinished_bg, (x + 1, cover_y + 1))
                            rate = computeRa(play.ds, play.achievements, onlyrate=True)
                            rank = Image.open(themed_path(Theme.PRISM_PLUS, f'UI_TTR_Rank_{rate}.png')).resize((80, 36))
                            im.alpha_composite(rank, (x, cover_y + 22))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 4 + 19 * n, cover_y + 63))
                START_Y += (max_row + 1) * 96 + 30
        if plan == '神':
            _fc = ['ap', 'app']
            for level in reversed(levelList):
                if level not in ra:
                    continue
                songs = ra[level]
                max_row = 0
                for num, _id in enumerate(songs):
                    row, col = divmod(num, 12)
                    max_row = max(max_row, row)
                    x = 180 + col * 96
                    cover_y = START_Y + row * 96
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or play.fc not in _fc: continue
                        if n == 3:
                            finished_songs.add(int(_id))
                            im.alpha_composite(complete_bg, (x + 1, cover_y + 1))
                            ap = Image.open(maimaidir / f'UI_CHR_PlayBonus_{fcl[play.fc]}.png').resize((60, 60))
                            im.alpha_composite(ap, (x + 10, cover_y + 12))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 4 + 19 * n, cover_y + 63))
                START_Y += (max_row + 1) * 96 + 30
        if plan == '舞舞':
            fs = ['fsd', 'fdx', 'fsdp', 'fdxp']
            for level in reversed(levelList):
                if level not in ra:
                    continue
                songs = ra[level]
                max_row = 0
                for num, _id in enumerate(songs):
                    row, col = divmod(num, 12)
                    max_row = max(max_row, row)
                    x = 180 + col * 96
                    cover_y = START_Y + row * 96
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or play.fs not in fs: continue
                        if n == 3:
                            finished_songs.add(int(_id))
                            im.alpha_composite(complete_bg, (x + 1, cover_y + 1))
                            fsd = Image.open(maimaidir / f'UI_CHR_PlayBonus_{fsl[play.fs]}.png').resize((60, 60))
                            im.alpha_composite(fsd, (x + 10, cover_y + 12))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 4 + 19 * n, cover_y + 63))
                START_Y += (max_row + 1) * 96 + 30
        
        # 进度条与统计面板
        complete_count = len(finished_songs)
        progress = complete_count / plate_total_num if plate_total_num > 0 else 0
        if progress != 0:
            bar = progress_big.crop((0, 0, int(993 * progress), 92))
            im.alpha_composite(bar, (204, 219))
        complete_text = 'COMPLETED!!!' if complete_count == plate_total_num else f'{complete_count}/{plate_total_num}'
        fn.draw(700, 240, 30, complete_text, default_color, 'mm', 3, (255, 255, 255, 255))
        fn.draw(1190, 240, 30, f'{round(progress * 100, 2)}%', default_color, 'rm', 3, (255, 255, 255, 255))
        
        stats_color = ScoreBaseImage.id_color.copy()
        stats_start_x, stats_gap_x, stats_start_y = 320, 253, 300
        for _l in range(number):
            x_pos = stats_start_x + _l * stats_gap_x
            complete_sum_group = len(lv[_l])
            plate_count = plate_total_num
            progress_group = complete_sum_group / plate_count if plate_count > 0 else 0
            if progress_group != 0 and progress_small_img:
                bar_small = progress_small_img.crop((0, 0, int(230 * progress_group), 46))
                im.alpha_composite(bar_small, (x_pos - 115, 326))
            fn.draw(x_pos, stats_start_y, 40, complete_sum_group, stats_color[_l], 'mm', 4, (255, 255, 255, 255))
            fn.draw(x_pos + 115, stats_start_y + 20, 14, f'/{plate_count}', stats_color[_l], 'rd', 3, (255, 255, 255, 255))
            fn.draw(x_pos + 115, 343, 20, f'{round(progress_group * 100, 2)}%', default_color, 'rm', 2, (255, 255, 255, 255))
        
        msg = MessageSegment.image(image_to_base64(im))
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