from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


async def can_manage_group(event: AstrMessageEvent, administrators: list | None = None) -> bool:
    """Administrators inherit group management rights; group commands still need a group."""
    group_id = event.message_obj.group_id
    if not group_id:
        return False
    sender_id = str(event.get_sender_id())
    if sender_id in {str(admin) for admin in administrators or []}:
        return True
    try:
        member = await event.bot.get_group_member_info(group_id=group_id, user_id=event.get_sender_id())
        return bool(member and member.get('role') in ('owner', 'admin'))
    except Exception as exc:
        logger.warning(f'检查群管理员权限失败: {exc}')
        return False
