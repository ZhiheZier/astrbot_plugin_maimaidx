import uuid
from pathlib import Path
from typing import Dict, List

from astrbot.api import logger

### 必须
log = logger
loga = logger

# BOTNAME 变量，会在插件初始化时从配置中读取并更新
_BOTNAME = "Bot"

# 是否使用“引用回复”（Reply）组件。默认开启，让多数指令更好用；
# 如不需要，可在 AstrBot 插件配置里关闭。
_ENABLE_REPLY = True


def get_botname():
    """获取机器人名称"""
    return _BOTNAME


def is_reply_enabled() -> bool:
    """是否启用引用回复（Reply）"""
    return bool(_ENABLE_REPLY)


# 为了向后兼容，保留 BOTNAME 作为属性访问
# 其他模块应该使用 get_botname() 函数或通过模块访问 BOTNAME
BOTNAME = _BOTNAME


# 兼容 MessageSegment 类，避免依赖 hoshino 包
class MessageSegment:
    """兼容 hoshino.typing.MessageSegment 的类"""

    def __init__(self, type: str, data: dict):
        self.type = type
        self.data = data

    @staticmethod
    def image(file: str) -> "MessageSegment":
        """
        创建图片消息段

        Args:
            file: 图片文件路径、URL 或 base64 字符串

        Returns:
            MessageSegment 对象
        """
        return MessageSegment("image", {"file": file})

    @staticmethod
    def text(text: str) -> "MessageSegment":
        """
        创建文本消息段

        Args:
            text: 文本内容

        Returns:
            MessageSegment 对象
        """
        return MessageSegment("text", {"text": text})

    def __str__(self):
        return f"MessageSegment(type={self.type}, data={self.data})"

    def __repr__(self):
        return self.__str__()

VOTE_URL = "https://www.yuzuchan.moe/vote"
public_addr = VOTE_URL

# ws
UUID = uuid.uuid1()

# echartsjs
SNAPSHOT_JS = (
    "echarts.getInstanceByDom(document.querySelector('div[_echarts_instance_]'))."
    "getDataURL({type: 'PNG', pixelRatio: 2, excludeComponents: ['toolbox']})"
)

Root: Path = Path(__file__).parent
static: Path = Root / "static"
font_dir: Path = static / "font"
data_dir: Path = static / "data"
mai_dir: Path = static / "mai"
pic_dir: Path = mai_dir / "pic"
cover_dir: Path = mai_dir / "cover"
plate_dir: Path = mai_dir / "plate"
shougou_dir: Path = mai_dir / "shougou"
plate_version_dir: Path = mai_dir / "plate_version"
plate_table_dir: Path = mai_dir / "plate_table"
rating_table_dir: Path = mai_dir / "rating_table"

data_dir.mkdir(parents=True, exist_ok=True)
plate_table_dir.mkdir(parents=True, exist_ok=True)
rating_table_dir.mkdir(parents=True, exist_ok=True)

# 路径文件
pie_html_file: Path = static / "temp_pie.html"  # 饼图html文件
guess_file: Path = data_dir / "group_guess_switch.json"  # 猜歌开关群文件
group_alias_file: Path = data_dir / "group_alias_switch.json"  # 别名推送开关群文件
alias_file: Path = data_dir / "music_alias.json"  # 柚子别名暂存文件
local_alias_file: Path = data_dir / "local_music_alias.json"  # 本地别名文件
music_file: Path = data_dir / "music_data.json"  # 曲目暂存文件
chart_file: Path = data_dir / "music_chart.json"  # 谱面数据暂存文件
lxns_music_file: Path = data_dir / "lxns_music_data.json"  # 落雪曲目暂存
lxns_alias_file: Path = data_dir / "lxns_music_alias.json"  # 落雪别名暂存
merge_music_file: Path = data_dir / "merge_music_data.json"  # 合并后曲目暂存
merge_alias_file: Path = data_dir / "merge_music_alias.json"  # 合并后别名暂存
arcades_json: Path = data_dir / "arcades.json"  # 机厅

# 旧命名兼容（本插件其它模块仍在使用）
maimaidir: Path = pic_dir
coverdir: Path = cover_dir
ratingdir: Path = rating_table_dir
platedir: Path = plate_table_dir

# 字体路径
SIYUAN: Path = font_dir / "ResourceHanRoundedCN-Bold.ttf"
SHANGGUMONO: Path = font_dir / "ShangguMonoSC-Regular.otf"
TBFONT: Path = font_dir / "Torus SemiBold.otf"
FOTNEWRODIN: Path = font_dir / "FOT-NewRodin Pro EB.otf"

SONGS_PER_PAGE: int = 25
FORTUNE: List[str] = [
    "拼机",
    "推分",
    "越级",
    "下埋",
    "夜勤",
    "练底力",
    "练手法",
    "打旧框",
    "干饭",
    "抓绝赞",
    "收歌",
    "打大歌",
    "推AP",
]
RANK_SP: List[str] = [
    "d",
    "c",
    "b",
    "bb",
    "bbb",
    "a",
    "aa",
    "aaa",
    "s",
    "sp",
    "ss",
    "ssp",
    "sss",
    "sssp",
]
STATISTICS_KEYS: List[str] = [
    "clear",
    "s",
    "sp",
    "ss",
    "ssp",
    "sss",
    "sssp",
    "sync",
    "fc",
    "fcp",
    "ap",
    "app",
    "fs",
    "fsp",
    "fsd",
    "fsdp",
]
RANK_PLUS: List[str] = [k.replace("p", "+") for k in RANK_SP]
RANK_MAP: Dict[str, str] = {
    k: (k[:-1].upper() + "p" if k.endswith("p") else k.upper()) for k in RANK_SP
}

COMBO_SP: List[str] = ["fc", "fcp", "ap", "app"]
COMBO_PLUS: List[str] = ["fc", "fc+", "ap", "ap+"]
COMBO_MAP: Dict[str, str] = {
    k: (k.upper()[:-1] + "p" if len(k) > 2 and k.endswith("p") else k.upper())
    for k in COMBO_SP
}

SYNC_D_SP: List[str] = ["fs", "fsp", "fsd", "fsdp"]
SYNC_SP: List[str] = ["fs", "fsp", "fdx", "fdxp"]
SYNC_PLUS: List[str] = [k.replace("p", "+") for k in SYNC_SP]
SYNC_MAP: Dict[str, str] = {
    "fs": "FS",
    "fsp": "FSp",
    "fsd": "FSD",
    "fdx": "FSD",
    "fsdp": "FSDp",
    "fdxp": "FSDp",
    "sync": "Sync",
}

DIFFS: List[str] = ["Basic", "Advanced", "Expert", "Master", "Re:Master"]
LEVEL_LIST: List[str] = [
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "7+",
    "8",
    "8+",
    "9",
    "9+",
    "10",
    "10+",
    "11",
    "11+",
    "12",
    "12+",
    "13",
    "13+",
    "14",
    "14+",
    "15",
]
LEVEL_INDEX_MAP: Dict[str, int] = {level: index for index, level in enumerate(LEVEL_LIST)}
ACHIEVEMENT_LIST: List[float] = [
    50.0,
    60.0,
    70.0,
    75.0,
    80.0,
    90.0,
    94.0,
    97.0,
    98.0,
    99.0,
    99.5,
    100.0,
    100.5,
]
BASE_RA_SPP: List[float] = [
    7.0,
    8.0,
    9.6,
    11.2,
    12.0,
    13.6,
    15.2,
    16.8,
    20.0,
    20.3,
    20.8,
    21.1,
    21.6,
    22.4,
]
SD_VERSION: Dict[str, str] = {
    "初": "maimai",
    "真": "maimai PLUS",
    "超": "maimai GreeN",
    "檄": "maimai GreeN PLUS",
    "橙": "maimai ORANGE",
    "暁": "maimai ORANGE PLUS",
    "晓": "maimai ORANGE PLUS",
    "桃": "maimai PiNK",
    "櫻": "maimai PiNK PLUS",
    "樱": "maimai PiNK PLUS",
    "紫": "maimai MURASAKi",
    "菫": "maimai MURASAKi PLUS",
    "堇": "maimai MURASAKi PLUS",
    "白": "maimai MiLK",
    "雪": "MiLK PLUS",
    "輝": "maimai FiNALE",
    "辉": "maimai FiNALE",
}
DX_VERSION: Dict[str, str] = {
    **SD_VERSION,
    "熊": "maimai でらっくす",
    "華": "maimai でらっくす PLUS",
    "华": "maimai でらっくす PLUS",
    "爽": "maimai でらっくす Splash",
    "煌": "maimai でらっくす Splash PLUS",
    "宙": "maimai でらっくす UNiVERSE",
    "星": "maimai でらっくす UNiVERSE PLUS",
    "祭": "maimai でらっくす FESTiVAL",
    "祝": "maimai でらっくす FESTiVAL PLUS",
    "双": "maimai でらっくす BUDDiES",
    "宴": "maimai でらっくす BUDDiES PLUS",
    "镜": "maimai でらっくす PRiSM",
    "彩": "maimai でらっくす PRiSM PLUS",
    # "丸": "maimai でらっくす CiRCLE"
    # "": "maimai でらっくす CiRCLE PLUS"
}
DX_CN_VERSION: Dict[str, tuple] = {
    "舞萌DX": ("熊&华", "maimai でらっくす"),
    "舞萌DX 2021": ("爽&煌", "maimai でらっくす Splash"),
    "舞萌DX 2022": ("宙&星", "maimai でらっくす UNiVERSE"),
    "舞萌DX 2023": ("祭&祝", "maimai でらっくす FESTiVAL"),
    "舞萌DX 2024": ("双&宴", "maimai でらっくす BUDDiES"),
    "舞萌DX 2025": ("镜", "maimai でらっくす PRiSM"),
    "舞萌DX 2026": ("彩", "maimai でらっくす PRiSM PLUS"),
}
ALL_VERSION: List[str] = list(dict.fromkeys(DX_VERSION.values()))
VERSION_MAP = {
    "真": ([SD_VERSION["真"], SD_VERSION["初"]], "真"),
    "超": ([SD_VERSION["超"]], "超"),
    "檄": ([SD_VERSION["檄"]], "檄"),
    "橙": ([SD_VERSION["橙"]], "橙"),
    "暁": ([SD_VERSION["暁"]], "暁"),
    "桃": ([SD_VERSION["桃"]], "桃"),
    "櫻": ([SD_VERSION["櫻"]], "櫻"),
    "紫": ([SD_VERSION["紫"]], "紫"),
    "菫": ([SD_VERSION["菫"]], "菫"),
    "白": ([SD_VERSION["白"]], "白"),
    "雪": ([SD_VERSION["雪"]], "雪"),
    "輝": ([SD_VERSION["輝"]], "輝"),
    "霸": (list(set(SD_VERSION.values())), "舞"),
    "舞": (list(set(SD_VERSION.values())), "舞"),
    "熊": ([DX_VERSION["熊"]], "熊&华"),
    "华": ([DX_VERSION["熊"]], "熊&华"),
    "華": ([DX_VERSION["熊"]], "熊&华"),
    "爽": ([DX_VERSION["爽"]], "爽&煌"),
    "煌": ([DX_VERSION["爽"]], "爽&煌"),
    "宙": ([DX_VERSION["宙"]], "宙&星"),
    "星": ([DX_VERSION["宙"]], "宙&星"),
    "祭": ([DX_VERSION["祭"]], "祭&祝"),
    "祝": ([DX_VERSION["祭"]], "祭&祝"),
    "双": ([DX_VERSION["双"]], "双&宴"),
    "宴": ([DX_VERSION["双"]], "双&宴"),
    "镜": ([DX_VERSION["镜"]], "镜"),
    "彩": ([DX_VERSION["彩"]], "彩"),
    # "丸": ([DX_VERSION["丸"]], "丸"),
    # "": ([DX_VERSION["丸"]], "丸")
}
PLATE_CN = {"晓": "暁", "樱": "櫻", "堇": "菫", "辉": "輝", "华": "華"}
# 查分器姓名框 / 牌子标题 → plate_version 素材文件名用字
# 素材包现状：版本字多为日文繁体，但「镜」用简体；后缀「極」为繁体
PLATE_FILE_NORMALIZE = {
    "鏡": "镜",  # 水鱼日文 → 素材简体
    "极": "極",  # 简体「极」→ 素材「極」
    **PLATE_CN,
}


def normalize_plate_filename(name: str) -> str:
    """将查分器返回的姓名框 / 牌子标题规范为 plate_version 素材文件名。"""
    if not name:
        return ""
    name = str(name).strip()
    for src, dst in PLATE_FILE_NORMALIZE.items():
        name = name.replace(src, dst)
    return name


def resolve_plate_asset(name: str | None, *, default: Path | None = None) -> Path:
    """解析 plate_version 下的素材路径；不存在时回退默认姓名框。"""
    fallback = default if default is not None else (pic_dir / "UI_Plate_550101.png")
    if not name:
        return fallback
    plate_name = normalize_plate_filename(name)
    candidates = [
        plate_version_dir / f"{plate_name}.png",
        plate_version_dir / f"{name}.png",  # 原始名再试一次
    ]
    # 若规范化后仍是简体「镜」以外的可能写法，补一轮反向尝试
    alt = plate_name.replace("镜", "鏡")
    if alt != plate_name:
        candidates.append(plate_version_dir / f"{alt}.png")
    for path in candidates:
        if path.exists():
            return path
    return fallback


CATEGORY: Dict[str, str] = {
    "流行&动漫": "anime",
    "舞萌": "maimai",
    "niconico & VOCALOID": "niconico",
    "东方Project": "touhou",
    "其他游戏": "game",
    "音击&中二节奏": "ongeki",
    "POPSアニメ": "anime",
    "maimai": "maimai",
    "niconicoボーカロイド": "niconico",
    "東方Project": "touhou",
    "ゲームバラエティ": "game",
    "オンゲキCHUNITHM": "ongeki",
    "宴会場": "宴会场",
}

# 旧命名兼容（本插件其它模块仍在使用这些名字）
scoreRank = RANK_PLUS
score_Rank = RANK_SP
score_Rank_l = RANK_MAP
comboRank = COMBO_PLUS
combo_rank = COMBO_SP
fcl = COMBO_MAP
syncRank = SYNC_PLUS
sync_rank = SYNC_D_SP
sync_rank_p = SYNC_SP
fsl = SYNC_MAP
diffs = DIFFS
levelList = LEVEL_LIST
achievementList = ACHIEVEMENT_LIST
BaseRaSpp = BASE_RA_SPP
plate_to_sd_version = SD_VERSION
plate_to_dx_version = DX_VERSION
version_map = VERSION_MAP
platecn = PLATE_CN
category = CATEGORY
