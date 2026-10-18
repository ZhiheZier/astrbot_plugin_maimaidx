import re

from astrbot.api.event import AstrMessageEvent

from .mai_base import convert_message_segment_to_chain, get_plain_message_text
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_tolerance import draw_tolerance_table


DIFFICULTIES = {
    '绿': 0, '黄': 1, '红': 2, '紫': 3, '白': 4,
    'basic': 0, 'advanced': 1, 'expert': 2, 'master': 3,
    'remaster': 4, 're:master': 4, 're：master': 4,
}
_DIFFICULTY_PATTERN = '|'.join(re.escape(key) for key in sorted(DIFFICULTIES, key=len, reverse=True))

def parse_chart_request(text: str, command: str) -> tuple[int, str] | None:
    match = re.fullmatch(
        rf'/?{re.escape(command)}\s*({_DIFFICULTY_PATTERN})(?:谱)?\s*(.+)',
        text.strip(), re.IGNORECASE,
    )
    if match is None or not match.group(2).strip():
        return None
    return DIFFICULTIES[match.group(1).lower()], match.group(2).strip()


def parse_tolerance_request(text: str) -> tuple[int, str, str] | None:
    request = parse_chart_request(text, '容错计算')
    if request is None:
        return None
    level_index, query = request
    mode = '101-'
    parts = query.rsplit(maxsplit=1)
    if parts[-1] in ('0+', '100-', '101-'):
        if len(parts) == 1:
            return None
        query, mode = parts
    return level_index, query, mode


async def find_tolerance_songs(query: str):
    """Use the song repository and aliases used by single-song queries."""
    if match := re.fullmatch(r'(?:id\s*)?([0-9]+)', query, re.IGNORECASE):
        song = mai.total_list.by_id(match.group(1))
        return [song] if song is not None else []

    name = query.lower()
    aliases = getattr(mai, 'total_alias_list', None)
    matches = aliases.by_alias(name) if aliases else []
    songs = {
        song.song_id: song for item in (matches or [])
        if (song := mai.total_list.by_id(item.SongID)) is not None
    }
    if songs:
        return list(songs.values())
    exact = [song for song in mai.total_list if song.song_name.lower() == name]
    if exact:
        return exact
    similar = list(mai.total_list.filter(title_search=name))
    if similar:
        return similar
    try:
        matches = await maiApi.get_songs(name)
    except Exception:
        return []
    return list({
        song.song_id: song for item in (matches or [])
        if (song := mai.total_list.by_id(item.SongID)) is not None
    }.values())


async def resolve_song_chart(level_index: int, query: str):
    if not getattr(mai, 'total_list', None):
        return '歌曲数据未加载，请稍后再试或联系管理员'
    songs = await find_tolerance_songs(query)
    if not songs:
        return f'未找到「{query}」对应的歌曲，请检查歌曲ID、别名或名称'
    if len(songs) > 1:
        candidates = '\n'.join(
            f'{song.song_id}：{song.song_name} [{song.type}]'
            for song in sorted(songs, key=lambda item: item.song_id)[:30]
        )
        return (
            f'找到 {len(songs)} 首歌曲，请使用歌曲ID指定曲目：\n{candidates}'
            + ('\n结果较多，仅显示前30首，请缩小查询范围。' if len(songs) > 30 else '')
        )
    song = songs[0]
    difficulty = next((d for d in song.difficulties if d.level_index == level_index), None)
    if difficulty is None:
        return '该乐曲没有这个难度'
    return song, difficulty


async def tolerance_handler(event: AstrMessageEvent):
    request = parse_tolerance_request(get_plain_message_text(event))
    if request is None:
        yield event.plain_result(
            '格式：容错计算 难度 歌曲ID/别名/名称 [101-/100-/0+]\n'
            '默认101-，例如：容错计算 紫799、容错计算 紫 799 0+、容错计算 Master 歌名 100-'
        )
        return
    level_index, query, mode = request
    chart = await resolve_song_chart(level_index, query)
    if isinstance(chart, str):
        yield event.plain_result(chart)
        return
    song, difficulty = chart
    try:
        picture = await draw_tolerance_table(song, difficulty, mode)
    except ValueError as exc:
        yield event.plain_result(str(exc))
        return
    yield event.chain_result(convert_message_segment_to_chain(picture))
