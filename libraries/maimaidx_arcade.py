import asyncio
import json
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import aiohttp
from pydantic import BaseModel, Field

from .. import arcades_json, loga
from .maimaidx_music import writefile
from .maimaidx_arcade_games import ArcadeGameType, DEFAULT_ARCADE_GAME, arcade_game_from_key
from .maimaidx_arcade_messages import format_arcade_query, local_arcade_query


class ArcadeAttendance(BaseModel):
    person: int = 0
    by: str = ''
    time: str = ''
    status: str = '营业'
    label: str = ''


class Arcade(BaseModel):
    
    name: str
    location: str
    province: str
    mall: str
    num: int
    id: str
    alias: List[str]
    group_aliases: Dict[str, List[str]] = Field(default_factory=dict)
    group: List[int]
    person: int
    by: str
    time: str
    status: str = '营业'
    game_attendance: Dict[str, ArcadeAttendance] = Field(default_factory=dict)
    game_machine_counts: Dict[str, int] = Field(default_factory=dict)

    def aliases_for(self, group_id=None):
        scoped = self.group_aliases.get(str(group_id), []) if group_id is not None else []
        return list(dict.fromkeys((*self.alias, *scoped)))

    def attendance_for(self, game: ArcadeGameType = DEFAULT_ARCADE_GAME):
        if game.key == DEFAULT_ARCADE_GAME.key:
            return self
        return self.game_attendance.get(game.key, ArcadeAttendance())

    def known_games(self) -> list[ArcadeGameType]:
        games = []
        for key in dict.fromkeys((*self.game_attendance, *self.game_machine_counts)):
            attendance = self.game_attendance.get(key)
            if game := arcade_game_from_key(key, attendance.label if attendance else ''):
                games.append(game)
        return games


def match_arcades(records, name, group_id=None):
    if name.isascii() and name.isdigit():
        return [record for record in records if record.id == name]
    scoped = [record for record in records if group_id is not None and name in record.group_aliases.get(str(group_id), [])]
    return scoped or [record for record in records if name == record.name or name in record.alias]


class ArcadeList(List[Arcade]):

    def __init__(self, *args):
        super().__init__(*args)
        self.alias_lock = asyncio.Lock()


    async def save_arcade(self):
        data = [record.model_dump() for record in self]
        saved = await writefile(arcades_json, data)
        if saved:
            arcade.arcades = data
        return saved

    async def reset_local_attendance(self):
        if not self:
            return True
        fields = ('person', 'by', 'time', 'game_attendance')
        snapshots = [(record, record.model_copy(deep=True)) for record in self]
        for record in self:
            record.person, record.by, record.time = 0, '系统每日清零', ''
            for attendance in record.game_attendance.values():
                attendance.person, attendance.by, attendance.time = 0, '系统每日清零', ''
        saved = False
        try:
            saved = bool(await self.save_arcade())
            return saved
        finally:
            if not saved:
                for record, snapshot in snapshots:
                    for field in fields:
                        setattr(record, field, getattr(snapshot, field))
    
    def search_name(self, name: str, group_id=None) -> List[Arcade]:
        """模糊查询机厅"""
        scoped = [record for record in self if group_id is not None and name in record.group_aliases.get(str(group_id), [])]
        if scoped:
            return scoped
        arcade_list = []
        for arcade in self:
            if name in arcade.name:
                arcade_list.append(arcade)
            elif name in arcade.location:
                arcade_list.append(arcade)
            elif name in arcade.aliases_for(group_id):
                arcade_list.append(arcade)
                
        return arcade_list
    
    def search_fullname(self, name: str) -> List[Arcade]:
        """查询店铺全名机厅"""
        arcade_list = []
        for arcade in self:
            if name == arcade.name:
                arcade_list.append(arcade)

        return arcade_list
    
    def search_alias(self, alias: str) -> List[Arcade]:
        """查询别名机厅"""
        arcade_list = []
        for arcade in self:
            if alias in arcade.alias:
                arcade_list.append(arcade)
        
        return arcade_list
    
    def search_id(self, id: str) -> List[Arcade]:
        """指定ID查询机厅"""
        arcade_list = []
        for arcade in self:
            if id == arcade.id:
                arcade_list.append(arcade)

        return arcade_list

    def add_arcade(self, arcade: dict) -> bool:
        """添加机厅"""
        self.append(Arcade(**arcade))
        return True

    def del_arcade(self, arcadeName: str) -> bool:
        """删除机厅"""
        for arcade in self:
            if arcadeName == arcade.name:
                self.remove(arcade)
                return True
        return False
    
    def group_in_arcade(self, group_id: int, arcadeName: str) -> bool:
        """是否已订阅该机厅"""
        for arcade in self:
            if arcadeName == arcade.name:
                if group_id in arcade.group:
                    return True
        return False
    
    def group_subscribe_arcade(self, group_id: int) -> List[Arcade]:
        """已订阅机厅"""
        arcade_list = []
        for arcade in self:
            if group_id in arcade.group:
                arcade_list.append(arcade)
        return arcade_list

    @classmethod
    def arcade_to_msg(cls, arcade_list: List[Arcade], game: ArcadeGameType | None = DEFAULT_ARCADE_GAME,
                      configured_games: dict | None = None) -> List[str]:
        """机厅人数格式化"""
        result = []
        for arcade in arcade_list:
            result.append(format_arcade_query(local_arcade_query(
                arcade, (configured_games or {}).get(arcade.id, []), game,
            )))
        return result


class ArcadeData:
    
    total: Optional[ArcadeList]
    
    def __init__(self) -> None: 
        self.arcades = []
        if arcades_json.exists():
            self.arcades: List[Dict] = json.load(open(arcades_json, 'r', encoding='utf-8'))
        self.idList = []

    def get_by_id(self, id: int) -> Optional[Dict]:
        id_list = [c_a['id'] for c_a in self.arcades]
        if id in id_list:
            return self.arcades[id_list.index(id)]
        else:
            return None
    
    async def getArcade(self):
        self.total = await download_arcade_info()
        # 确保 self.total 不为 None 且是有效的列表
        if self.total is None or not isinstance(self.total, ArcadeList):
            loga.warning('机厅数据获取失败，使用空列表')
            self.total = ArcadeList()
            self.idList = []
        else:
            self.idList = [int(c_a.id) for c_a in self.total]

arcade = ArcadeData()


async def download_arcade_info(save: bool = True) -> ArcadeList:
    arcadelist = ArcadeList()
    try:
        async with aiohttp.request('GET', 'https://wc.wahlap.net/maidx/rest/location', timeout=aiohttp.ClientTimeout(total=60)) as req:
            if req.status == 200:
                data = await req.json()
            else:
                data = None
                loga.warning(f'获取机厅信息失败，HTTP状态码: {req.status}')
        
        if data is not None:
            if not arcade.arcades:
                for num in range(len(data)):
                    _arc = data[num]
                    arcade_dict = {
                        'name': _arc['arcadeName'],
                        'location': _arc['address'],
                        'province': _arc['province'],
                        'mall': _arc['mall'],
                        'num': _arc['machineCount'],
                        'id': _arc['id'],
                        'alias': [],
                        'group': [],
                        'person': 0,
                        'by': '',
                        'time': ''
                    }
                    arcadelist.append(Arcade.model_validate(arcade_dict))
            else:
                for num in range(len(data)):
                    _arc = data[num]
                    arcade_dict = arcade.get_by_id(_arc['id'])
                    if arcade_dict is not None:
                        arcade_dict['name'] = _arc['arcadeName']
                        arcade_dict['location'] = _arc['address']
                        arcade_dict['province'] = _arc['province']
                        arcade_dict['mall'] = _arc['mall']
                        arcade_dict['num'] = _arc['machineCount']
                        arcade_dict['id'] = _arc['id']
                    else:
                        arcade_dict = {
                            'name': _arc['arcadeName'],
                            'location': _arc['address'],
                            'province': _arc['province'],
                            'mall': _arc['mall'],
                            'num': _arc['machineCount'],
                            'id': _arc['id'],
                            'alias': [],
                            'group': [],
                            'person': 0,
                            'by': '',
                            'time': ''
                        }
                        arcade.arcades.insert(num, arcade_dict)
                    arcadelist.append(Arcade.model_validate(arcade_dict))
            for n in arcade.arcades:
                if int(n['id']) >= 10000:
                    arcadelist.append(Arcade.model_validate(n))
        else:
            # 网络请求失败，使用本地数据
            loga.warning('网络请求失败，使用本地机厅数据')
            for _a in arcade.arcades:
                arcadelist.append(Arcade.model_validate(_a))
        
        if save and arcadelist:
            await writefile(arcades_json, [_.model_dump() for _ in arcadelist])
        return arcadelist
    except asyncio.TimeoutError:
        loga.error('获取机厅信息超时，使用本地数据')
        # 超时时返回本地数据
        for _a in arcade.arcades:
            arcadelist.append(Arcade.model_validate(_a))
        return arcadelist
    except Exception as e:
        loga.error(f'获取机厅信息失败: {e}')
        loga.error(f'Error: {traceback.format_exc()}')
        # 异常时返回本地数据，确保不会返回 None
        for _a in arcade.arcades:
            arcadelist.append(Arcade.model_validate(_a))
        return arcadelist


async def updata_arcade(arcadeName: str, num: str, game: ArcadeGameType = DEFAULT_ARCADE_GAME):
    if arcadeName.isdigit():
        arcade_list = arcade.total.search_id(arcadeName)
    else:
        arcade_list = arcade.total.search_fullname(arcadeName)
    if arcade_list:
        _arcade = arcade_list[0]
        count = int(num)
        if count < 0 or count > 9999:
            return '机台数量需为0至9999的整数'
        original_num, original_counts = _arcade.num, dict(_arcade.game_machine_counts)
        if game.key == DEFAULT_ARCADE_GAME.key:
            _arcade.num = count
        else:
            _arcade.game_machine_counts[game.key] = count
        label = '' if game.key == DEFAULT_ARCADE_GAME.key else f'（{game.name}）'
        msg = f'已修改机厅 [{arcadeName}]{label} 机台数量为 [{num}]'
        try:
            if not await arcade.total.save_arcade():
                raise OSError('machine count save failed')
        except Exception as exc:
            _arcade.num, _arcade.game_machine_counts = original_num, original_counts
            loga.warning(f'保存机台数量失败（{type(exc).__name__}）')
            return '本地保存失败，机台数量未修改，请稍后重试'
    else:
        msg = f'未找到机厅：{arcadeName}'
    return msg
    

async def update_alias(arcadeName: str, aliasName: str, add_del: bool):
    """变更机厅别名"""
    change = False
    if arcadeName.isdigit():
        arcade_list = arcade.total.search_id(arcadeName)
    else:
        arcade_list = arcade.total.search_fullname(arcadeName)
    if arcade_list:
        _arcade = arcade_list[0]
        if add_del:
            if aliasName not in _arcade.alias:
                _arcade.alias.append(aliasName)
                msg = f'机厅：{_arcade.name}\n已添加别名：{aliasName}'
                change = True
            else:
                msg = f'机厅：{_arcade.name}\n已拥有别名：{aliasName}\n请勿重复添加'
        else:
            if aliasName in _arcade.alias:
                _arcade.alias.remove(aliasName)
                msg = f'机厅：{_arcade.name}\n已删除别名：{aliasName}'
                change = True
            else:
                msg = f'机厅：{_arcade.name}\n未拥有别名：{aliasName}'
    else:
        msg = f'未找到机厅：{arcadeName}'
    if change:
        await arcade.total.save_arcade()
    return msg


async def subscribe(group_id: int, arcadeName: str, sub: bool):
    """订阅机厅，`sub` 等于 `True` 为订阅，`False` 为取消订阅"""
    change = False
    arcade_list = match_arcades(arcade.total, arcadeName, group_id)
    if len(arcade_list) > 1:
        return '找到多个机厅，请使用ID订阅\n' + '\n'.join(f'{record.id}：{record.name}' for record in arcade_list)
    if arcade_list:
        _arcade = arcade_list[0]
        if sub:
            if group_id in _arcade.group:
                msg = f'该群已订阅机厅：{_arcade.name}'
            else:
                _arcade.group.append(group_id)
                msg = f'群：{group_id} 已添加订阅机厅：{_arcade.name}'
                change = True
        else:
            if group_id not in _arcade.group:
                msg = f'该群未订阅机厅：{_arcade.name}，无需取消订阅'
            else:
                _arcade.group.remove(group_id)
                msg = f'群：{group_id} 已取消订阅机厅：{_arcade.name}'
                change = True
    else:
        msg = f'未找到机厅：{arcadeName}'
    if change:
        await arcade.total.save_arcade() 
    return msg
        
        
@dataclass(frozen=True)
class AttendanceUpdateResult:
    message: str
    successful: bool = True
    changed: bool = False


async def update_attendance(record: Arcade, user_name: str, value: str, person: int,
                            game: ArcadeGameType = DEFAULT_ARCADE_GAME, *, records=None) -> AttendanceUpdateResult:
    attendance = record.attendance_for(game)
    name = record.name if game.key == DEFAULT_ARCADE_GAME.key else f'{record.name}（{game.name}）'
    setting = value in ('=', '＝', '设置', '设定')
    closing = person == -1 and setting
    if closing:
        if attendance.status == '闭店' and attendance.person == 0:
            return AttendanceUpdateResult(f'机厅：{name} 已处于闭店状态')
        count = 0
    elif person < 0:
        return AttendanceUpdateResult('人数不能为负数；闭店请使用 =-1', successful=False)
    elif value in ('+', '＋', '增加', '添加', '加'):
        if person > 30:
            return AttendanceUpdateResult('单次人数变化不能超过30人', successful=False)
        count = attendance.person + person
    elif value in ('-', '－', '减少', '降低', '减'):
        if person > 30:
            return AttendanceUpdateResult('单次人数变化不能超过30人', successful=False)
        if person > attendance.person:
            return AttendanceUpdateResult('减少人数不能超过当前人数', successful=False)
        count = attendance.person - person
    elif setting:
        if abs(attendance.person - person) > 30:
            return AttendanceUpdateResult('单次人数变化不能超过30人', successful=False)
        count = person
    else:
        return AttendanceUpdateResult('未识别的人数操作', successful=False)
    known = (bool(record.person or record.by or record.time or record.status == '闭店')
             if game.key == DEFAULT_ARCADE_GAME.key else game.key in record.game_attendance)
    if not closing and count == attendance.person and known:
        return AttendanceUpdateResult(f'人数没有变化\n机厅：{name}\n当前人数：{count}')

    snapshot = record.model_copy(deep=True)
    attendance.person = count
    attendance.status = '闭店' if closing else '营业'
    attendance.by = user_name
    attendance.time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    if game.key != DEFAULT_ARCADE_GAME.key:
        attendance.label = game.name
        record.game_attendance[game.key] = attendance
    try:
        if not await (records if records is not None else arcade.total).save_arcade():
            raise OSError('attendance save failed')
    except Exception as exc:
        # Do not retain an in-memory change that was never successfully saved.
        for field in ('person', 'status', 'by', 'time', 'game_attendance'):
            setattr(record, field, getattr(snapshot, field))
        loga.warning(f'保存排卡人数失败（{type(exc).__name__}），未上传云端')
        return AttendanceUpdateResult('本地保存失败，人数未更新、未上传云端，请稍后重试', successful=False)
    detail = '已闭店，人数已清空' if closing else f'当前人数：{count}'
    return AttendanceUpdateResult(f'机厅：{name}\n{detail}\n变更时间：{attendance.time}', changed=True)


async def update_person(arcadeList: List[Arcade], userName: str, value: str, person: int,
                        game: ArcadeGameType = DEFAULT_ARCADE_GAME):
    """变更机厅人数，保留原调用方的文本返回接口。"""
    if len(arcadeList) == 1:
        return (await update_attendance(arcadeList[0], userName, value, person, game)).message
    if len(arcadeList) > 1:
        return '找到多个机厅，请使用id变更人数\n' + '\n'.join([f'{_.id}：{_.name}' for _ in arcadeList])
    return '没有找到指定机厅'
