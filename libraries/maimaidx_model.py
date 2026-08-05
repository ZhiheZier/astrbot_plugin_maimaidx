from collections import namedtuple
from typing import Any, List, Optional, Union

from pydantic import BaseModel, Field, field_validator


##### Music
class Stats(BaseModel):
    
    cnt: Optional[float] = None
    diff: Optional[str] = None
    fit_diff: Optional[float] = None
    avg: Optional[float] = None
    avg_dx: Optional[float] = None
    std_dev: Optional[float] = None
    dist: Optional[List[int]] = None
    fc_dist: Optional[List[float]] = None


Notes1 = namedtuple('Notes', ['tap', 'hold', 'slide', 'brk'])
Notes2 = namedtuple('Notes', ['tap', 'hold', 'slide', 'touch', 'brk'])


class Chart(BaseModel):
    
    notes: Union[Notes1, Notes2]
    charter: str = None

    @field_validator('notes', mode='before')
    @classmethod
    def _coerce_notes(cls, v: Any):
        if isinstance(v, (Notes1, Notes2)):
            return v
        if isinstance(v, (list, tuple)):
            if len(v) == 4:
                return Notes1(*v)
            if len(v) >= 5:
                return Notes2(*v[:5])
        return v


class BasicInfo(BaseModel):
    
    title: str
    artist: str
    genre: str
    bpm: int
    release_date: Optional[str] = ''
    version: str = Field(alias='from')
    is_new: bool


class Music(BaseModel):
    
    id: str
    title: str
    type: str
    ds: List[float]
    level: List[str]
    cids: List[int] = []
    charts: List[Chart]
    basic_info: BasicInfo
    stats: Optional[List[Optional[Stats]]] = []
    diff: Optional[List[int]] = []
    # 合并曲库扩展字段（来自落雪 / Song）
    version_int: int = 0
    kanji: Optional[str] = None
    description: Optional[str] = None
    is_buddy: Optional[bool] = None


class RaMusic(BaseModel):
    
    id: str
    ds: float
    lv: str
    lvp: str
    type: str


##### API
class APIResult(BaseModel):
    
    code: int = 0
    content: Union[dict, list, str]


##### Aliases
class Alias(BaseModel):
    
    SongID: int
    Name: str
    Alias: List[str]


class StatusBase(BaseModel):
    
    SongID: int
    ApplyUID: int
    ApplyAlias: str


class Approved(StatusBase):
    
    Tag: str
    Name: str
    GroupID: Optional[int] = None
    WSUUID: Optional[str] = None


class AliasStatus(StatusBase):
    
    Tag: str
    Name: str
    Time: str
    AgreeVotes: Optional[int] = 0
    Votes: int

class Reviewed(StatusBase):

    Tag: str
    Name: str


class PushAliasStatus(BaseModel):
    model_config = {"populate_by_name": True}

    Type: str = Field(alias="type")
    Status: Union[AliasStatus, Approved, Reviewed] = Field(alias="status")


##### Guess
class GuessData(BaseModel):
    
    music: Music
    img: str
    answer: List[str]
    end: bool = False


class GuessDefaultData(GuessData):
    
    options: List[str]


class GuessPicData(GuessData): ...


class Switch(BaseModel):

    enable: List[str] = []  # 使用字符串类型，group_id 在 AstrBotMessage 中是字符串
    disable: List[str] = []  # 使用字符串类型，group_id 在 AstrBotMessage 中是字符串


class GuessSwitch(Switch): ...


##### AliasesPush
class AliasesPush(Switch): ...


##### Best50
class PlayInfo(BaseModel):
    
    achievements: float
    fc: str = ''
    fs: str = ''
    level: str
    level_index: int
    title: str
    type: str
    ds: float = 0
    dxScore: int = 0
    ra: int = 0
    rate: str = ''


class ChartInfo(PlayInfo):
    
    level_label: str
    song_id: int


class Data(BaseModel):
    
    sd: Optional[List[ChartInfo]] = None
    dx: Optional[List[ChartInfo]] = None


class _UserInfo(BaseModel):
    
    additional_rating: Optional[int]
    nickname: Optional[str]
    plate: Optional[str] = None
    rating: Optional[int]
    username: Optional[str]


class UserInfo(_UserInfo):
    
    charts: Optional[Data]

class PlayInfoDefault(PlayInfo):
    
    song_id: int = Field(alias='id')
    table_level: List[int] = []


class PlayInfoDev(ChartInfo): ...


class TableData(BaseModel):
    
    achievements: float
    fc: str = ''


class PlanInfo(BaseModel):
    
    completed: Union[PlayInfoDefault, PlayInfoDev] = None
    unfinished: Union[PlayInfoDefault, PlayInfoDev] = None


class RiseScore(BaseModel):
    
    song_id: int
    title: str
    type: str
    level_index: int
    ds: float
    ra: int
    rate: str
    achievements: float
    oldra: Optional[int] = 0
    oldrate: Optional[str] = 'D'
    oldachievements: Optional[float] = 0


##### Dev
class UserInfoDev(_UserInfo):
    
    records: Optional[List[PlayInfoDev]] = None


##### Rank
class UserRanking(BaseModel):
    
    username: str
    ra: int