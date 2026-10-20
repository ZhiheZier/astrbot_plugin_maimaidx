"""Group opt-in and song ranking commands."""

import re

from .. import log
from ..libraries.maimaidx_music import mai
from ..libraries.maimaidx_ranking import collect_ranking
from ..libraries.maimaidx_ranking_image import draw_song_ranking
from .mai_base import convert_message_segment_to_chain, get_plain_message_text
from .mai_score import _minfo_songs
from .mai_tolerance import DIFFICULTIES


def ranking_scope(event):
    group = event.message_obj.group_id
    if not group or event.get_platform_name() != 'aiocqhttp':
        return None
    return f'{event.get_self_id()}:{group}'


def _ranking_songs(query):
    exact = _minfo_songs(query)
    if exact or query.isascii() and query.isdigit():
        return exact
    return [song for song in mai.total_list if query.casefold() in song.song_name.casefold()]


def resolve_ranking_chart(argument):
    query = argument.strip()
    if not query:
        return '格式：wmrk 歌曲ID/别名/名称 [难度]，省略难度默认最高难度'
    # Full titles/aliases win over parsing a trailing difficulty or fuzzy title search.
    songs = _minfo_songs(query)
    index = None
    if not songs:
        parts = query.rsplit(maxsplit=1)
        if len(parts) == 2:
            label = parts[1].casefold().removesuffix('谱')
            if label in DIFFICULTIES:
                index, query = DIFFICULTIES[label], parts[0]
                songs = _ranking_songs(query)
    if not songs:
        songs = _ranking_songs(query)
    if not songs:
        return f'未找到「{query}」对应的歌曲；格式：wmrk 歌曲ID/别名/名称 [绿/黄/红/紫/白]'
    if len(songs) != 1:
        candidates = '\n'.join(f'- {song.song_name}（ID: {song.song_id}）' for song in songs[:20])
        return ('❌ 匹配到多个歌曲，请使用更完整的歌曲名、别名或 ID：\n' + candidates
                + ('\n仅显示前20首，请缩小查询范围。' if len(songs) > 20 else ''))
    song = songs[0]
    if not song.difficulties:
        return '该歌曲暂无谱面数据'
    if song.genre in ('宴会場', '宴会场') or song.song_id >= 100000:
        return '暂不支持宴会场谱面排行榜'
    difficulty = (max(song.difficulties, key=lambda item: item.level_index) if index is None
                  else next((item for item in song.difficulties if item.level_index == index), None))
    if difficulty is None:
        return '该乐曲没有这个难度'
    return song, difficulty


async def ranking_membership_handler(event, store):
    scope = ranking_scope(event)
    if scope is None:
        yield event.plain_result('加入/退出排行榜仅在 QQ 群聊中可用')
        return
    joined = get_plain_message_text(event).removeprefix('/').strip() == '加入排行榜'
    try:
        changed = await store.set_member(scope, str(event.get_sender_id()),
                                         event.get_sender_name() or '未命名玩家', joined=joined)
    except (OSError, ValueError):
        yield event.plain_result('排行榜名单保存失败，本次操作未生效，请联系管理员检查')
        return
    if joined:
        message = ('已加入本群排行榜，仅在本群参与排名；wmrk 查询使用你当前选择的查分器。'
                   '\n请确保已绑定水鱼或落雪，发送「退出排行榜」可取消参与。' if changed
                   else '你已加入本群排行榜，无需重复加入')
    else:
        message = '已退出本群排行榜，后续排名不再展示你的成绩' if changed else '你尚未加入本群排行榜'
    yield event.plain_result(message)


async def song_ranking_handler(event, store):
    scope = ranking_scope(event)
    if scope is None:
        yield event.plain_result('wmrk 仅在 QQ 群聊中可用')
        return
    if not getattr(mai, 'total_list', None):
        yield event.plain_result('歌曲数据未加载，请稍后再试或联系管理员')
        return
    argument = re.sub(r'^/?wmrk\s*', '', get_plain_message_text(event), flags=re.IGNORECASE).strip()
    chart = resolve_ranking_chart(argument)
    if isinstance(chart, str):
        yield event.plain_result(chart)
        return
    lock = store.query_lock(scope)
    if lock.locked():
        yield event.plain_result('本群排行榜正在查询中，请稍后再试')
        return
    async with lock:
        try:
            if not await store.members(scope):
                yield event.plain_result('本群尚无人加入排行榜，请发送「加入排行榜」参与')
                return
            song, difficulty = chart
            result = await collect_ranking(store, scope, song, difficulty)
            if not result.rows:
                if result.failed:
                    message = (f'本群暂无可展示的「{song.song_name}」成绩！\n'
                               f'有 {result.failed} 位已加入玩家查询失败，请检查授权或稍后重试。')
                else:
                    message = f'本群暂无人游玩过「{song.song_name}」！'
                yield event.plain_result(message)
                return
            pictures = await draw_song_ranking(song, difficulty, result)
        except Exception as exc:
            log.warning(f'生成群单曲排行榜失败（{type(exc).__name__}）')
            yield event.plain_result('排行榜生成失败，请稍后重试；管理员可检查名单文件、字体和绘图资源')
            return
        for picture in pictures:
            yield event.chain_result(convert_message_segment_to_chain(picture))
