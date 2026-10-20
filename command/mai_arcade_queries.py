"""Cloud-first, read-only arcade queries with a labeled local fallback."""

from dataclasses import replace

from ..libraries.maimaidx_arcade_messages import format_arcade_query, local_arcade_query, split_arcade_query_messages
from ..libraries.maimaidx_nearcade import NearcadeError


async def query_arcade_blocks(records, configured, nearcade=None, *, selected=None, compact=False):
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
            if selected is None or not selected.is_named:
                totals = {row.game.key for row in rows if not row.game.is_named}
                rows = tuple(row for row in rows if ':@' not in row.game.key or row.game.family_key not in totals)
            result = replace(result, name=result.name or record.name, games=rows)
        messages.append(format_arcade_query(result, compact=compact))
    return messages


async def query_arcade_messages(records, configured, nearcade=None, *, selected=None, compact=False):
    blocks = await query_arcade_blocks(records, configured, nearcade, selected=selected, compact=compact)
    return list(split_arcade_query_messages(blocks, compact=compact))
