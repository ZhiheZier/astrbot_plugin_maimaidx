import asyncio
import base64
import tempfile
from textwrap import dedent
from typing import Any, List

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType

from .. import log
from ..command.mai_base import convert_message_segment_to_chain
from ..libraries.maimaidx_music import guess
from ..libraries.maimaidx_music_info import draw_music_info


async def is_admin(event: AstrMessageEvent) -> bool:
    """检查用户是否是群管理员或群主"""
    group_id = event.message_obj.group_id
    if not group_id:
        return False  # 私聊不支持管理员检查
    
    try:
        sender_id = event.get_sender_id()
        member_info = await event.bot.get_group_member_info(group_id=group_id, user_id=sender_id)
        if member_info:
            role = member_info.get('role', '')
            # role 可能是 'owner', 'admin', 'member'
            return role in ['owner', 'admin']
    except Exception as e:
        log.warning(f'检查管理员权限失败: {e}')
    return False


async def convert_chain_to_onebot_format(chain: List[Any]):
    """将消息链转换为 OneBot 协议格式（字典列表）"""
    result = []
    for item in chain:
        if isinstance(item, str):
            # 字符串直接转换为文本消息
            if item.strip():
                result.append({"type": "text", "data": {"text": item}})
        elif isinstance(item, Comp.Plain):
            # Plain 组件转换为文本消息
            text = item.text if hasattr(item, 'text') else str(item)
            if text.strip():
                result.append({"type": "text", "data": {"text": text}})
        elif isinstance(item, Comp.Image):
            # Image 组件转换为图片消息
            try:
                # 使用 convert_to_base64 方法获取 base64 字符串
                base64_str = await item.convert_to_base64()
                result.append({"type": "image", "data": {"file": f"base64://{base64_str}"}})
            except Exception as e:
                log.error(f'转换图片失败: {e}')
                result.append({"type": "text", "data": {"text": "[图片加载失败]"}})
        elif isinstance(item, Comp.At):
            # At 组件转换为 @ 消息
            qq_id = item.qq if hasattr(item, 'qq') else ''
            if qq_id:
                result.append({"type": "at", "data": {"qq": str(qq_id)}})
        elif hasattr(item, 'type'):
            # 其他消息组件，尝试转换为字典
            try:
                if hasattr(item, 'toDict'):
                    result.append(item.toDict())
                elif hasattr(item, 'to_dict'):
                    result.append(item.to_dict())
                else:
                    # 尝试手动构建
                    comp_type = str(item.type).lower() if hasattr(item, 'type') else 'text'
                    comp_data = {}
                    if hasattr(item, 'text'):
                        comp_data = {"text": item.text}
                    result.append({"type": comp_type, "data": comp_data})
            except Exception as e:
                log.warning(f'转换消息组件失败: {e}, item: {item}')
                result.append({"type": "text", "data": {"text": str(item)}})
        else:
            # 其他类型，转换为文本
            result.append({"type": "text", "data": {"text": str(item)}})
    return result


async def guess_music_handler(event: AstrMessageEvent):
    """猜歌命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('猜歌功能仅在群聊中可用')
        return
    
    gid = str(group_id)
    
    # 检查是否在启用列表中
    if gid not in guess.switch.enable:
        yield event.plain_result('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
        return
    
    if gid in guess.Group:
        yield event.plain_result('该群已有正在进行的猜歌或猜曲绘')
        return
    
    # 开始猜歌
    try:
        guess.start(gid)
    except Exception as e:
        log.error(f'开始猜歌失败: {e}')
        import traceback
        log.error(traceback.format_exc())
        yield event.plain_result(f'开始猜歌失败，请稍后重试或联系管理员。错误信息: {str(e)}')
        return
    
    yield event.plain_result(dedent('''\
        我将从热门乐曲中选择一首歌，每隔8秒描述它的特征，
        请输入歌曲的 id 标题 或 别名（需bot支持，无需大小写）进行猜歌（DX乐谱和标准乐谱视为两首歌）。
        猜歌时查歌等其他命令依然可用。
    ''').strip())
    
    # 保存 bot 客户端和 group_id 用于异步任务
    bot_client = event.bot
    group_id_int = int(gid) if gid.isdigit() else gid
    
    # 异步发送后续消息
    async def send_hints():
        await asyncio.sleep(4)
        for cycle in range(7):
            if gid not in guess.switch.enable or gid not in guess.Group or guess.Group[gid].end:
                break
            if cycle < 6:
                # 发送提示信息（纯文本）
                # 注意：只有 GuessDefaultData 有 options 属性，GuessPicData 没有
                guess_data = guess.Group[gid]
                if hasattr(guess_data, 'options'):
                    hint_text = guess_data.options[cycle]
                else:
                    # 如果是 GuessPicData，不应该到这里，但为了安全起见
                    break
                try:
                    await bot_client.send_group_msg(
                        group_id=group_id_int,
                        message=f'{cycle + 1}/7 这首歌{hint_text}'
                    )
                except Exception as e:
                    log.error(f'发送猜歌提示失败: {e}')
                await asyncio.sleep(8)
            else:
                # 发送图片和提示（第7条）
                img_data = guess.Group[gid].img
                chain: List[Any] = [Comp.Plain('7/7 这首歌封面的一部分是：\n')]
                
                # 处理 base64 图片
                if isinstance(img_data, str) and img_data.startswith('base64://'):
                    try:
                        # 如果失败，尝试保存为临时文件
                        base64_data = img_data[9:]  # 移除 'base64://' 前缀
                        img_bytes = base64.b64decode(base64_data)
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
                            temp_file.write(img_bytes)
                            temp_file_path = temp_file.name
                        chain.append(Comp.Image.fromFileSystem(temp_file_path))
                    except Exception as e:
                        log.error(f'处理猜歌图片失败: {e}')
                        chain.append(Comp.Plain('[图片加载失败]'))
                else:
                    chain.append(Comp.Plain(str(img_data)))
                
                chain.append(Comp.Plain('答案将在30秒后揭晓'))
                
                # 转换为 OneBot 格式
                try:
                    onebot_chain = await convert_chain_to_onebot_format(chain)
                    await bot_client.send_group_msg(
                        group_id=group_id_int,
                        message=onebot_chain
                    )
                except Exception as e:
                    log.error(f'发送猜歌图片提示失败: {e}')
                    import traceback
                    log.error(traceback.format_exc())
                
                # 等待30秒
                for _ in range(30):
                    await asyncio.sleep(1)
                    if gid in guess.Group:
                        if gid not in guess.switch.enable or guess.Group[gid].end:
                            return
                    else:
                        return
                
                # 发送答案
                guess.Group[gid].end = True
                pic = await draw_music_info(guess.Group[gid].music)
                answer_chain: List[Any] = convert_message_segment_to_chain(pic)
                answer_chain.insert(0, Comp.Plain('答案是：\n'))
                
                # 转换为 OneBot 格式
                try:
                    onebot_answer = await convert_chain_to_onebot_format(answer_chain)
                    await bot_client.send_group_msg(
                        group_id=group_id_int,
                        message=onebot_answer
                    )
                except Exception as e:
                    log.error(f'发送猜歌答案失败: {e}')
                    import traceback
                    log.error(traceback.format_exc())
                guess.end(gid)
    
    # 启动异步任务
    asyncio.create_task(send_hints())


async def guess_pic_handler(event: AstrMessageEvent):
    """猜曲绘命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('猜曲绘功能仅在群聊中可用')
        return
    
    gid = str(group_id)
    
    # 检查是否在启用列表中
    if gid not in guess.switch.enable:
        yield event.plain_result('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
        return
    
    if gid in guess.Group:
        yield event.plain_result('该群已有正在进行的猜歌或猜曲绘')
        return
    
    # 开始猜曲绘
    try:
        guess.startpic(gid)
    except Exception as e:
        log.error(f'开始猜曲绘失败: {e}')
        import traceback
        log.error(traceback.format_exc())
        yield event.plain_result(f'开始猜曲绘失败，请稍后重试或联系管理员。错误信息: {str(e)}')
        return
    
    # 发送图片和提示
    img_data = guess.Group[gid].img
    chain: List[Any] = [Comp.Plain('以下裁切图片是哪首谱面的曲绘：\n')]
    
    # 处理 base64 图片
    if isinstance(img_data, str) and img_data.startswith('base64://'):
        try:
            base64_data = img_data[9:]  # 移除 'base64://' 前缀
            img_bytes = base64.b64decode(base64_data)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
                temp_file.write(img_bytes)
                temp_file_path = temp_file.name
            chain.append(Comp.Image.fromFileSystem(temp_file_path))
        except Exception as e:
            log.error(f'处理猜曲绘图片失败: {e}')
            chain.append(Comp.Plain('[图片加载失败]'))
    else:
        chain.append(Comp.Plain(str(img_data)))
    
    chain.append(Comp.Plain('\n请在30s内输入答案'))
    yield event.chain_result(chain)
    
    # 保存 bot 客户端和 group_id 用于异步任务
    bot_client = event.bot
    group_id_int = int(gid) if gid.isdigit() else gid
    
    # 异步等待30秒后发送答案
    async def send_answer():
        for _ in range(30):
            await asyncio.sleep(1)
            if gid in guess.Group:
                if gid not in guess.switch.enable or guess.Group[gid].end:
                    return
            else:
                return
        
        # 发送答案
        guess.Group[gid].end = True
        pic = await draw_music_info(guess.Group[gid].music)
        answer_chain: List[Any] = convert_message_segment_to_chain(pic)
        answer_chain.insert(0, Comp.Plain('答案是：\n'))
        
        # 转换为 OneBot 格式
        try:
            onebot_answer = await convert_chain_to_onebot_format(answer_chain)
            await bot_client.send_group_msg(
                group_id=group_id_int,
                message=onebot_answer
            )
        except Exception as e:
            log.error(f'发送猜曲绘答案失败: {e}')
            import traceback
            log.error(traceback.format_exc())
        guess.end(gid)
    
    asyncio.create_task(send_answer())


async def guess_music_solve_handler(event: AstrMessageEvent):
    """猜歌答案处理（监听所有群消息）"""
    group_id = event.message_obj.group_id
    if not group_id:
        return  # 私聊不处理
    
    gid = str(group_id)
    
    if gid not in guess.Group:
        return  # 该群没有进行中的猜歌
    
    ans = event.message_str.strip().lower()
    if not ans:
        return
    
    # 检查答案（将答案列表中的所有答案转为小写，然后检查用户输入是否在其中）
    answer_list = guess.Group[gid].answer
    answer_list_lower = [str(a).lower() for a in answer_list]
    if ans in answer_list_lower:
        guess.Group[gid].end = True
        pic = await draw_music_info(guess.Group[gid].music)
        answer_chain: List[Any] = convert_message_segment_to_chain(pic)
        # event.chain_result 需要消息组件对象
        answer_chain.insert(0, Comp.At(qq=event.get_sender_id()))
        answer_chain.insert(1, Comp.Plain('\n猜对了，答案是：\n'))
        yield event.chain_result(answer_chain)
        guess.end(gid)


async def reset_guess_handler(event: AstrMessageEvent):
    """重置猜歌命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('重置猜歌功能仅在群聊中可用')
        return
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员重置')
        return
    
    gid = str(group_id)
    if gid in guess.Group:
        guess.end(gid)
        yield event.plain_result('已重置该群猜歌')
    else:
        yield event.plain_result('该群未处在猜歌状态')


async def guess_on_off_handler(event: AstrMessageEvent):
    """开启/关闭mai猜歌命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('开启/关闭猜歌功能仅在群聊中可用')
        return
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员开关')
        return
    
    gid = str(group_id)
    message_str = event.message_str.strip()
    
    # 判断是开启还是关闭
    if message_str.endswith('开启mai猜歌'):
        msg = await guess.on(gid)
    elif message_str.endswith('关闭mai猜歌'):
        msg = await guess.off(gid)
    else:
        msg = '指令错误，请使用「开启mai猜歌」或「关闭mai猜歌」'
    
    yield event.plain_result(msg)

