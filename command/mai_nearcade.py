from astrbot.api.event import AstrMessageEvent

from ..libraries.maimaidx_nearcade import (
    NearcadeClient,
    NearcadeError,
    extract_qq_location,
    format_nearby_shops,
)


async def nearby_arcades_handler(event: AstrMessageEvent, client: NearcadeClient):
    if not client.location_enabled:
        return
    point = extract_qq_location(event)
    if point is None:
        return
    try:
        shops = await client.nearby(point)
        message = ''
        for block in format_nearby_shops(shops, client.radius, point).split('\n\n'):
            combined = f'{message}\n\n{block}' if message else block
            if message and len(combined) > 1800:
                yield event.plain_result(message)
                message = block
            else:
                message = combined
        if message:
            yield event.plain_result(message)
    except NearcadeError as exc:
        yield event.plain_result(f'附近机厅查询失败：{exc}')
