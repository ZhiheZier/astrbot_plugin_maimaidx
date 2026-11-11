"""One-to-one account ownership claimed only from validated server responses."""

import asyncio
import hashlib
from pathlib import Path

from .maimaidx_management_store import ManagementError, read_json, write_json
from .maimaidx_official import OfficialError


class AccountBindings:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._account_locks = {}

    def account_lock(self, qq):
        return self._account_locks.setdefault(str(qq), asyncio.Lock())

    def _read(self):
        try:
            data = read_json(self.path, {'schema_version': 1, 'bindings': {}})
            if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
                    or data['schema_version'] != 1 or not isinstance(data.get('bindings'), dict)):
                raise ValueError()
            bindings = data['bindings']
            if any(not isinstance(qq, str) or not qq.isascii() or not qq.isdigit()
                   or not 0 < int(qq) < 10 ** 20 or str(int(qq)) != qq
                   or type(uid) is not int or not 0 < uid < 2 ** 64 for qq, uid in bindings.items()):
                raise ValueError()
            if len(set(bindings.values())) != len(bindings):
                raise ValueError()
            return bindings
        except (ValueError, OSError, ManagementError):
            raise OfficialError('游戏账号绑定文件异常，请管理员检查；未覆盖原文件。') from None

    async def records(self):
        async with self._lock:
            return await asyncio.to_thread(self._read)

    async def _save(self, bindings):
        task = asyncio.create_task(asyncio.to_thread(write_json, self.path, {'schema_version': 1, 'bindings': bindings}))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except (OSError, ManagementError):
            raise OfficialError('游戏账号绑定保存失败，未继续保存或上传成绩，请稍后重试。') from None

    async def bind(self, qq, user_id):
        qq = str(qq)
        if (not qq.isascii() or not qq.isdigit() or not 0 < int(qq) < 10 ** 20
                or str(int(qq)) != qq or type(user_id) is not int or not 0 < user_id < 2 ** 64):
            raise OfficialError('无法建立有效的 QQ 与游戏账号绑定，未保存或上传成绩。')
        async with self._lock:
            bindings = await asyncio.to_thread(self._read)
            current = bindings.get(qq)
            if current is not None and current != user_id:
                raise OfficialError('此游戏账号与当前 QQ 已绑定的账号不一致，未保存或上传成绩；请管理员在玩家管理中核实并解绑。')
            if any(owner != qq and uid == user_id for owner, uid in bindings.items()):
                raise OfficialError('此游戏账号已绑定其他 QQ，未保存或上传成绩；请管理员核实原绑定。')
            if current is None:
                await self._save({**bindings, qq: user_id})

    async def verify_snapshot(self, qq, account_hash):
        bindings = await self.records()
        uid = bindings.get(str(qq))
        if uid is None:
            raise OfficialError('尚未绑定游戏账号，请重新发送「更新成绩」并提交本人凭据后再重传。')
        if hashlib.sha256(str(uid).encode()).hexdigest() != account_hash:
            raise OfficialError('本地成绩与当前绑定的游戏账号不一致，请重新更新成绩；未上传旧快照。')

    async def unbind(self, qq, expected):
        async with self._lock:
            bindings = await asyncio.to_thread(self._read)
            if bindings.get(qq) is None or bindings.get(qq) != expected:
                raise OfficialError('游戏账号绑定已变化，请刷新后重新操作。')
            bindings.pop(qq)
            await self._save(bindings)
