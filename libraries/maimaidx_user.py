import json
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel

from .. import data_dir, log

# 用户数据（数据源/主题/落雪绑定）与原项目保持一致，存放于 static/data
user_file = data_dir / 'user_data.json'

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

    qqid: int
    friend_code: Optional[int] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    service: ServiceName = ServiceName.DIVINGFISH
    theme: Theme = Theme.PRISM_PLUS


class UserStore:
    """基于 JSON 文件的用户数据存储，避免引入数据库依赖"""

    def __init__(self) -> None:
        self._data: Dict[str, User] = {}
        self._load()

    def _load(self) -> None:
        if not user_file.exists():
            self._data = {}
            return
        try:
            raw = json.load(open(user_file, 'r', encoding='utf-8'))
            self._data = {}
            for qq, info in raw.items():
                try:
                    self._data[str(qq)] = User.model_validate({**info, 'qqid': int(qq)})
                except Exception:
                    # 兼容脏数据，跳过单条
                    continue
        except Exception as e:
            log.error(f'加载用户数据失败: {e}')
            self._data = {}

    async def _save(self) -> None:
        from .tool import writefile

        dump = {
            qq: user.model_dump(exclude={'qqid'}, mode='json')
            for qq, user in self._data.items()
        }
        await writefile(user_file, dump)

    def get(self, qqid: int) -> User:
        """获取用户配置，不存在时返回默认（数据源为水鱼）"""
        key = str(qqid)
        if key in self._data:
            return self._data[key]
        return User(qqid=int(qqid))

    def exists(self, qqid: int) -> bool:
        return str(qqid) in self._data

    async def update(
        self,
        qqid: int,
        *,
        friend_code: Optional[int] = None,
        service: Optional[ServiceName] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        theme: Optional[Theme] = None,
    ) -> User:
        key = str(qqid)
        user = self._data.get(key) or User(qqid=int(qqid))
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
        await self._save()
        return user

    async def delete(self, qqid: int) -> bool:
        key = str(qqid)
        if key in self._data:
            del self._data[key]
            await self._save()
            return True
        return False


userstore = UserStore()
