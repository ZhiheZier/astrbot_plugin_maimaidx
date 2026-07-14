import asyncio
import json
import random
import traceback
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

from .. import *
from .image import image_to_base64, music_picture
from .maimaidx_api_data import maiApi
from .maimaidx_error import *
from .maimaidx_merge import LXSongs, merge_alias_data, merge_music_data, song_to_music
from .maimaidx_model import *
from .tool import openfile, writefile


def cross(
    checker: Union[List[str], List[float]], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]], 
    diff: List[int]
) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    if isinstance(elem, List):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if __e in elem:
                diff_ret.append(_j)
                ret = True
    elif isinstance(elem, Tuple):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem[0] <= __e <= elem[1]:
                diff_ret.append(_j)
                ret = True
    else:
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem == __e:
                diff_ret.append(_j)
                ret = True
    return ret, diff_ret


def in_or_equal(
    checker: Union[str, int], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]]
) -> bool:
    if elem is Ellipsis:
        return True
    if isinstance(elem, List):
        return checker in elem
    elif isinstance(elem, Tuple):
        return elem[0] <= checker <= elem[1]
    else:
        return checker == elem


class MusicList(List[Music]):
    
    def by_id(self, music_id: Union[str, int]) -> Optional[Music]:
        for music in self:
            if music.id == str(music_id):
                return music
        return None

    def by_title(self, music_title: str) -> Optional[Music]:
        for music in self:
            if music.title == music_title:
                return music
        return None
    
    def by_plan(
        self, 
        level: str
    ) -> Dict[str, Union[PlanInfo, RaMusic, Dict[int, Union[PlanInfo, RaMusic]]]]:
        lv = defaultdict(dict)
        
        def create_ra_music(music: Music, index: int) -> RaMusic:
            return RaMusic(
                id=music.id, 
                ds=music.ds[index], 
                lv=str(index), 
                lvp=music.level[index], 
                type=music.type
            )
        
        for music in self:
            if level not in music.level:
                continue
            if int(music.id) >= 100000:
                continue
            if music.level.count(level) > 1: # 同曲有相同等级
                lv[music.id] = { 
                    index: create_ra_music(music, index)
                    for index, _lv in enumerate(music.level) 
                    if _lv == level 
                }
            else:
                index = music.level.index(level)
                lv[music.id] = create_ra_music(music, index)
        return dict(lv)
    
    def by_level_list(self) -> Dict[str, Dict[str, List[RaMusic]]]:
        
        def level_range(lv: str) -> range:
            if lv == '15':
                return range(1)
            if lv.endswith('+'):
                return range(9, 5, -1)
            return range(9, -1, -1) if int(lv) <= 5 else range(5, -1, -1)
        
        _level = {
            lv: {f"{lv.rstrip('+')}.{i}": [] for i in level_range(lv)} for lv in levelList
        }
        for music in self:
            if int(music.id) >= 100000:
                continue
            for index, ds in enumerate(music.ds):
                if ds < 7:
                    continue
                ra = RaMusic(
                    id=music.id,
                    ds=ds,
                    lv=str(index),
                    lvp=music.level[index],
                    type=music.type
                )
                _level[music.level[index]][str(ds)].append(ra)
        return _level
    
    def by_id_list(self, music_id_list: List[int]) -> Optional[List[Music]]:
        musicList = []
        for music in self:
            if int(music.id) in music_id_list:
                musicList.append(music)
        return musicList
    
    def random(self) -> Music:
        return random.choice(self)

    def filter(
        self,
        *,
        level: Optional[Union[str, List[str]]] = ...,
        ds: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        title_search: Optional[str] = ...,
        artist_search: Optional[str] = ...,
        charter_search: Optional[str] = ...,
        genre: Optional[Union[str, List[str]]] = ...,
        bpm: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        type: Optional[Union[str, List[str]]] = ...,
        diff: List[int] = ...,
        version: Union[str, List[str]] = ...
    ) -> 'MusicList':
        new_list = MusicList()
        for music in self:
            diff2 = diff
            music = deepcopy(music)
            ret, diff2 = cross(music.level, level, diff2)
            if not ret:
                continue
            ret, diff2 = cross(music.ds, ds, diff2)
            if not ret:
                continue
            ret, diff2 = search_charts(music.charts, charter_search, diff2)
            if not ret:
                continue
            if not in_or_equal(music.basic_info.genre, genre):
                continue
            if not in_or_equal(music.type, type):
                continue
            if not in_or_equal(music.basic_info.bpm, bpm):
                continue
            if not in_or_equal(music.basic_info.version, version):
                continue
            if title_search is not Ellipsis and title_search.lower() not in music.title.lower():
                continue
            if artist_search is not Ellipsis and artist_search.lower() not in music.basic_info.artist.lower():
                continue
            music.diff = diff2
            new_list.append(music)
        return new_list


def search_charts(checker: List[Chart], elem: str, diff: List[int]) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    for _j in (range(len(checker)) if diff is Ellipsis else diff):
        if elem.lower() in checker[_j].charter.lower():
            diff_ret.append(_j)
            ret = True
    return ret, diff_ret


class AliasList(List[Alias]):

    def by_id(self, music_id: Union[str, int]) -> Optional[List[Alias]]:
        alias_music = []
        for music in self:
            if music.SongID == int(music_id):
                alias_music.append(music)
        return alias_music
    
    def by_alias(self, music_alias: str) -> Optional[List[Alias]]:
        alias_list = []
        for music in self:
            if music_alias in music.Alias:
                alias_list.append(music)
        return alias_list


dataerror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/music_data" 
    将内容保存为 "music_data.json" 存放在 "static" 目录下并重启bot
''').strip()
charterror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/chart_stats"
    将内容保存为 "music_chart.json" 存放在 "static" 目录下并重启bot
''').strip()
aliaserror = dedent('''
    本地暂存别名文件为空，请自行使用浏览器访问 "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias" 
    获取别名数据并保存在 "static/music_alias.json" 文件中并重启bot
''').strip()


def _parse_stats_map(chart_stats: dict) -> Dict[str, List[Optional[Stats]]]:
    """水鱼 chart_stats → sid -> Stats 列表。"""
    result: Dict[str, List[Optional[Stats]]] = {}
    for sid, items in (chart_stats.get('charts') or {}).items():
        parsed: List[Optional[Stats]] = []
        for item in items:
            if not item:
                parsed.append(None)
            else:
                try:
                    parsed.append(Stats.model_validate(item))
                except Exception:
                    parsed.append(None)
        result[str(sid)] = parsed
    return result


async def _load_diving_fish() -> Tuple[List[Music], Dict[str, List[Optional[Stats]]]]:
    """拉取水鱼曲库与谱面统计（失败则回退本地缓存）。"""
    try:
        try:
            music_data = await maiApi.music_data()
            await writefile(music_file, music_data)
        except asyncio.exceptions.TimeoutError:
            log.error('maimaiDX曲库数据获取失败，请检查网络环境。已切换至本地暂存文件')
            music_data = await openfile(music_file)
    except FileNotFoundError:
        log.error(dataerror)
        raise FileNotFoundError

    try:
        try:
            chart_stats = await maiApi.chart_stats()
            await writefile(chart_file, chart_stats)
        except asyncio.exceptions.TimeoutError:
            log.error('maimaiDX数据获取错误，请检查网络环境，已切换至本地暂存文件')
            chart_stats = await openfile(chart_file)
    except FileNotFoundError:
        log.error(charterror)
        raise FileNotFoundError

    df_list: List[Music] = []
    for music in music_data:
        try:
            df_list.append(Music.model_validate(music))
        except Exception as e:
            log.error(f'解析水鱼曲目失败 id={music.get("id")}: {e}')
    return df_list, _parse_stats_map(chart_stats)


async def _load_lxns_songs() -> Optional[LXSongs]:
    """有开发者 Token 时拉取落雪曲库；失败则尝试本地缓存。"""
    if not maiApi.config.lxns_dev_token:
        log.warning('未配置落雪开发者 Token，跳过落雪曲库合并')
        return None
    try:
        from .maimaidx_lxns import LxnsAPI

        songs = await LxnsAPI().music_data()
        await writefile(lxns_music_file, songs.model_dump(by_alias=True))
        log.info(f'成功获取落雪曲库：{len(songs.songs)} 首')
        return songs
    except Exception as e:
        log.warning(f'落雪曲库获取失败，尝试本地缓存：{e}')
        if lxns_music_file.exists():
            try:
                return LXSongs.model_validate(await openfile(lxns_music_file))
            except Exception as e2:
                log.warning(f'落雪曲库本地缓存无效：{e2}')
        return None


async def get_music_list() -> Tuple[MusicList, Dict[str, float]]:
    """获取并合并曲库，返回 MusicList（兼容原字段）与定数字典。"""
    df_list, stats_map = await _load_diving_fish()
    lxns_list = await _load_lxns_songs()

    songs, level_value_map = await merge_music_data(
        diving_fish_list=df_list,
        lxns_list=lxns_list,
        stats_map=stats_map,
    )
    total_list = MusicList()
    for song in songs:
        total_list.append(song_to_music(song))
    log.info(f'曲库合并完成：{len(total_list)} 首')
    return total_list, level_value_map


async def _load_lxns_aliases():
    """有开发者 Token 时拉取落雪别名；失败则尝试本地缓存。"""
    from .maimaidx_merge import LXAliases

    if not maiApi.config.lxns_dev_token:
        return None
    try:
        from .maimaidx_lxns import LxnsAPI

        aliases = await LxnsAPI().music_alias_data()
        await writefile(lxns_alias_file, aliases.model_dump())
        log.info(f'成功获取落雪别名：{len(aliases.aliases)} 条')
        return aliases
    except Exception as e:
        log.warning(f'落雪别名获取失败，尝试本地缓存：{e}')
        if lxns_alias_file.exists():
            try:
                return LXAliases.model_validate(await openfile(lxns_alias_file))
            except Exception as e2:
                log.warning(f'落雪别名本地缓存无效：{e2}')
        return None


async def get_music_alias_list() -> AliasList:
    """获取并合并柚子 / 落雪 / 本地别名。"""
    if local_alias_file.exists():
        local_alias_data = await openfile(local_alias_file)
    else:
        local_alias_data = {}

    alias_data: List[Dict[str, Union[int, str, List[str]]]] = []
    try:
        alias_data = await maiApi.get_alias()
        await writefile(alias_file, alias_data)
    except asyncio.exceptions.TimeoutError:
        log.error('获取别名超时。已切换至本地暂存文件')
        alias_data = await openfile(alias_file)
        if not alias_data:
            log.error(aliaserror)
            raise ValueError
    except ServerError as e:
        log.error(e)
        alias_data = await openfile(alias_file)
    except UnknownError:
        log.error('获取所有曲目别名信息错误，请检查网络环境。已切换至本地暂存文件')
        alias_data = await openfile(alias_file)
        if not alias_data:
            log.error(aliaserror)
            raise ValueError

    lxns_aliases = await _load_lxns_aliases()
    merged = await merge_alias_data(alias_data, lxns_aliases, local_alias_data)

    total_alias_list = AliasList()
    for _a in merged:
        if not mai.total_list.by_id(_a['SongID']):
            continue
        # 曲名缺失时用曲库补全
        if not _a.get('Name'):
            music = mai.total_list.by_id(_a['SongID'])
            if music:
                _a['Name'] = music.title
        total_alias_list.append(Alias.model_validate(_a))

    log.info(f'别名合并完成：{len(total_alias_list)} 首')
    return total_alias_list


async def update_local_alias(id: str, alias_name: str) -> bool:
    try:
        if local_alias_file.exists():
            local_alias_data: Dict[str, List[str]] = await openfile(local_alias_file)
        else:
            local_alias_data: Dict[str, List[str]] = {}
        if id not in local_alias_data:
            local_alias_data[id] = []
        
        local_alias_data[id].append(alias_name.lower())
        mai.total_alias_list.by_id(id)[0].Alias.append(alias_name.lower())
        await writefile(local_alias_file, local_alias_data)
        return True
    except Exception as e:
        log.error(f'添加本地别名失败: {e}')
        return False


class MaiMusic:
    
    total_list: MusicList
    """曲目数据"""
    total_alias_list: AliasList
    """别名数据"""
    total_plate_id_list: Dict[str, List[int]]
    """牌子ID列表数据"""
    total_level_data: Dict[str, Dict[str, List[RaMusic]]]
    """等级列表数据"""
    total_level_value_map: Dict[str, float]
    """定数字典，key 为 `song_id-level_index`，例如 `11451-3`"""
    hot_music_ids: List = []
    """游玩次数超过1w次的曲目数据"""
    guess_data: List[Music]
    """猜歌数据"""

    def __init__(self) -> None:
        """封装所有曲目信息以及猜歌数据，便于更新"""
        self.total_level_value_map = {}

    async def get_music(self) -> None:
        """获取所有曲目数据（水鱼 + 落雪合并）"""
        self.total_list, self.total_level_value_map = await get_music_list()
        self.total_level_data = self.total_list.by_level_list()

    async def get_music_alias(self) -> None:
        """获取所有曲目别名"""
        self.total_alias_list = await get_music_alias_list()
        
    async def get_plate_json(self) -> None:
        """获取所有牌子数据"""
        self.total_plate_id_list = await maiApi.get_plate_json()

    def guess(self):
        """初始化猜歌数据"""
        for music in self.total_list:
            if music.stats:
                count = 0
                for stats in music.stats:
                    if stats:
                        count += stats.cnt if stats.cnt else 0
                if count > 10000:
                    self.hot_music_ids.append(music.id)
        self.guess_data = list(filter(lambda x: x.id in self.hot_music_ids, self.total_list))


mai = MaiMusic()


class Guess:
    
    Group: Dict[str, Union[GuessDefaultData, GuessPicData]] = {}  # 使用字符串类型作为键，group_id 在 AstrBotMessage 中是字符串
    switch: GuessSwitch

    def __init__(self) -> None:
        """猜歌类"""
        if not guess_file.exists():
            self.switch = GuessSwitch()
        else:
            self.switch = GuessSwitch.model_validate(
                json.load(open(guess_file, 'r', encoding='utf-8'))
            )
            # 清理数据，确保 enable 和 disable 列表中的值都是字符串类型（兼容旧数据，自动转换）
            try:
                self.switch.enable = [str(x) for x in self.switch.enable if x is not None]
            except (ValueError, TypeError):
                self.switch.enable = []
            try:
                self.switch.disable = [str(x) for x in self.switch.disable if x is not None]
            except (ValueError, TypeError):
                self.switch.disable = []
    
    def start(self, group_id: str):
        """开始猜歌"""
        self.Group[group_id] = self.guessData()

    def startpic(self, group_id: str):
        """开始猜曲绘"""
        self.Group[group_id] = self.guesspicdata()
        
    def calculate_frequency_weights(self, image: Image.Image) -> np.ndarray:
        """
        计算图像的频率权重，用于在图像中选择裁剪区域
        
        Params:
            `image`: PIL.Image.Image, 输入图像
        Returns:
            `np.ndarray` 频率权重矩阵
        """
        gray_image = np.array(image.convert('L'))
        freq = np.fft.fft2(gray_image)
        freq_shift = np.fft.fftshift(freq)
        magnitude = np.abs(freq_shift)
        normalized_magnitude = magnitude / magnitude.max()
        weights = normalized_magnitude ** 2
        return weights
    
    def select_crop_region(
        self, 
        weights: np.ndarray, 
        crop_width: int, 
        crop_height: int, 
        top_p: int
    ) -> Tuple[int, int]:
        h, w = weights.shape
        valid_regions = weights[:h - crop_height + 1, :w - crop_width + 1]
        flattened_weights = valid_regions.flatten()
        threshold = np.percentile(flattened_weights, top_p)
        valid_indices = np.where(flattened_weights >= threshold)[0]
        probabilities = flattened_weights[valid_indices]
        probabilities /= probabilities.sum()
        chosen_index = np.random.choice(valid_indices, p=probabilities)
        top_left_y = chosen_index // valid_regions.shape[1]
        top_left_x = chosen_index % valid_regions.shape[1]
        return top_left_x, top_left_y

    def pic(self, music: Music) -> Image.Image:
        """裁切曲绘"""
        im = Image.open(music_picture(music.id))
        w, h = im.size
        weights = self.calculate_frequency_weights(im)
        scale = random.uniform(0.15, 0.4)  # 裁剪尺寸范围 可在此修改
        w2, h2 = int(w * scale), int(h * scale)
        top_p = min(1.3 - np.power(scale, 0.4), 0.95) * 100
        x, y = self.select_crop_region(weights, w2, h2, top_p)
        im = im.crop((x, y, x + w2, y + h2))
        return im

    def guesspicdata(self) -> GuessPicData:
        """猜曲绘数据"""
        if not mai.guess_data:
            raise ValueError("猜歌数据未初始化，请先调用 mai.guess() 初始化数据")
        music = random.choice(mai.guess_data)
        pic = self.pic(music)
        alias_list = mai.total_alias_list.by_id(music.id)
        if not alias_list or len(alias_list) == 0:
            # 如果没有别名数据，使用歌曲ID和标题作为答案
            answer = [str(music.id), music.title]
        else:
            answer = alias_list[0].Alias.copy() if hasattr(alias_list[0], 'Alias') else [str(music.id), music.title]
            answer.append(str(music.id))
        return GuessPicData(music=music, img=image_to_base64(pic), answer=answer, end=False)

    def guessData(self) -> GuessDefaultData:
        """猜歌数据"""
        if not mai.guess_data:
            raise ValueError("猜歌数据未初始化，请先调用 mai.guess() 初始化数据")
        music = random.choice(mai.guess_data)
        guess_options = random.sample([
            f'的 Expert 难度是 {music.level[2]}',
            f'的 Master 难度是 {music.level[3]}',
            f'的分类是 {music.basic_info.genre}',
            f'的版本是 {music.basic_info.version}',
            f'的艺术家是 {music.basic_info.artist}',
            f'{"不" if music.type == "SD" else ""}是 DX 谱面',
            f'{"没" if len(music.ds) == 4 else ""}有白谱',
            f'的 BPM 是 {music.basic_info.bpm}'
        ], 6)
        alias_list = mai.total_alias_list.by_id(music.id)
        if not alias_list or len(alias_list) == 0:
            # 如果没有别名数据，使用歌曲ID和标题作为答案
            answer = [str(music.id), music.title]
        else:
            answer = alias_list[0].Alias.copy() if hasattr(alias_list[0], 'Alias') else [str(music.id), music.title]
            answer.append(str(music.id))
        pic = self.pic(music)
        return GuessDefaultData(
            music=music, 
            img=image_to_base64(pic), 
            answer=answer, 
            end=False, 
            options=guess_options
        )

    def end(self, group_id: str):
        """结束猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        if group_id in self.Group:
            del self.Group[group_id]

    async def on(self, group_id: str) -> str:
        """开启猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        # 清理 enable 列表，确保所有值都是字符串类型（兼容旧数据）
        self.switch.enable = [str(x) for x in self.switch.enable if x is not None]
        if group_id not in self.switch.enable:
            self.switch.enable.append(group_id)
        # 清理 disable 列表，确保所有值都是字符串类型
        self.switch.disable = [str(x) for x in self.switch.disable if x is not None]
        if group_id in self.switch.disable:
            self.switch.disable.remove(group_id)
        await writefile(guess_file, self.switch.model_dump())
        return '群猜歌功能已开启'

    async def off(self, group_id: str) -> str:
        """关闭猜歌"""
        # group_id 在 AstrBotMessage 中已经是字符串，直接使用
        if group_id not in self.switch.disable:
            self.switch.disable.append(group_id)
        if group_id in self.switch.enable:
            self.switch.enable.remove(group_id)
        if group_id in self.Group:
            self.end(group_id)
        await writefile(guess_file, self.switch.model_dump())
        return '群猜歌功能已关闭'


guess = Guess()


class GroupAlias:

    push: AliasesPush

    @staticmethod
    def _normalize_group_ids(ids) -> List[str]:
        """群号统一为字符串，避免 int/str 混用导致 in 判断失效"""
        return [str(x) for x in ids if x is not None]

    def __init__(self) -> None:
        """别名推送类"""
        if not group_alias_file.exists():
            self.push = AliasesPush()
        else:
            self.push = AliasesPush.model_validate(
                json.load(open(group_alias_file, 'r', encoding='utf-8'))
            )
        # 兼容旧数据：JSON 中可能存成数字，与推送处 str(gid) 比较会永远不匹配
        self.push.enable = self._normalize_group_ids(self.push.enable)
        self.push.disable = self._normalize_group_ids(self.push.disable)

    async def on(self, gid: str) -> str:
        """开启推送"""
        gid = str(gid)
        self.push.enable = self._normalize_group_ids(self.push.enable)
        self.push.disable = self._normalize_group_ids(self.push.disable)
        if gid not in self.push.enable:
            self.push.enable.append(gid)
        if gid in self.push.disable:
            self.push.disable.remove(gid)
        await writefile(group_alias_file, self.push.model_dump())
        return '群别名推送功能已开启'

    async def off(self, gid: str) -> str:
        """关闭推送"""
        gid = str(gid)
        self.push.enable = self._normalize_group_ids(self.push.enable)
        self.push.disable = self._normalize_group_ids(self.push.disable)
        if gid not in self.push.disable:
            self.push.disable.append(gid)
        if gid in self.push.enable:
            self.push.enable.remove(gid)
        await writefile(group_alias_file, self.push.model_dump())
        return '群别名推送功能已关闭'

    async def alias_global_change(self, switch: bool, group_list: List[str]):
        """修改全局开关"""
        group_list = self._normalize_group_ids(group_list)
        if switch:
            self.push.disable.clear()
            self.push.enable.clear()
            self.push.enable.extend(group_list)
        else:
            self.push.enable.clear()
            self.push.disable.clear()
            self.push.disable.extend(group_list)
        await writefile(group_alias_file, self.push.model_dump())


alias = GroupAlias()
