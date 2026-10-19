"""Independent subscriptions and local report fallbacks for the nearcade catalog."""

import asyncio
import json
import re
from pathlib import Path

from .maimaidx_arcade import Arcade, ArcadeAttendance, ArcadeList
from .maimaidx_music import writefile


class NearcadeArcadeList(ArcadeList):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.lock = asyncio.Lock()
        if path.exists():
            with path.open(encoding='utf-8') as stream:
                data = json.load(stream)
            if not isinstance(data, list):
                raise ValueError('invalid nearcade subscriptions')
            self.extend(Arcade.model_validate(item) for item in data)
            if len({record.id for record in self}) != len(self):
                raise ValueError('duplicate nearcade shop IDs')
            for record in self:
                self._merge_legacy_games(record)

    @staticmethod
    def _merge_legacy_games(record):
        families = {}
        for key in (*record.game_attendance, *record.game_machine_counts):
            match = re.fullmatch(r'([^:]+):机台[0-9]+号', key)
            if match:
                families.setdefault(match[1], set()).add(key)
        for family, keys in families.items():
            counts = [record.game_machine_counts.pop(key) for key in keys if key in record.game_machine_counts]
            if counts and family not in record.game_machine_counts:
                record.game_machine_counts[family] = sum(counts)
            reports = [record.game_attendance.pop(key) for key in keys if key in record.game_attendance]
            reports = [item for item in reports if item.person or item.by or item.time or item.status == '闭店']
            if not reports:
                continue
            # A later family-level report is authoritative; do not add old counters again.
            if family == 'mai' and (record.person or record.by or record.time or record.status == '闭店'):
                continue
            if family in record.game_attendance:
                continue
            merged = ArcadeAttendance(person=sum(item.person for item in reports),
                                      by=reports[0].by if len(reports) == 1 else 'Nearcade（本地缓存汇总）',
                                      time=max(item.time for item in reports),
                                      status='闭店' if all(item.status == '闭店' for item in reports) else '营业')
            if family == 'mai':
                record.person, record.by, record.time, record.status = merged.person, merged.by, merged.time, merged.status
            else:
                record.game_attendance[family] = merged
        if 'mai' in record.game_machine_counts:
            record.num = record.game_machine_counts['mai']

    async def save_arcade(self):
        async with self.lock:
            # Catalog search results are disposable; persist only user-owned state.
            data = [record.model_dump() for record in self
                    if record.group or record.alias or record.group_aliases or record.time or record.game_attendance]
            temporary = self.path.with_suffix('.tmp')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not await writefile(temporary, data):
                return False
            temporary.replace(self.path)
            return True

    def remember(self, shop, games):
        matches = self.search_id(str(shop.id))
        if matches:
            record = matches[0]
        else:
            record = Arcade(name=shop.name, location='', province='', mall='', num=0,
                            id=str(shop.id), alias=[], group=[], person=0, by='', time='')
            self.append(record)
        record.name = shop.name
        self._merge_legacy_games(record)
        record.location = ' '.join(filter(None, (
            ' '.join(shop.address.general), shop.address.detailed,
        )))
        record.game_machine_counts = {game.key: quantity for game, quantity in games}
        record.num = record.game_machine_counts.get('mai', 0)
        return record
