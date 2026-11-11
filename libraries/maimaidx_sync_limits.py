"""Persistent per-sender budgets, reserved before external work starts."""

import asyncio
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .maimaidx_official import OfficialError


DEFAULTS = {
    'official_fetch_cooldown': 300,
    'official_fetch_daily_limit': 10,
    'official_upload_cooldown': 60,
    'official_upload_daily_limit': 20,
}
SHANGHAI = timezone(timedelta(hours=8))


class SyncLimitError(OfficialError):
    pass


class Usage(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    day: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    count: int = Field(ge=0, strict=True)
    last_at: float = Field(ge=0)


class UsageFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    users: dict[str, dict[Literal['fetch', 'upload'], Usage]] = Field(default_factory=dict)


class SyncLimits:
    def __init__(self, root: Path, config: dict):
        self.path = root / 'official_sync_limits.json'
        self.config = config
        self._state = None
        self._lock = asyncio.Lock()

    def _settings(self, action):
        values = [self.config.get(f'official_{action}_{suffix}', DEFAULTS[f'official_{action}_{suffix}'])
                  for suffix in ('cooldown', 'daily_limit')]
        if any(type(value) is not int or value < 0 for value in values):
            raise OfficialError('官方成绩同步限流配置无效，冷却秒数和每日次数必须为非负整数。')
        return values

    def _load(self):
        try:
            return UsageFile.model_validate_json(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return UsageFile()
        except (ValueError, OSError):
            raise OfficialError('成绩同步限流记录无法读取或已损坏，请联系管理员检查；本次未开始新的请求。') from None

    def _write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(state.model_dump_json())
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    async def check(self, identity, action):
        await self._access(identity, action, reserve=False)

    async def reserve(self, identity, action):
        await self._access(identity, action, reserve=True)

    async def _access(self, identity, action, *, reserve):
        if action not in ('fetch', 'upload'):
            raise ValueError('invalid sync budget action')
        cooldown, daily_limit = self._settings(action)
        if not cooldown and not daily_limit:
            return
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        async with self._lock:
            if self._state is None:
                self._state = await asyncio.to_thread(self._load)
            now = time.time()
            day = datetime.fromtimestamp(now, SHANGHAI).date().isoformat()
            usage = self._state.users.get(key, {}).get(action)
            count = usage.count if usage and usage.day == day else 0
            label = '官方取数' if action == 'fetch' else '查分器上传'
            if daily_limit and count >= daily_limit:
                raise SyncLimitError(f'今日{label}次数已达上限（{daily_limit} 次），请在北京时间次日 00:00 后重试。')
            remaining = math.ceil(usage.last_at + cooldown - now) if usage and cooldown else 0
            if remaining > 0:
                raise SyncLimitError(f'{label}冷却中，请在 {remaining} 秒后重试。')
            if not reserve:
                return
            state = self._state.model_copy(deep=True)
            state.users.setdefault(key, {})[action] = Usage(day=day, count=count + 1, last_at=now)
            # Keep the lock through cancellation until the atomic write completes.
            task = asyncio.create_task(asyncio.to_thread(self._write, state))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                finally:
                    self._state = state
                raise
            except OSError:
                raise OfficialError('成绩同步限流记录保存失败，请联系管理员检查目录权限；本次未开始新的请求。') from None
            self._state = state
