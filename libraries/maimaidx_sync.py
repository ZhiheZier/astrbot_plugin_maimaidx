"""Validated local PC snapshots and independent uploads to bound probers."""

import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .maimaidx_api_data import maiApi
from .maimaidx_error import DivingFishOAuthNotBoundError
from .maimaidx_lxns import LxnsAPI
from .maimaidx_model import is_regular_chart
from .maimaidx_official import OfficialClient, OfficialData, OfficialError, validate_sgid
from .maimaidx_sync_limits import SyncLimits
from .maimaidx_user import userstore
from .maimaidx_account_binding import AccountBindings


FC = ('', 'fc', 'fcp', 'ap', 'app')
FS = ('', 'fs', 'fsp', 'fsd', 'fsdp', 'sync')


class OfficialScore(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    song_id: int = Field(gt=0, strict=True)
    level_index: int = Field(ge=0, le=10, strict=True)
    achievements: float = Field(ge=0, le=202)
    dx_score: int = Field(ge=0, strict=True)
    combo: int = Field(ge=0, le=4, strict=True)
    sync: int = Field(ge=0, le=5, strict=True)
    play_count: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode='after')
    def validate_achievement_range(self):
        # BUDDY utage records combine both players' achievement rates.
        if self.song_id < 100000 and self.achievements > 101:
            raise ValueError('regular achievement exceeds 101 percent')
        return self


class PCSnapshot(BaseModel):
    schema_version: Literal[1] = 1
    account_hash: str
    name: str
    rating: int = Field(ge=0)
    updated_at: int
    scores: list[OfficialScore]

    def pc50(self, is_new_song: Callable[[int], bool | None] | None = None) -> list[OfficialScore]:
        if is_new_song is None:
            from .maimaidx_music import mai

            def is_new_song(song_id):
                song = mai.get_song(song_id)
                return song.isnew if song else None

        old, new = [], []
        for score in self.scores:
            if (not is_regular_chart(score.song_id, score.level_index)
                    or score.play_count is None or score.play_count <= 0):
                continue
            is_new = is_new_song(score.song_id)
            if is_new is not None:
                (new if is_new else old).append(score)
        sort_key = lambda score: (-score.play_count, -score.achievements, -score.dx_score,
                                 score.song_id, score.level_index)
        return sorted(old, key=sort_key)[:35] + sorted(new, key=sort_key)[:15]


def snapshot_from_official(data: OfficialData) -> PCSnapshot:
    if data.account_id <= 0 or not isinstance(data.details, list) or not data.details:
        raise OfficialError('官方未返回有效成绩，本地数据未被覆盖。')
    unique = {}
    try:
        for detail in data.details:
            if type(detail.get('achievement')) is not int:
                raise ValueError('achievement must be an integer')
            dx = next(detail[key] for key in ('deluxscoreMax', 'deluxeScoreMax', 'deluxScoreMax',
                                              'dxScore', 'dx_score') if detail.get(key) is not None)
            score = OfficialScore(
                song_id=detail['musicId'], level_index=detail['level'],
                achievements=detail['achievement'] / 10000, dx_score=dx,
                combo=detail['comboStatus'], sync=detail['syncStatus'], play_count=detail.get('playCount'),
            )
            key = score.song_id, score.level_index
            if key in unique and unique[key] != score:
                raise ValueError('conflicting chart')
            unique[key] = score
        return PCSnapshot(
            account_hash=hashlib.sha256(str(data.account_id).encode()).hexdigest(),
            name=data.name, rating=data.rating, updated_at=int(time.time()), scores=list(unique.values()),
        )
    except (ValueError, TypeError, KeyError, StopIteration):
        raise OfficialError('官方成绩字段不完整或无效，本地数据未被覆盖，也未上传查分器。') from None


class PCStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self._locks = {}

    def path_lock(self, path: Path):
        return self._locks.setdefault(path.resolve(), asyncio.Lock())

    def path(self, identity: tuple[str, ...]) -> Path:
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        return self.directory / f'{digest}.json'

    def _write(self, path: Path, snapshot: PCSnapshot):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(snapshot.model_dump(mode='json'), stream, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    async def save(self, identity, snapshot):
        path = self.path(identity)
        async with self.path_lock(path):
            task = asyncio.create_task(asyncio.to_thread(self._write, path, snapshot))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def load(self, identity) -> PCSnapshot | None:
        path = self.path(identity)

        def read():
            try:
                return PCSnapshot.model_validate_json(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return None
            except ValueError:
                raise OfficialError('本地 PC 数据损坏，请重新同步成绩。') from None

        async with self.path_lock(path):
            return await asyncio.to_thread(read)

    async def delete(self, identity):
        path = self.path(identity)
        async with self.path_lock(path):
            path.unlink(missing_ok=True)


def upload_records(snapshot: PCSnapshot):
    from .maimaidx_music import mai

    fish, lxns = [], []
    for score in snapshot.scores:
        utage = score.song_id >= 100000
        if not utage and not is_regular_chart(score.song_id, score.level_index):
            continue
        level_index = 0 if utage else score.level_index
        song = mai.get_song(score.song_id)
        if song is None or mai.get_difficulty(score.song_id, level_index) is None:
            continue
        if song.type not in ('SD', 'DX'):
            continue
        title = 'Link(CoF)' if score.song_id == 383 else song.song_name
        kanji = getattr(song, 'kanji', None)
        if utage and kanji and not title.startswith(f'[{kanji}]'):
            title = f'[{kanji}]' + title
        fish.append({'title': title,
                     'type': song.type, 'level_index': level_index,
                     'achievements': score.achievements, 'dxScore': score.dx_score,
                     'fc': FC[score.combo], 'fs': FS[score.sync]})
        lxns.append({'id': score.song_id if utage else score.song_id % 10000,
                     'type': 'utage' if utage else ('dx' if song.type == 'DX' else 'standard'),
                     'level_index': level_index, 'achievements': score.achievements,
                     'dx_score': score.dx_score, 'fc': FC[score.combo] or None, 'fs': FS[score.sync] or None})
    return fish, lxns


async def upload_bound_sources(qqid: str, snapshot: PCSnapshot, *, before_upload=None) -> list[str]:
    fish, lxns = upload_records(snapshot)
    if not fish:
        return ['曲库未匹配到可上传的谱面，请先更新曲库；PC 数据仍已保留。']
    result, unbound = [], []
    reserved = False

    async def reserve_upload():
        nonlocal reserved
        if not reserved and before_upload is not None:
            await before_upload()
        reserved = True

    if maiApi.divingfish_oauth_configured:
        submitted = 0
        try:
            subject = maiApi._divingfish_subject(qqid=qqid)
            await maiApi._divingfish_access_token(subject)
            await reserve_upload()
            for index in range(0, len(fish), 500):
                batch = fish[index:index + 500]
                await maiApi.upload_user_records(qqid, batch)
                submitted += len(batch)
            result.append(f'水鱼：上传成功，共上传 {submitted} 条成绩。')
        except DivingFishOAuthNotBoundError:
            unbound.append('水鱼')
        except OfficialError:
            raise
        except Exception:
            partial = '部分成绩可能已上传，可发送「重传成绩」重试；' if submitted else ''
            result.append('水鱼：上传失败；' + partial + '请检查网络及 '
                          'prober.records.write 权限，必要时重新「绑定水鱼」。')
    else:
        result.append('水鱼：未配置 OAuth，已跳过。')
    user = userstore.get(qqid)
    if user.access_token:
        submitted = 0
        try:
            api = LxnsAPI(qqid=qqid, access_token=user.access_token, refresh_token=user.refresh_token)
            await reserve_upload()
            for index in range(0, len(lxns), 500):
                batch = lxns[index:index + 500]
                await api.upload_scores_personal(batch)
                submitted += len(batch)
            result.append(f'落雪：上传成功，共上传 {submitted} 条成绩。')
        except OfficialError:
            raise
        except Exception:
            partial = '部分成绩可能已上传，可发送「重传成绩」重试；' if submitted else ''
            result.append('落雪：上传失败；' + partial + '请检查网络及 '
                          'write_player 权限，必要时重新「绑定落雪」。')
    else:
        unbound.append('落雪')
    if unbound:
        commands = '或'.join(f'「绑定{provider}」' for provider in unbound)
        result.append(f'请发送{commands}完成绑定。')
    return result


class ScoreSync:
    def __init__(self, root: Path, config: dict):
        self.client = OfficialClient(root, config)
        self.store = PCStore(root / 'pc_data')
        self.limits = SyncLimits(root, config)
        self.bindings = AccountBindings(root / 'official_bindings.json')
        self.pending = {}
        self.used = {}
        self._locks = {}
        self._input_locks = {}

    @staticmethod
    def identity(event):
        return (str(event.get_platform_id()), str(event.get_self_id()), str(event.get_sender_id()))

    @staticmethod
    def conversation(event):
        return ScoreSync.identity(event) + (str(event.message_obj.group_id or ''),)

    def lock(self, identity):
        return self._locks.setdefault(identity, asyncio.Lock())

    def input_lock(self, identity):
        return self._input_locks.setdefault(identity, asyncio.Lock())

    def waiting(self, event):
        now = time.monotonic()
        self.pending = {key: end for key, end in self.pending.items() if end > now}
        return self.pending.get(self.conversation(event), 0) > now

    async def begin(self, event):
        await self.client.check_available()
        if self.lock(self.identity(event)).locked() or self.bindings.account_lock(event.get_sender_id()).locked():
            raise OfficialError('你的成绩正在同步，请勿重复提交。')
        await self.limits.check(self.identity(event), 'fetch')
        self.pending[self.conversation(event)] = time.monotonic() + 180

    async def sync(self, event, sgid):
        sgid = validate_sgid(sgid)
        identity = self.identity(event)
        lock = self.lock(identity)
        account_lock = self.bindings.account_lock(identity[-1])
        if lock.locked() or account_lock.locked():
            raise OfficialError('你的成绩正在同步，请勿重复提交。')
        async with lock, account_lock:
            await self.client.check_available()
            now = time.monotonic()
            self.used = {key: end for key, end in self.used.items() if end > now}
            digest = hashlib.sha256(sgid.encode()).hexdigest()
            if digest in self.used:
                raise OfficialError('此 SGID 已经尝试使用，请重新获取；重传查分器可使用「重传成绩」。')
            # Reserve before the first network await so two users cannot consume the same QR.
            self.used[digest] = now + 300
            try:
                await self.limits.reserve(identity, 'fetch')
            except BaseException:
                self.used.pop(digest, None)
                raise
            self.pending.pop(self.conversation(event), None)
            data = await self.client.fetch(sgid)
            snapshot = snapshot_from_official(data)
            await self.bindings.bind(identity[-1], data.account_id)
            await self.store.save(identity, snapshot)
            statuses = await self._upload(event, snapshot)
            return snapshot, statuses

    async def _upload(self, event, snapshot):
        identity = self.identity(event)
        try:
            await self.limits.check(identity, 'upload')
            return await upload_bound_sources(
                str(event.get_sender_id()), snapshot,
                before_upload=lambda: self.limits.reserve(identity, 'upload'),
            )
        except OfficialError as exc:
            return [str(exc) + ' 本地成绩已保留，可稍后发送「重传成绩」。']

    async def retry(self, event):
        if not self.client.config.get('official_sync_enabled', False):
            raise OfficialError('官方成绩同步尚未开启。')
        identity = self.identity(event)
        lock = self.lock(identity)
        account_lock = self.bindings.account_lock(identity[-1])
        if lock.locked() or account_lock.locked():
            raise OfficialError('你的成绩正在同步，请勿重复提交。')
        async with lock, account_lock:
            snapshot = await self.store.load(identity)
            if snapshot is None:
                raise OfficialError('尚无本地成绩，请先「同步成绩」。')
            await self.bindings.verify_snapshot(identity[-1], snapshot.account_hash)
            return await self._upload(event, snapshot)
