"""Shared presentation for local arcade attendance reports and queries."""

from dataclasses import dataclass

from .maimaidx_arcade_games import ArcadeGameType, DEFAULT_ARCADE_GAME


@dataclass(frozen=True)
class ArcadeWaitEstimate:
    capacity: int
    average: int
    minimum: int
    maximum: int
    min_rounds: int
    max_rounds: int


@dataclass(frozen=True)
class ArcadeQueryGame:
    game: ArcadeGameType
    person: int | None
    machines: int | None
    by: str = ''
    time: str = ''
    status: str = '营业'
    label: str = ''
    records: tuple['ArcadeQueryGame', ...] = ()

    def attendance_for(self, game):
        return self


@dataclass(frozen=True)
class ArcadeQueryResult:
    name: str
    games: tuple[ArcadeQueryGame, ...]
    source: str = '本地'
    warning: str = ''


def local_arcade_query(record, configured=(), selected=None, warning='') -> ArcadeQueryResult:
    choices = {game.key: game for game in record.known_games()}
    choices.update((game.key, game) for game in configured)
    families = {game.family_key for game in choices.values() if game.is_named and ':@' not in game.key}
    if 'mai' not in families or attendance_is_known(record, DEFAULT_ARCADE_GAME):
        choices.setdefault('mai', DEFAULT_ARCADE_GAME)
    if selected is None or (not selected.is_named and selected.key in families):
        games = [game for game in choices.values() if selected is None or game.family_key == selected.key]
        games.sort(key=lambda game: (game.family_key != 'mai', game.family_key, game.key))
    else:
        games = [selected]
    rows = []
    for game in games:
        attendance = record.attendance_for(game)
        label = game.name + ('（未分区旧记录）' if not game.is_named and game.key in families else '')
        rows.append(ArcadeQueryGame(game, attendance.person if attendance_is_known(record, game) else None,
                                   attendance_machine_count(record, game, choices.values()),
                                   attendance.by, attendance.time, attendance.status, label))
    return ArcadeQueryResult(record.name, tuple(rows), warning=warning)


def format_arcade_query(result: ArcadeQueryResult, *, compact=False) -> str:
    name = ' '.join(result.name.split())
    if compact:
        rows = [f'店名：{name}']
        if result.source != 'nearcade':
            source = '本地·云端查询失败' if result.warning else '本地'
            rows.append(f'  - 数据来源：{source}')
        for category in result.games:
            for row in (category.records or (category,)):
                count = '未上报' if row.person is None else f'{row.person}人'
                status = '（闭店）' if row.status == '闭店' else ''
                if category.records:
                    label = row.label or row.game.name
                else:
                    label = '舞萌' if row.game.key == 'mai' else '中二' if row.game.key == 'chu' else row.label or row.game.name
                    if '未分区旧记录' in row.label:
                        label += '（未分区旧记录）'
                rows.append(f'  - {label}：{count}{status}')
        if not result.games:
            rows.append('  - 暂无游戏记录')
        return '\n'.join(rows)
    source = result.source + (f'（云端查询失败：{result.warning}）' if result.warning else '')
    blocks = ['\n'.join(format_attendance_lines(row, row.game, label=row.label or row.game.name,
                                              machine_count=row.machines, known=row.person is not None))
              for category in result.games for row in (category.records or (category,))]
    return f'📍 机厅：{name}\n数据来源：{source}\n' + '\n\n'.join(blocks or ['暂无对应游戏记录'])


def split_arcade_query_messages(blocks, *, compact=False, limit=1800):
    separator = '\n\n'
    message = ''
    for block in blocks:
        combined = message + separator + block if message else block
        if message and len(combined) > limit:
            yield message
            message = ''
        while len(block) > limit:
            if message:
                yield message
                message = ''
            yield block[:limit]
            block = block[limit:]
        message = message + separator + block if message else block
    if message:
        yield message


def arcade_round_settings(game=DEFAULT_ARCADE_GAME) -> tuple[int, int]:
    return (16, 2) if game.family_key == 'mai' else (12, 1)


def estimate_arcade_wait(person: int, machines: int, game=DEFAULT_ARCADE_GAME) -> ArcadeWaitEstimate | None:
    if (isinstance(person, bool) or isinstance(machines, bool)
            or not isinstance(person, int) or not isinstance(machines, int) or person < 0 or machines <= 0):
        return None
    duration, players_per_machine = arcade_round_settings(game)
    capacity = machines * players_per_machine
    waiting = max(person - capacity, 0)
    minimum, remainder = divmod(waiting, capacity)
    maximum = minimum + bool(remainder)
    return ArcadeWaitEstimate(capacity, round(waiting * duration / capacity), minimum * duration, maximum * duration,
                              minimum, maximum)


def attendance_is_known(record, game) -> bool:
    if game.key == DEFAULT_ARCADE_GAME.key:
        return bool(record.person or record.by or record.time or record.status == '闭店')
    return game.key in record.game_attendance


def attendance_machine_count(record, game, choices=()) -> int | None:
    if game.key in record.game_machine_counts:
        count = record.game_machine_counts[game.key]
        return count if count >= 0 else None
    # The local cabinet count belongs to unpartitioned maimai, not every game/zone.
    if game.key == DEFAULT_ARCADE_GAME.key and not any(item.is_named and ':@' not in item.key and item.family_key == 'mai' for item in choices):
        return record.num if record.num > 0 else None
    return None


def format_attendance_lines(record, game, *, label=None, headline=None, machine_count=None,
                            known=None) -> list[str]:
    attendance = record.attendance_for(game)
    label = label or game.name
    known = attendance_is_known(record, game) if known is None else known
    if not known:
        return [f'🎮 {label}：尚未上报']
    state = '（闭店）' if attendance.status == '闭店' else ''
    lines = [f'🎮 {label}：{headline or f"当前 {attendance.person} 人{state}"}']
    if attendance.status != '闭店':
        if machine_count is None:
            lines.append('🕹️ 机台数量未知')
        elif machine_count <= 0:
            lines.append('🕹️ 机台数量：0 台，暂无可用机台')
        else:
            lines.append(f'🕹️ 机台数量：{machine_count} 台')
    lines.append(f'（{attendance.by or "未知用户"} · {attendance.time or "时间未知"}）')
    return lines
