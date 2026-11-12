"""Adapt local PC snapshots to the existing B50 renderer."""

from datetime import datetime, timedelta, timezone

from .. import MessageSegment
from .image import image_to_base64
from .maimai_best_50 import DrawBest, computeRa
from .maimaidx_music import mai
from .maimaidx_model import is_regular_chart
from .maimaidx_play_result import Best50, PlayedResult, Player
from .maimaidx_sync import FC, FS
from .maimaidx_user import Theme


def pc50_to_best50(snapshot):
    old, new = [], []
    for score in snapshot.pc50():
        song = mai.get_song(score.song_id)
        difficulty = mai.get_difficulty(score.song_id, score.level_index)
        level_value = difficulty.level_value if difficulty else 0
        rating, rate = computeRa(level_value, score.achievements, israte=True)
        (new if song.isnew else old).append(PlayedResult(
            song_id=score.song_id,
            song_name=song.song_name if song else f'ID {score.song_id}',
            type=song.type if song else ('DX' if score.song_id >= 10000 else 'SD'),
            level_index=score.level_index, level=difficulty.level if difficulty else '?',
            level_value=level_value, rating=rating, achievements=score.achievements,
            dx_score=score.dx_score, fc=FC[score.combo], fs=FS[score.sync],
            rate=rate.lower(), play_count=score.play_count,
        ))
    return Player(name=snapshot.name, rating=sum(score.rating for score in old + new)), Best50(sd=old, dx=new)


async def draw_pc50(snapshot, *, qqid=None, theme=Theme.PRISM_PLUS):
    player, best50 = pc50_to_best50(snapshot)
    updated = datetime.fromtimestamp(snapshot.updated_at, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    renderer = DrawBest.from_best50(
        player, best50, qqid=qqid, theme=theme, pc50=True,
        pc_total=sum(score.play_count or 0 for score in snapshot.scores
                     if is_regular_chart(score.song_id, score.level_index)), pc_updated_at=updated,
    )
    return MessageSegment.image(image_to_base64(await renderer.draw()))
