"""统一曲库：水鱼 + 落雪合并为 Song，再转回插件现有 Music 以复用渲染。"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .. import DX_CN_VERSION, merge_alias_file, merge_music_file
from .maimaidx_model import (
    BasicInfo,
    Chart,
    Music,
    Notes1,
    Notes2,
    Stats,
)
from .tool import writefile


# ---------------------------------------------------------------------------
# 落雪曲目原始模型（仅合并所需字段）
# ---------------------------------------------------------------------------
class LevelIndex(IntEnum):
    BASIC = 0
    ADVANCED = 1
    EXPERT = 2
    MASTER = 3
    RE_MASTER = 4


class LxNotes(BaseModel):
    total: int = 0
    tap: int = 0
    hold: int = 0
    slide: int = 0
    touch: int = 0
    brk: int = Field(0, alias='break')

    model_config = ConfigDict(populate_by_name=True)


class BuddyNotes(BaseModel):
    left: LxNotes
    right: LxNotes


class SongDifficulty(BaseModel):
    difficulty: int = 0
    level: str = ''
    level_value: float = 0
    note_designer: str = ''
    version: int = 0
    notes: LxNotes


class SongDifficultyUtage(SongDifficulty):
    kanji: str = ''
    description: str = ''
    is_buddy: bool = False
    notes: Union[LxNotes, BuddyNotes]  # type: ignore[assignment]


class SongDifficulties(BaseModel):
    standard: list[SongDifficulty] = []
    dx: list[SongDifficulty] = []
    utage: list[SongDifficultyUtage] = []


class LXSong(BaseModel):
    id: int
    title: str
    artist: str = ''
    genre: str = ''
    bpm: float = 0
    difficulties: SongDifficulties = SongDifficulties()


class LXVersion(BaseModel):
    title: str = ''
    version: int = 0


class LXSongs(BaseModel):
    songs: list[LXSong] = []
    versions: list[LXVersion] = []


# ---------------------------------------------------------------------------
# 统一域模型 Song
# ---------------------------------------------------------------------------
class Notes(BaseModel):
    total: int = 0
    tap: int = 0
    hold: int = 0
    slide: int = 0
    touch: int = 0
    brk: int = 0


class Difficulties(BaseModel):
    level_index: int
    level: str
    level_value: float
    note_designer: str = ''
    notes: Notes
    dx_score: int = 0
    stats: Optional[Stats] = None


class Song(BaseModel):
    song_id: int
    song_name: str
    artist: str = ''
    genre: str = ''
    bpm: float = 0
    version_str: str = ''
    version_int: int = 0
    type: Literal['SD', 'DX'] = 'SD'
    isnew: bool = False
    difficulties: list[Difficulties] = []
    cids: list[int] = []
    kanji: Optional[str] = None
    description: Optional[str] = None
    is_buddy: Optional[bool] = None


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------
def chart_notes_to_domain(notes: Union[Notes1, Notes2, list, tuple]) -> Notes:
    if isinstance(notes, (list, tuple)):
        if len(notes) == 4:
            tap, hold, slide, brk = notes
            touch = 0
        else:
            tap, hold, slide, touch, brk = notes[:5]
    else:
        tap = notes.tap
        hold = notes.hold
        slide = notes.slide
        brk = notes.brk
        touch = getattr(notes, 'touch', 0)
    return Notes(
        tap=tap,
        hold=hold,
        slide=slide,
        touch=touch,
        brk=brk,
        total=tap + hold + slide + touch + brk,
    )


def build_difficulty(
    level_index: int,
    level: str,
    level_value: float,
    note_designer: str,
    notes: Notes,
) -> Difficulties:
    return Difficulties(
        level_index=level_index,
        level=level,
        level_value=level_value,
        note_designer=note_designer or '',
        notes=notes,
        dx_score=notes.total * 3,
        stats=None,
    )


def append_missing_difficulty(song: Song, diffs: list[SongDifficulty]) -> None:
    if len(song.difficulties) >= len(diffs):
        return
    diff = diffs[-1]
    song.difficulties.append(
        build_difficulty(
            level_index=int(diff.difficulty),
            level=diff.level,
            level_value=diff.level_value,
            note_designer=diff.note_designer,
            notes=Notes(
                tap=diff.notes.tap,
                hold=diff.notes.hold,
                slide=diff.notes.slide,
                touch=diff.notes.touch,
                brk=diff.notes.brk,
                total=diff.notes.total
                or (
                    diff.notes.tap
                    + diff.notes.hold
                    + diff.notes.slide
                    + diff.notes.touch
                    + diff.notes.brk
                ),
            ),
        )
    )


async def merge_music_data(
    *,
    diving_fish_list: list[Music],
    lxns_list: Optional[LXSongs],
    stats_map: dict[str, list[Optional[Stats]]],
) -> tuple[list[Song], dict[str, float]]:
    """合并水鱼与落雪曲目，返回 Song 列表与定数字典。"""
    song_map: dict[int, Song] = {}
    level_value_map: dict[str, float] = {}

    for raw in diving_fish_list:
        song_id = int(raw.id)
        song = Song(
            song_id=song_id,
            song_name=raw.title,
            artist=raw.basic_info.artist,
            genre=raw.basic_info.genre,
            bpm=raw.basic_info.bpm,
            version_str=raw.basic_info.version,
            type=raw.type if raw.type in ('SD', 'DX') else 'DX',
            isnew=raw.basic_info.is_new,
            cids=list(raw.cids or []),
        )
        for n, ds in enumerate(raw.ds):
            charts = raw.charts[n]
            notes = chart_notes_to_domain(charts.notes)
            song.difficulties.append(
                Difficulties(
                    level_index=n,
                    level=raw.level[n],
                    level_value=ds,
                    note_designer=charts.charter or '',
                    notes=notes,
                    dx_score=notes.total * 3,
                    stats=None,
                )
            )
            level_value_map[f'{song_id}-{n}'] = ds
        song_map[song_id] = song

    if lxns_list is not None and lxns_list.songs:
        versions = lxns_list.versions
        ver_map = {v.version: v.title for v in versions}
        new_version = versions[-1].version if versions else 0

        def set_version(
            raw: LXSong,
            ver_type: str,
            sid: int,
            diffs: list[SongDifficulty] | list[SongDifficultyUtage],
        ) -> None:
            if not diffs:
                return
            song = song_map.get(sid)
            base = diffs[0]
            if song is not None:
                song.version_int = base.version
                if isinstance(base, SongDifficultyUtage):
                    song.kanji = base.kanji
                    song.description = base.description
                    song.is_buddy = base.is_buddy
                else:
                    append_missing_difficulty(song, diffs)  # type: ignore[arg-type]
                return

            _ver = base.version
            diff_ver = _ver - _ver % 100
            cn = DX_CN_VERSION.get(ver_map.get(diff_ver, ''), None)
            version_str = cn[-1] if cn else ver_map.get(diff_ver, '')

            if sid > 100000:
                if isinstance(base.notes, BuddyNotes):
                    note_list = [base.notes.left, base.notes.right]
                else:
                    note_list = [base.notes]
                difficulties = [
                    build_difficulty(
                        level_index=n,
                        level=base.level,
                        level_value=base.level_value,
                        note_designer=base.note_designer,
                        notes=Notes(
                            tap=n_.tap,
                            hold=n_.hold,
                            slide=n_.slide,
                            touch=n_.touch,
                            brk=n_.brk,
                            total=n_.total
                            or (n_.tap + n_.hold + n_.slide + n_.touch + n_.brk),
                        ),
                    )
                    for n, n_ in enumerate(note_list)
                ]
            else:
                difficulties = [
                    build_difficulty(
                        level_index=n,
                        level=d.level,
                        level_value=d.level_value,
                        note_designer=d.note_designer,
                        notes=Notes(
                            tap=d.notes.tap,
                            hold=d.notes.hold,
                            slide=d.notes.slide,
                            touch=d.notes.touch,
                            brk=d.notes.brk,
                            total=d.notes.total
                            or (
                                d.notes.tap
                                + d.notes.hold
                                + d.notes.slide
                                + d.notes.touch
                                + d.notes.brk
                            ),
                        ),
                    )
                    for n, d in enumerate(diffs)
                    if not isinstance(d.notes, BuddyNotes)
                ]

            song = Song(
                song_id=sid,
                song_name=raw.title,
                artist=raw.artist,
                genre=raw.genre,
                bpm=raw.bpm,
                version_str=version_str,
                version_int=base.version,
                type=ver_type if ver_type in ('SD', 'DX') else 'DX',
                isnew=bool(new_version and new_version == base.version),
                difficulties=difficulties,
            )
            if isinstance(base, SongDifficultyUtage):
                song.kanji = base.kanji
                song.description = base.description
                song.is_buddy = base.is_buddy
            else:
                append_missing_difficulty(song, diffs)  # type: ignore[arg-type]

            for n, d in enumerate(song.difficulties):
                level_value_map.setdefault(f'{sid}-{n}', d.level_value)
            song_map[sid] = song

        for _raw in lxns_list.songs:
            song_id = _raw.id
            if song_id < 10000:
                if _raw.difficulties.standard:
                    set_version(_raw, 'SD', song_id, _raw.difficulties.standard)
                if _raw.difficulties.dx:
                    set_version(_raw, 'DX', song_id + 10000, _raw.difficulties.dx)
            elif song_id < 100000:
                if _raw.difficulties.dx:
                    set_version(_raw, 'DX', song_id, _raw.difficulties.dx)
                if _raw.difficulties.standard:
                    set_version(_raw, 'SD', song_id - 10000, _raw.difficulties.standard)
            else:
                if _raw.difficulties.utage:
                    set_version(_raw, 'DX', song_id, _raw.difficulties.utage)

    for sid, stat_list in stats_map.items():
        song = song_map.get(int(sid))
        if song is None:
            continue
        for s in stat_list:
            if s is None or not s.diff:
                continue
            for diff in song.difficulties:
                if diff.level == s.diff:
                    diff.stats = s
                    break

    result = sorted(song_map.values(), key=lambda x: x.song_id)
    await writefile(merge_music_file, [s.model_dump() for s in result])
    return result, level_value_map


def _domain_notes_to_chart(notes: Notes) -> Union[Notes1, Notes2]:
    if notes.touch:
        return Notes2(notes.tap, notes.hold, notes.slide, notes.touch, notes.brk)
    return Notes1(notes.tap, notes.hold, notes.slide, notes.brk)


def song_to_music(song: Song) -> Music:
    """Song → 现有 Music，渲染层可继续使用原字段。"""
    charts = [
        Chart(
            notes=_domain_notes_to_chart(d.notes),
            charter=d.note_designer or '',
        )
        for d in song.difficulties
    ]
    n = len(song.difficulties)
    return Music(
        id=str(song.song_id),
        title=song.song_name,
        type=song.type,
        ds=[d.level_value for d in song.difficulties],
        level=[d.level for d in song.difficulties],
        cids=song.cids if song.cids else [0] * n,
        charts=charts,
        basic_info=BasicInfo.model_validate(
            {
                'title': song.song_name,
                'artist': song.artist,
                'genre': song.genre,
                'bpm': int(song.bpm),
                'from': song.version_str,
                'is_new': song.isnew,
            }
        ),
        stats=[d.stats for d in song.difficulties],
        version_int=song.version_int,
        kanji=song.kanji,
        description=song.description,
        is_buddy=song.is_buddy,
    )


# ---------------------------------------------------------------------------
# 别名合并（柚子 + 落雪 + 本地）
# ---------------------------------------------------------------------------
class LXAliasItem(BaseModel):
    song_id: int
    aliases: list[str] = []


class LXAliases(BaseModel):
    aliases: list[LXAliasItem] = []


async def merge_alias_data(
    yuzu_aliases: list[dict],
    lxns_aliases: Optional[LXAliases],
    local_alias_data: Optional[dict[str, list[str]]],
) -> list[dict]:
    """合并别名，返回插件 Alias 兼容结构：SongID / Name / Alias。"""
    alias_map: dict[int, set[str]] = {}
    song_name_map: dict[int, str] = {}

    for item in yuzu_aliases or []:
        try:
            sid = int(item.get('SongID', item.get('song_id')))
        except (TypeError, ValueError):
            continue
        names = item.get('Alias') or item.get('alias') or []
        alias_map.setdefault(sid, set()).update(str(a) for a in names if a)
        name = item.get('Name') or item.get('name') or ''
        if name:
            song_name_map.setdefault(sid, str(name))

    if lxns_aliases is not None:
        for item in lxns_aliases.aliases:
            sid = item.song_id
            # 落雪别名 ID 为标准谱 ID；>1000 的 DX 曲在水鱼侧 +10000
            if sid > 1000:
                sid += 10000
            alias_map.setdefault(sid, set()).update(item.aliases or [])

    if local_alias_data:
        for _a, aliases in local_alias_data.items():
            try:
                sid = int(_a)
            except (TypeError, ValueError):
                continue
            alias_map.setdefault(sid, set()).update(aliases or [])

    result = sorted(
        [
            {
                'SongID': sid,
                'Name': song_name_map.get(sid, ''),
                'Alias': sorted(aliases),
            }
            for sid, aliases in alias_map.items()
            if aliases
        ],
        key=lambda x: x['SongID'],
    )
    await writefile(merge_alias_file, result)
    return result
