"""Fold long arcade lists into one QQ merged-forward message."""

import astrbot.api.message_components as Comp

from .. import get_botname
from ..libraries.maimaidx_arcade_messages import split_arcade_query_messages


ARCADE_TEXT_LIMIT = 1000
ARCADE_LINE_LIMIT = 30
ARCADE_NODE_LIMIT = 1500


def arcade_list_results(event, blocks):
    blocks = list(blocks)
    if not blocks:
        return
    message = '\n\n'.join(blocks)
    if len(message) <= ARCADE_TEXT_LIMIT and len(message.splitlines()) <= ARCADE_LINE_LIMIT:
        yield event.plain_result(message)
    elif event.get_platform_name() == 'aiocqhttp':
        nodes = [
            Comp.Node(uin=str(event.get_self_id()), name=get_botname(), content=[Comp.Plain(chunk)])
            for block in blocks
            for chunk in split_arcade_query_messages([block], limit=ARCADE_NODE_LIMIT)
        ]
        yield event.chain_result([Comp.Nodes(nodes)])
    else:
        for chunk in split_arcade_query_messages(blocks, limit=ARCADE_TEXT_LIMIT):
            yield event.plain_result(chunk)
