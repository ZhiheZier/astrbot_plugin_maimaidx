"""Opt-in, per-group song rankings. No credentials or scores are persisted."""

import asyncio
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .maimaidx_error import MusicNotPlayError
from .maimaidx_play_result import PlayedResult, playinfo_to_played
from .maimaidx_source import get_music_record


@dataclass(frozen=True)
class RankingRow:
    qqid: str
    name: str
    score: PlayedResult
    rank: int = 0


@dataclass(frozen=True)
class RankingResult:
    rows: tuple[RankingRow, ...]
    failed: int = 0
    unplayed: int = 0


class SongRankingStore:
    def __init__(self, path: Path):
        self.path = path
        self._groups = None
        self._lock = asyncio.Lock()
        self.query_locks = {}
        self.query_semaphore = asyncio.Semaphore(3)

    def _read(self):
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        groups = raw.get('groups') if isinstance(raw, dict) else None
        if not isinstance(groups, dict) or any(
            not isinstance(scope, str) or not isinstance(members, dict)
            or any(not isinstance(qq, str) or not isinstance(name, str) for qq, name in members.items())
            for scope, members in groups.items()
        ):
            raise ValueError('排行榜参与名单格式无效，请联系管理员检查 song_ranking.json')
        return groups

    def _write(self, groups):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             prefix='ranking-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'groups': groups}, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def _load(self):
        if self._groups is None:
            self._groups = await asyncio.to_thread(self._read)

    async def members(self, scope: str) -> dict[str, str]:
        async with self._lock:
            await self._load()
            return dict(self._groups.get(scope, {}))

    async def set_member(self, scope: str, qqid: str, name: str, *, joined: bool) -> bool:
        async with self._lock:
            await self._load()
            members = dict(self._groups.get(scope, {}))
            if (qqid in members) == joined:
                return False
            if joined:
                members[qqid] = name
            else:
                members.pop(qqid, None)
            updated = dict(self._groups)
            if members:
                updated[scope] = members
            else:
                updated.pop(scope, None)
            # Publish only after the atomic save succeeds, so a failed opt-out never looks successful.
            await asyncio.to_thread(self._write, updated)
            self._groups = updated
            return True

    def query_lock(self, scope):
        return self.query_locks.setdefault(scope, asyncio.Lock())


def rank_rows(rows):
    ordered = sorted(rows, key=lambda row: (-round(row.score.achievements, 4), -row.score.dx_score, row.qqid))
    result = []
    previous, rank = None, 0
    for index, row in enumerate(ordered, 1):
        key = (round(row.score.achievements, 4), row.score.dx_score)
        if key != previous:
            rank = index
        result.append(RankingRow(row.qqid, row.name, row.score, rank))
        previous = key
    return tuple(result)


async def collect_ranking(store: SongRankingStore, scope, song, difficulty) -> RankingResult:
    members = await store.members(scope)

    async def fetch(qqid, name):
        async with store.query_semaphore:
            try:
                data = await asyncio.wait_for(get_music_record(qqid, str(song.song_id)), timeout=20)
                scores = [item if isinstance(item, PlayedResult) else playinfo_to_played(item) for item in data]
                scores = [score for score in scores if score.song_id == song.song_id
                          and score.level_index == difficulty.level_index and score.type == song.type
                          and math.isfinite(score.achievements) and 0 <= score.achievements <= 101
                          and score.dx_score >= 0]
                if not scores:
                    return qqid, None, 'unplayed'
                score = max(scores, key=lambda item: (round(item.achievements, 4), item.dx_score))
                return qqid, RankingRow(qqid, name, score), ''
            except MusicNotPlayError:
                return qqid, None, 'unplayed'
            except Exception:
                return qqid, None, 'failed'

    fetched = await asyncio.gather(*(fetch(qqid, name) for qqid, name in members.items()))
    # An opt-out while requests are in flight must not appear in the generated result.
    active = await store.members(scope)
    rows = [row for qqid, row, status in fetched if qqid in active and row is not None]
    return RankingResult(rank_rows(rows),
                         sum(status == 'failed' for qqid, _, status in fetched if qqid in active),
                         sum(status == 'unplayed' for qqid, _, status in fetched if qqid in active))
