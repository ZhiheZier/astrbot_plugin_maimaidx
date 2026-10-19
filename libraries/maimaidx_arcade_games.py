"""Game families understood by arcade attendance commands."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ArcadeGameType:
    key: str
    name: str
    title_ids: tuple[int, ...]
    aliases: tuple[str, ...]

    @property
    def family_key(self) -> str:
        return self.key.partition(':')[0]

    @property
    def is_named(self) -> bool:
        return ':' in self.key


ARCADE_GAMES = (
    ArcadeGameType('mai', '舞萌 DX', (1,), ('舞萌DX', '舞萌', 'maimaiDX', 'maimai', 'mai')),
    ArcadeGameType('chu', '中二节奏', (3,), ('中二节奏', '中二', 'chunithm', 'chu')),
    ArcadeGameType('taiko', '太鼓达人', (31, 15), ('太鼓达人', '太鼓', 'taiko')),
    ArcadeGameType('sdvx', 'SOUND VOLTEX', (4,), ('音律炫动', 'sdvx', 'soundvoltex')),
    ArcadeGameType('iidx', 'beatmania IIDX', (5,), ('iidx', 'beatmaniaiidx')),
    ArcadeGameType('ongeki', '音击', (27,), ('音击', '音撃', 'ongeki')),
    ArcadeGameType('wacca', 'WACCA', (17,), ('华卡', 'wacca')),
    ArcadeGameType('jubeat', 'jubeat', (6, 34), ('乐动魔方', 'jubeat', 'jb')),
    ArcadeGameType('ddr', 'DanceDanceRevolution', (11,), ('ddr', '跳舞革命')),
    ArcadeGameType('dancerush', 'DANCERUSH', (10,), ('dancerush', 'drs')),
    ArcadeGameType('nostalgia', 'NOSTALGIA', (7,), ('nostalgia', '诺斯塔尔吉亚')),
    ArcadeGameType('gc', 'GROOVE COASTER', (16,), ('音炫轨道', 'groovecoaster', 'gc')),
    ArcadeGameType('piu', 'PUMP IT UP', (19,), ('pumpitup', 'piu')),
    ArcadeGameType('diva', 'Project DIVA Arcade', (24,), ('初音', 'diva')),
)
DEFAULT_ARCADE_GAME = ARCADE_GAMES[0]
_ALIASES = {alias.casefold(): game for game in ARCADE_GAMES for alias in (game.key, *game.aliases)}


def resolve_arcade_game(label: str) -> ArcadeGameType | None:
    label = ''.join(label.split())
    folded = label.casefold()
    if game := _ALIASES.get(folded):
        return game
    if not label or len(label) > 40 or any(char in label for char in '[]=:+-＋－＝'):
        return None
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if folded.startswith(alias):
            base = _ALIASES[alias]
            suffix = label[len(alias):]
            return ArcadeGameType(f'{base.key}:{suffix.casefold()}', label, base.title_ids, (label,))
    return None


def arcade_game_from_key(key: str, label: str = '') -> ArcadeGameType | None:
    family, separator, suffix = key.partition(':')
    game = resolve_arcade_game(family + suffix if separator else family)
    if game and game.key == key:
        return ArcadeGameType(game.key, label or game.name, game.title_ids, (label or game.name,))
    return None


def _game_aliases(named_games):
    aliases = dict(_ALIASES)
    for game in named_games:
        if game.is_named:
            aliases.update((alias.casefold(), game) for alias in game.aliases)
    return aliases


def split_arcade_game(target: str, named_games=()) -> tuple[str, ArcadeGameType]:
    target = target.strip()
    folded = target.casefold()
    aliases = _game_aliases(named_games)
    for alias in sorted(aliases, key=len, reverse=True):
        if folded.endswith(alias):
            return target[:-len(alias)].rstrip(), aliases[alias]
    return target, DEFAULT_ARCADE_GAME


@dataclass(frozen=True)
class ArcadeAttendanceReport:
    game: ArcadeGameType
    operation: str
    person: int


def parse_arcade_reports(text: str, named_games=()) -> list[ArcadeAttendanceReport]:
    """Parse the entire game/count tail before any attendance is changed."""
    aliases = _game_aliases(named_games)
    names = '|'.join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    pattern = re.compile(rf'\s*({names})\s*([=＝+＋\-－]?)\s*(-?[0-9]+)\s*(?:人|卡)?', re.IGNORECASE)
    reports, seen, position = [], set(), 0
    while position < len(text.rstrip()):
        match = pattern.match(text, position)
        if match is None:
            if not reports:
                return []
            raise ValueError('格式不完整或存在未识别的游戏名，请使用如 mai3chu2 的格式；本次未更新任何人数')
        if len(match[3]) > 9 or len(reports) >= 16:
            raise ValueError('人数或条目数量过大；本次未更新任何人数')
        game = aliases[match[1].casefold()]
        if game.key in seen:
            raise ValueError(f'{game.name} 重复填写；本次未更新任何人数')
        reports.append(ArcadeAttendanceReport(game, match[2] or '=', int(match[3])))
        seen.add(game.key)
        position = match.end()
    return reports
