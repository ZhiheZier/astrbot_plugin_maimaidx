"""Bounded operational metadata; never collect command arguments or response bodies."""

import asyncio
import functools
import inspect
import re
import time
from contextlib import aclosing
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .maimaidx_management_store import ManagementError, read_json, write_json


class ActivityEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    at: float = Field(gt=0)
    scope: Literal['chat', 'web', 'resource']
    feature: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    label: str = Field(min_length=1, max_length=80)
    status: Literal['responded', 'handled', 'submitted', 'error', 'cancelled', 'running', 'completed']
    actor: str = Field(default='', pattern=r'^\d{0,20}$')
    group: str = Field(default='', pattern=r'^\d{0,20}$')
    bot: str = Field(default='', pattern=r'^\d{0,20}$')
    duration_ms: int = Field(default=0, ge=0, le=86400000)


class ActivityLog:
    capacity = 1000
    retention = 7 * 86400

    def __init__(self, path: Path):
        self.path = path
        self._rows = []
        self._task = None
        self._loaded = False
        self._writable = True
        self._closed = False
        self._generation = 0
        self._saved = 0
        self._loop = None
        self._load_lock = asyncio.Lock()

    def _prune(self):
        cutoff = time.time() - self.retention
        self._rows = sorted((row for row in self._rows if row['at'] >= cutoff), key=lambda row: row['at'])[-self.capacity:]

    async def load(self):
        async with self._load_lock:
            await self._load_once()

    async def _load_once(self):
        if self._loaded:
            return
        self._loop = asyncio.get_running_loop()
        try:
            raw = await asyncio.to_thread(read_json, self.path, {'schema_version': 1, 'entries': []})
            if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('entries'), list):
                raise ValueError()
            rows = [ActivityEntry.model_validate(row).model_dump() for row in raw['entries']]
            self._rows = rows + self._rows
            self._prune()
        except (OSError, ValueError, ManagementError):
            self._writable = False
            self._warn()
        self._loaded = True

    @staticmethod
    def _warn():
        from .. import log
        log.warning('舞萌功能日志无法读写，当前仅保留内存记录；原日志文件未覆盖。')

    def record(self, scope, feature, label, status, *, actor='', group='', bot='', duration_ms=0):
        if self._closed:
            return
        try:
            row = ActivityEntry(id=uuid4().hex, at=time.time(), scope=scope, feature=feature,
                                label=label, status=status, actor=actor, group=group, bot=bot,
                                duration_ms=min(86400000, max(0, int(duration_ms))))
        except (TypeError, ValueError):
            return
        self._rows.append(row.model_dump())
        self._prune()
        self._generation += 1
        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            if self._task is None or self._task.done():
                self._task = loop.create_task(self._flush_later())
        except RuntimeError:
            pass

    def resource_phase(self, phase):
        phases = {'downloading': ('下载资源', 'running'), 'extracting': ('读取资源包', 'running'),
                  'installing': ('安装资源', 'running'), 'reloading': ('加载资源', 'running'),
                  'rebuilding': ('生成表格', 'running'), 'completed': ('资源任务完成', 'completed'),
                  'failed': ('资源任务失败', 'error'), 'cancelled': ('资源任务取消', 'cancelled')}
        if phase not in phases or self._loop is None or self._loop.is_closed():
            return
        label, status = phases[phase]
        try:
            self._loop.call_soon_threadsafe(self.record, 'resource', 'resource_task', label, status)
        except RuntimeError:
            pass

    async def _flush_later(self):
        await asyncio.sleep(0.5)
        await self.load()
        while self._writable and self._saved < self._generation:
            generation = self._generation
            payload = {'schema_version': 1, 'entries': list(self._rows)}
            task = asyncio.create_task(asyncio.to_thread(write_json, self.path, payload))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except (OSError, ManagementError):
                self._writable = False
                self._warn()
                return
            self._saved = generation

    async def shutdown(self):
        self._closed = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    def listing(self, query):
        from .maimaidx_management import page

        scope = query.get('scope', 'all')
        if scope not in ('all', 'chat', 'web', 'resource'):
            raise ManagementError('请选择有效的日志类型。')
        self._prune()
        rows = [dict(row) for row in reversed(self._rows) if scope == 'all' or row['scope'] == scope]
        return {**page(rows, query), 'persistent': self._writable}


def _numeric(value):
    value = str(value or '')
    return value if re.fullmatch(r'\d{1,20}', value, flags=re.ASCII) else ''


def usage_logged(function):
    labels = {'sync_official_scores': '更新成绩', 'official_score_input': '成绩凭据处理', 'pc50': 'PC50',
              'nearby_arcades': '附近机厅', 'guess_music_solve': '猜歌作答'}
    label = labels.get(function.__name__, ((function.__doc__ or '').strip() or function.__name__).splitlines()[0][:80])

    def record(plugin, event, status, started):
        store = getattr(plugin, 'activity', None)
        if store is None:
            return
        try:
            store.record('chat', function.__name__, label, status,
                         actor=_numeric(event.get_sender_id()), bot=_numeric(event.get_self_id()),
                         group=_numeric(event.message_obj.group_id), duration_ms=(time.monotonic() - started) * 1000)
        except Exception:
            pass

    if inspect.isasyncgenfunction(function):
        @functools.wraps(function)
        async def generator(plugin, event, *args, **kwargs):
            started, responded = time.monotonic(), False
            try:
                async with aclosing(function(plugin, event, *args, **kwargs)) as results:
                    async for result in results:
                        responded = responded or result is not None
                        yield result
            except (asyncio.CancelledError, GeneratorExit):
                if responded:
                    record(plugin, event, 'cancelled', started)
                raise
            except Exception:
                record(plugin, event, 'error', started)
                raise
            else:
                if responded:
                    record(plugin, event, 'responded', started)
        return generator

    @functools.wraps(function)
    async def coroutine(plugin, event, *args, **kwargs):
        started = time.monotonic()
        try:
            result = await function(plugin, event, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            record(plugin, event, 'error', started)
            raise
        if result is not None:
            record(plugin, event, 'responded', started)
        return result
    return coroutine
