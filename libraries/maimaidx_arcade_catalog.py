"""Independent subscriptions and local report fallbacks for the nearcade catalog."""

import asyncio
import json
from pathlib import Path

from .maimaidx_arcade import Arcade, ArcadeList
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
        record.location = ' '.join(filter(None, (
            ' '.join(shop.address.general), shop.address.detailed,
        )))
        record.game_machine_counts = {game.key: quantity for game, quantity in games}
        record.num = record.game_machine_counts.get('mai', 0)
        return record
