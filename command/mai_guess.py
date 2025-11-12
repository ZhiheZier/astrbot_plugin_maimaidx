import asyncio
import tempfile
from textwrap import dedent
import astrbot.api.message_components as Comp

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType

from .. import log
from ..libraries.maimaidx_music import guess
from ..libraries.maimaidx_music_info import draw_music_info
from .mai_base import convert_message_segment_to_chain


async def guess_music_start_handler(event: AstrMessageEvent):
    """猜歌 - 开始猜歌"""
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    if group_id not in guess.switch.enable:
        yield event.plain_result('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
        return
    
    if group_id in guess.Group:
        yield event.plain_result('该群已有正在进行的猜歌或猜曲绘')
        return
    
    guess.start(group_id)
    yield event.plain_result(dedent(''' \
        我将从热门乐曲中选择一首歌，每隔8秒描述它的特征，
        请输入歌曲的 id 标题 或 别名（需bot支持，无需大小写） 进行猜歌（DX乐谱和标准乐谱视为两首歌）。
        猜歌时查歌等其他命令依然可用。
    '''))
    
    await asyncio.sleep(4)
    for cycle in range(7):
        if group_id not in guess.switch.enable or group_id not in guess.Group or guess.Group[group_id].end:
            break
        if cycle < 6:
            yield event.plain_result(f'{cycle + 1}/7 这首歌{guess.Group[group_id].options[cycle]}')
            await asyncio.sleep(8)
        else:
            # 发送图片
            img_base64 = guess.Group[group_id].img
            if img_base64.startswith('base64://'):
                import base64
                from io import BytesIO
                base64_data = img_base64.replace('base64://', '')
                img_data = base64.b64decode(base64_data)
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                temp_file.write(img_data)
                temp_file.close()
                
                chain = [
                    Comp.Plain('7/7 这首歌封面的一部分是：\n'),
                    Comp.Image.fromFileSystem(temp_file.name),
                    Comp.Plain('答案将在30秒后揭晓')
                ]
                yield event.chain_result(chain)
            
            for _ in range(30):
                await asyncio.sleep(1)
                if group_id in guess.Group:
                    if group_id not in guess.switch.enable or guess.Group[group_id].end:
                        return
                else:
                    return
            
            guess.Group[group_id].end = True
            result_msg = await draw_music_info(guess.Group[group_id].music)
            chain = convert_message_segment_to_chain(result_msg)
            chain.insert(0, Comp.Plain('答案是：\n'))
            guess.end(group_id)
            yield event.chain_result(chain)


async def guess_pic_handler(event: AstrMessageEvent):
    """猜曲绘 - 开始猜曲绘"""
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    if group_id not in guess.switch.enable:
        yield event.plain_result('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
        return
    
    if group_id in guess.Group:
        yield event.plain_result('该群已有正在进行的猜歌或猜曲绘')
        return
    
    guess.startpic(group_id)
    
    # 发送图片
    img_base64 = guess.Group[group_id].img
    if img_base64.startswith('base64://'):
        import base64
        base64_data = img_base64.replace('base64://', '')
        img_data = base64.b64decode(base64_data)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        temp_file.write(img_data)
        temp_file.close()
        
        chain = [
            Comp.Plain('以下裁切图片是哪首谱面的曲绘：\n'),
            Comp.Image.fromFileSystem(temp_file.name),
            Comp.Plain('请在30s内输入答案')
        ]
        yield event.chain_result(chain)
    
    for _ in range(30):
        await asyncio.sleep(1)
        if group_id in guess.Group:
            if group_id not in guess.switch.enable or guess.Group[group_id].end:
                return
        else:
            return
    
    guess.Group[group_id].end = True
    result_msg = await draw_music_info(guess.Group[group_id].music)
    chain = convert_message_segment_to_chain(result_msg)
    chain.insert(0, Comp.Plain('答案是：\n'))
    guess.end(group_id)
    yield event.chain_result(chain)


async def guess_music_solve_handler(event: AstrMessageEvent):
    """处理猜歌答案 - 需要注册为消息监听器"""
    try:
        # 只处理群消息
        if event.is_private_chat():
            return
        
        # 获取群ID
        group_id = event.message_obj.group_id  # 私聊时为空字符串
        if not group_id:
            return
        
        # 检查该群是否在猜歌状态
        if group_id not in guess.Group:
            return
        
        # 缓存 guess 数据，避免重复访问
        guess_data = guess.Group[group_id]
        
        # 检查该群是否开启了猜歌功能
        if group_id not in guess.switch.enable:
            return
        
        # 检查猜歌是否已结束
        if guess_data.end:
            return
        
        # 检查消息内容是否为空
        message_str = event.message_str.strip()
        if not message_str:
            return
        
        # 优化答案匹配：去除空格、标点符号，统一小写
        ans = message_str.lower().strip()
        # 移除常见的标点符号和空格
        ans_normalized = ''.join(c for c in ans if c.isalnum() or c in [' ', '-', '_'])
        ans_normalized = ans_normalized.replace(' ', '').replace('-', '').replace('_', '')
        
        # 检查答案（支持原始答案和规范化后的答案）
        answer_list = guess_data.answer
        matched = False
        
        # 先检查原始答案
        if ans in answer_list:
            matched = True
        # 再检查规范化后的答案
        elif ans_normalized:
            for answer in answer_list:
                answer_normalized = ''.join(c for c in answer.lower() if c.isalnum() or c in [' ', '-', '_'])
                answer_normalized = answer_normalized.replace(' ', '').replace('-', '').replace('_', '')
                if ans_normalized == answer_normalized:
                    matched = True
                    break
        
        if matched:
            guess_data.end = True
            result_msg = await draw_music_info(guess_data.music)
            chain = convert_message_segment_to_chain(result_msg)
            chain.insert(0, Comp.Plain('猜对了，答案是：\n'))
            guess.end(group_id)
            yield event.chain_result(chain)
    except Exception as e:
        # 错误处理：记录错误但不影响其他功能
        log.error(f'猜歌答案处理失败: {e}', exc_info=True)
        return


async def reset_guess_handler(event: AstrMessageEvent):
    """重置猜歌"""
    from ..utils.permission import is_admin
    
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员重置')
        return
    
    if group_id in guess.Group:
        guess.end(group_id)
        yield event.plain_result('已重置该群猜歌')
    else:
        yield event.plain_result('该群未处在猜歌状态')


async def guess_on_off_handler(event: AstrMessageEvent):
    """开启mai猜歌/关闭mai猜歌"""
    from ..utils.permission import is_admin
    
    group_id = event.message_obj.group_id  # 私聊时为空字符串
    if not group_id:
        yield event.plain_result('此功能仅在群聊中可用')
        return
    
    message_str = event.message_str.strip()
    args = message_str.replace('开启mai猜歌', '').replace('关闭mai猜歌', '').strip()
    
    if not await is_admin(event):
        yield event.plain_result('仅允许管理员开关')
        return
    
    if '开启' in message_str:
        msg = await guess.on(group_id)
    elif '关闭' in message_str:
        msg = await guess.off(group_id)
    else:
        msg = '指令错误'
    
    yield event.plain_result(msg)
