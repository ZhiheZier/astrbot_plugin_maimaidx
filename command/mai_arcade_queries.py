"""Cloud-first, read-only arcade queries with a labeled local fallback."""

from dataclasses import replace

from ..libraries.maimaidx_arcade_messages import format_arcade_query, local_arcade_query, split_arcade_query_messages
from ..libraries.maimaidx_nearcade import NearcadeError


async def query_arcade_messages(records, configured, nearcade=None, *, selected=None, compact=False):
    cloud = await nearcade.read_attendance(record.id for record in records) if nearcade else {}
    messages = []
    for record in records:
        result = cloud.get(record.id)
        if result is None or isinstance(result, NearcadeError):
            result = local_arcade_query(record, configured.get(record.id, []), selected,
                                        str(result) if isinstance(result, NearcadeError) else '')
        else:
            rows = result.games
            if selected:
                rows = tuple(row for row in rows if row.game.key == selected.key
                             or (not selected.is_named and row.game.family_key == selected.key))
            result = replace(result, name=result.name or record.name, games=rows)
        messages.append(format_arcade_query(result, compact=compact))
    return list(split_arcade_query_messages(messages, compact=compact))
