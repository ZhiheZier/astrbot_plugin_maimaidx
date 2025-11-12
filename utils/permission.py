"""
权限检查工具函数
用于检查用户是否为群主、管理员等
"""
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger


async def is_superuser(event: AstrMessageEvent, superusers: list = None) -> bool:
    """
    检查用户是否为超级管理员
    
    Args:
        event: 消息事件
        superusers: 超级管理员列表（可选，从配置中读取）
    
    Returns:
        bool: 是否为超级管理员
    """
    if superusers is None:
        # 可以从配置中读取
        return False
    
    user_id = str(event.get_sender_id())
    return user_id in superusers


async def is_owner(event: AstrMessageEvent) -> bool:
    """
    检查用户是否为群主
    
    Args:
        event: 消息事件
    
    Returns:
        bool: 是否为群主
    """
    if event.is_private_chat():
        return False
    
    try:
        # group_id 是直接属性，私聊时为空字符串
        group_id = event.message_obj.group_id
        if not group_id:  # 空字符串或 None 都表示不是群聊
            return False
        
        user_id = event.get_sender_id()
        info = await event.bot.get_group_member_info(
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=True
        )
        role = info.get("role", "unknown")
        return role == "owner"
    except Exception as e:
        logger.warning(f"检查群主权限失败: {e}")
        return False


async def is_admin(event: AstrMessageEvent) -> bool:
    """
    检查用户是否为管理员（包括群主）
    
    Args:
        event: 消息事件
    
    Returns:
        bool: 是否为管理员或群主
    """
    if event.is_private_chat():
        return False
    
    try:
        # group_id 是直接属性，私聊时为空字符串
        group_id = event.message_obj.group_id
        if not group_id:  # 空字符串或 None 都表示不是群聊
            return False
        
        user_id = event.get_sender_id()
        info = await event.bot.get_group_member_info(
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=True
        )
        role = info.get("role", "unknown")
        return role in ["owner", "admin"]
    except Exception as e:
        logger.warning(f"检查管理员权限失败: {e}")
        return False


async def check_permission(event: AstrMessageEvent, require_owner: bool = False, require_admin: bool = False, superusers: list = None) -> bool:
    """
    检查用户权限
    
    Args:
        event: 消息事件
        require_owner: 是否需要群主权限
        require_admin: 是否需要管理员权限（包括群主）
        superusers: 超级管理员列表（可选）
    
    Returns:
        bool: 是否有权限
    """
    # 先检查超级管理员
    if superusers and await is_superuser(event, superusers):
        return True
    
    # 检查群主权限
    if require_owner:
        return await is_owner(event)
    
    # 检查管理员权限
    if require_admin:
        return await is_admin(event)
    
    return True

