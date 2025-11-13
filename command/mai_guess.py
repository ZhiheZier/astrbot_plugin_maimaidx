import asyncio
import base64
import tempfile
from textwrap import dedent

import astrbot.api.message_components as Comp

from astrbot.api.event import AstrMessageEvent

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


async def guess_music_handler(event: AstrMessageEvent):
    """猜歌命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('猜歌功能仅在群聊中可用')
        return
    
    # 将 group_id 转换为整数
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f'无法转换 group_id 为整数: {group_id} (类型: {type(group_id)})')
        yield event.plain_result('获取群组信息失败，请稍后重试')
        return
    
    # 检查是否在启用列表中
    if group_id not in guess.switch.enable:
        yield event.plain_result('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
        return
    
    if group_id in guess.Group:
        yield event.plain_result('该群已有正在进行的猜歌或猜曲绘')
        return
    
    # 尝试开始猜歌，如果失败则返回错误信息
    try:
        guess.start(group_id)
    except Exception as e:
        log.error(f'开始猜歌失败: {e}')
        import traceback
        log.error(traceback.format_exc())
        yield event.plain_result(f'开始猜歌失败，请稍后重试或联系管理员。错误信息: {str(e)}')
        return
    
    yield event.plain_result(dedent(''' \
        我将从热门乐曲中选择一首歌，每隔8秒描述它的特征，
        请输入歌曲的 id 标题 或 别名（需bot支持，无需大小写） 进行猜歌（DX乐谱和标准乐谱视为两首歌）。
        猜歌时查歌等其他命令依然可用。
    ''').strip())
    
    # 获取 bot client 用于发送消息
    bot_client = event.bot
    
    # 异步发送后续消息
    async def send_hints():
        await asyncio.sleep(4)
        for cycle in range(7):
            if group_id not in guess.switch.enable or group_id not in guess.Group or guess.Group[group_id].end:
                break
            if cycle < 6:
                # 发送提示信息
                hint_text = guess.Group[group_id].hint[cycle]
                try:
                    await bot_client.send_group_msg(group_id=group_id, message=f'{cycle + 1}/7 {hint_text}')
                except Exception as e:
                    log.error(f'发送猜歌提示失败: {e}')
                await asyncio.sleep(8)
            else:
                # 发送图片和提示
                img_data = guess.Group[group_id].img
                # img_data 是 base64://... 格式的字符串
                chain = [Comp.Plain('7/7 这首歌封面的一部分是：\n')]
                
                # 将 base64 字符串转换为临时文件
                if isinstance(img_data, str) and img_data.startswith('base64://'):
                    try:
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
                
                chain.append(Comp.Plain('\n答案将在30秒后揭晓'))
                try:
                    await bot_client.send_group_msg(group_id=group_id, message=chain)
                except Exception as e:
                    log.error(f'发送猜歌图片提示失败: {e}')
                for _ in range(30):
                    await asyncio.sleep(1)
                    if group_id in guess.Group:
                        if group_id not in guess.switch.enable or guess.Group[group_id].end:
                            return
                    else:
                        return
                guess.Group[group_id].end = True
                pic = await draw_music_info(guess.Group[group_id].music)
                answer_chain = convert_message_segment_to_chain(pic)
                answer_chain.insert(0, Comp.Plain('答案是：\n'))
                try:
                    await bot_client.send_group_msg(group_id=group_id, message=answer_chain)
                except Exception as e:
                    log.error(f'发送猜歌答案失败: {e}')
                guess.end(group_id)
    
    # 启动异步任务
    asyncio.create_task(send_hints())


async def guess_pic_handler(event: AstrMessageEvent):
    """猜曲绘命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('猜曲绘功能仅在群聊中可用')
        return
    
    # 将 group_id 转换为整数
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f'无法转换 group_id 为整数: {group_id} (类型: {type(group_id)})')
        yield event.plain_result('获取群组信息失败，请稍后重试')
        return
    
    # 检查是否在启用列表中
    if group_id not in guess.switch.enable:
        yield event.plain_result('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
        return
    
    if group_id in guess.Group:
        yield event.plain_result('该群已有正在进行的猜歌或猜曲绘')
        return
    
    # 尝试开始猜曲绘，如果失败则返回错误信息
    try:
        guess.startpic(group_id)
    except Exception as e:
        log.error(f'开始猜曲绘失败: {e}')
        import traceback
        log.error(traceback.format_exc())
        yield event.plain_result(f'开始猜曲绘失败，请稍后重试或联系管理员。错误信息: {str(e)}')
        return
    
    img_data = guess.Group[group_id].img
    # img_data 是 base64://... 格式的字符串
    chain = [Comp.Plain('以下裁切图片是哪首谱面的曲绘：\n')]
    
    # 将 base64 字符串转换为临时文件
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
    
    # 获取 bot client 用于发送消息
    bot_client = event.bot
    
    # 异步等待30秒后发送答案
    async def send_answer():
        for _ in range(30):
            await asyncio.sleep(1)
            if group_id in guess.Group:
                if group_id not in guess.switch.enable or guess.Group[group_id].end:
                    return
            else:
                return
        guess.Group[group_id].end = True
        pic = await draw_music_info(guess.Group[group_id].music)
        answer_chain = convert_message_segment_to_chain(pic)
        answer_chain.insert(0, Comp.Plain('答案是：\n'))
        try:
            await bot_client.send_group_msg(group_id=group_id, message=answer_chain)
        except Exception as e:
            log.error(f'发送猜曲绘答案失败: {e}')
        guess.end(group_id)
    
    asyncio.create_task(send_answer())


async def guess_music_solve_handler(event: AstrMessageEvent):
    """猜歌答案处理（监听所有消息）"""
    group_id = event.message_obj.group_id
    if not group_id:
        return  # 私聊不处理
    
    # 将 group_id 转换为整数
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        return  # 无法转换则忽略
    
    if group_id not in guess.Group:
        return  # 该群没有进行中的猜歌
    
    ans = event.message_str.strip().lower()
    if not ans:
        return
    
    # 检查答案（支持多种匹配方式）
    answer_list = guess.Group[group_id].answer
    if ans in answer_list or any(ans in str(a).lower() for a in answer_list):
        guess.Group[group_id].end = True
        pic = await draw_music_info(guess.Group[group_id].music)
        answer_chain = convert_message_segment_to_chain(pic)
        answer_chain.insert(0, Comp.Plain('猜对了，答案是：\n'))
        answer_chain.insert(1, Comp.At(qq=event.get_sender_id()))
        yield event.chain_result(answer_chain)
        guess.end(group_id)


async def reset_guess_handler(event: AstrMessageEvent):
    """重置猜歌命令处理"""
    group_id = event.message_obj.group_id
    if not group_id:
        yield event.plain_result('重置猜歌功能仅在群聊中可用')
        return
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员重置')
        return
    
    # 将 group_id 转换为整数
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        yield event.plain_result('获取群组信息失败，请稍后重试')
        return
    
    if group_id in guess.Group:
        guess.end(group_id)
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
    
    # 将 group_id 转换为整数
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        yield event.plain_result('获取群组信息失败，请稍后重试')
        return
    
    message_str = event.message_str.strip()
    # 移除后缀
    for suffix in ['开启mai猜歌', '关闭mai猜歌']:
        if message_str.endswith(suffix):
            args = suffix.replace('mai猜歌', '').strip()
            break
    else:
        args = ''
    
    if args == '开启':
        msg = await guess.on(group_id)
    elif args == '关闭':
        msg = await guess.off(group_id)
    else:
        msg = '指令错误，请使用「开启mai猜歌」或「关闭mai猜歌」'
    
    yield event.plain_result(msg)