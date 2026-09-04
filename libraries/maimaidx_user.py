import asyncio
import json
import os
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Union

from pydantic import BaseModel

from .. import log

class ServiceName(str, Enum):
    """查分数据源"""

    DIVINGFISH = 'Diving-Fish'
    LXNS = 'Lxns-Network'

    @property
    def label(self) -> str:
        """用户可见的中文名称"""
        return {
            ServiceName.DIVINGFISH: '水鱼查分器（Diving-Fish）',
            ServiceName.LXNS: '落雪查分器（Lxns-Network）',
        }.get(self, self.value)

    @classmethod
    def get_by_index(cls, index_str: str) -> Optional['ServiceName']:
        mapping = {str(i): item for i, item in enumerate(cls)}
        return mapping.get(index_str)

    @classmethod
    def get_help(cls) -> str:
        return '\n'.join([f'「{i}」：{item.label}' for i, item in enumerate(cls)])


class Theme(str, Enum):
    """成绩图主题"""

    PRISM_PLUS = 'prism_plus'
    CIRCLE = 'circle'

    @property
    def color(self) -> tuple:
        """主题主色（文字描边等）"""
        if self == Theme.CIRCLE:
            return (249, 62, 172, 255)
        return (124, 129, 255, 255)

    @classmethod
    def get_by_index(cls, index_str: str) -> Optional['Theme']:
        mapping = {str(i): item for i, item in enumerate(cls)}
        return mapping.get(index_str)

    @classmethod
    def get_help(cls) -> str:
        return '\n'.join([f'「{i}」：{item.value}' for i, item in enumerate(cls)])


class User(BaseModel):
    """用户配置"""

    qqid: str
    friend_code: Optional[int] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    service: ServiceName = ServiceName.DIVINGFISH
    theme: Theme = Theme.PRISM_PLUS


class UserStore:
    """基于 JSON 文件的用户数据存储，避免引入数据库依赖"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._data: Dict[str, User] = {}
        self._path = path
        self._lock = asyncio.Lock()
        self._load()

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        # init_static_dir 会在插件启动时更新持久化目录，因此这里动态取值。
        from .. import user_file

        return user_file

    def _load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            with self.path.open('r', encoding='utf-8') as file:
                raw = json.load(file)
            self._data = {}
            for qq, info in raw.items():
                try:
                    self._data[str(qq)] = User.model_validate({**info, 'qqid': str(qq)})
                except Exception:
                    # 兼容脏数据，跳过单条
                    continue
        except Exception as e:
            log.error(f'加载用户数据失败: {e}')
            self._data = {}

    def reload(self) -> None:
        """持久化目录初始化后重新载入用户数据。"""
        self._load()

    async def _save_unlocked(self) -> None:
        from .tool import writefile

        dump = {
            qq: user.model_dump(exclude={'qqid'}, mode='json')
            for qq, user in self._data.items()
        }
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f'.{path.name}.tmp')
        try:
            await writefile(temp_path, dump)
            await asyncio.to_thread(os.replace, temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    async def _save(self) -> None:
        async with self._lock:
            await self._save_unlocked()

    def get(self, qqid: Union[int, str]) -> User:
        """获取用户配置，不存在时返回默认（数据源为水鱼）"""
        key = str(qqid)
        if key in self._data:
            return self._data[key]
        return User(qqid=key)

    def exists(self, qqid: Union[int, str]) -> bool:
        return str(qqid) in self._data

    async def update(
        self,
        qqid: Union[int, str],
        *,
        friend_code: Optional[int] = None,
        service: Optional[ServiceName] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        theme: Optional[Theme] = None,
    ) -> User:
        async with self._lock:
            key = str(qqid)
            user = self._data.get(key) or User(qqid=key)
            if friend_code is not None:
                user.friend_code = friend_code
            if service is not None:
                user.service = service
            if access_token is not None:
                user.access_token = access_token
            if refresh_token is not None:
                user.refresh_token = refresh_token
            if theme is not None:
                user.theme = theme
            self._data[key] = user
            await self._save_unlocked()
            return user

    async def delete(self, qqid: Union[int, str]) -> bool:
        async with self._lock:
            key = str(qqid)
            if key in self._data:
                del self._data[key]
                await self._save_unlocked()
                return True
            return False


userstore = UserStore()
