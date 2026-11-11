"""Self-service official sync; never include credentials in results."""

import asyncio

from .. import log
from ..libraries.maimaidx_official import OfficialError
from ..libraries.maimaidx_sync_input import (
    SYNC_COMMAND, credential_images, credential_text, decode_qr, read_credential_image,
)
from .mai_base import convert_message_segment_to_chain, get_plain_message_text


async def _recall_credential(event):
    if not event.message_obj.group_id:
        return None
    if event.get_platform_name() == 'aiocqhttp':
        try:
            await event.bot.delete_msg(message_id=int(event.message_obj.message_id))
        except Exception:
            return False
        return True
    return False


async def sync_credential_handler(event, service, sgid, *, recalled=None):
    if recalled is None:
        recalled = await _recall_credential(event)
    if recalled is False:
        yield event.plain_result('消息包含登录凭据，自动撤回未成功，请及时手动撤回。')
    yield event.plain_result('正在获取官方成绩并上传已绑定的查分器，请勿同时登录机台。')
    try:
        _, statuses = await service.sync(event, sgid)
        yield event.plain_result(
            '官方成绩已获取并保存到本地。\n'
            + '\n'.join(statuses) + '\n可发送「pc50」查看常玩谱面；上传失败可发送「重传成绩」。'
        )
    except OfficialError as exc:
        yield event.plain_result(str(exc))
    except Exception as exc:
        log.warning(f'官方成绩同步失败（{type(exc).__name__}），凭据与响应未写入日志')
        yield event.plain_result('成绩同步失败，未确认上传成功；请检查 HTTPS 网络、依赖和目录权限。')


async def sync_input_handler(event, service, raw=None, *, require_pending=False):
    key = service.conversation(event)
    if require_pending and not service.waiting(event):
        return
    pending = service.pending.get(key)
    lock = service.input_lock(service.identity(event))
    if lock.locked():
        yield event.plain_result('正在处理你的成绩凭据，请勿重复发送。')
        return
    async with lock:
        images = credential_images(event)
        error = None
        content = None
        if images:
            yield event.plain_result('已收到二维码图片，正在读取，请稍候。')
            try:
                if len(images) != 1 or raw:
                    raise OfficialError('请一次只发送一个 SGID 字符串或一张玩家二维码图片，不要混合发送。')
                content = await read_credential_image(images[0])
            except OfficialError as exc:
                error = str(exc)
        recalled = await _recall_credential(event)
        if recalled is False:
            yield event.plain_result('消息可能包含登录凭据，自动撤回未成功，请及时手动撤回。')
        if error:
            yield event.plain_result(error)
            return
        try:
            if images:
                raw = await asyncio.to_thread(decode_qr, content)
            if require_pending and (not service.waiting(event) or service.pending.get(key) != pending):
                raise OfficialError('本次等待已取消或过期，请重新发送「更新b50」后再提交新的凭据。')
            async for result in sync_credential_handler(event, service, credential_text(raw or '') or raw or '', recalled=True):
                yield result
        except OfficialError as exc:
            yield event.plain_result(str(exc))


async def sync_command_handler(event, service):
    text = get_plain_message_text(event).lstrip('/')
    try:
        if text == '取消成绩更新':
            service.pending.pop(service.conversation(event), None)
            yield event.plain_result('已取消等待 SGID；已开始的同步不会重复执行。')
        elif text == '删除PC数据':
            lock = service.lock(service.identity(event))
            if lock.locked():
                raise OfficialError('正在同步成绩，请结束后再删除。')
            async with lock:
                service.pending.pop(service.conversation(event), None)
                await service.store.delete(service.identity(event))
            yield event.plain_result('已删除本地 PC 和成绩快照，不影响查分器成绩及绑定。')
        elif text == '重传成绩':
            yield event.plain_result('\n'.join(await service.retry(event)))
        else:
            match = SYNC_COMMAND.fullmatch(text)
            if match is None:
                return
            raw = (match.group(1) or '').strip()
            if raw or credential_images(event):
                await service.client.check_available()
                async for result in sync_input_handler(event, service, raw):
                    yield result
            else:
                await service.begin(event)
                yield event.plain_result(
                    '请在 180 秒内于本会话发送完整 SGID 字符串（SGWCMAID 开头）、官方玩家二维码链接，或玩家二维码图片。\n'
                    '仅同步你自己的账号，请先退出机台。SGID 属于敏感登录凭据，请勿转发给他人；'
                    '也可直接发送「更新b50 SGID」或「更新b50 官方二维码链接」，将占位内容替换为自己的实际凭据。\n'
                    '首次成功更新会将游戏账号绑定到当前 QQ；更换账号须由管理员在玩家管理中解绑。\n'
                    '发送「取消成绩更新」可取消。'
                )
    except OfficialError as exc:
        yield event.plain_result(str(exc))
    except Exception as exc:
        log.warning(f'处理官方同步命令失败（{type(exc).__name__}）')
        yield event.plain_result('操作失败，请检查本地目录权限后重试。')


async def pc_handler(event, service):
    try:
        snapshot = await service.store.load(service.identity(event))
        if snapshot is None:
            yield event.plain_result('尚无本地 PC 数据，请先发送「同步成绩」。')
            return
        if not snapshot.pc50():
            yield event.plain_result('本地没有含有效 PC 次数的普通谱面，请重新同步；缺失次数不会按 0 计算。')
            return
        from ..libraries.maimaidx_pc_image import draw_pc50
        from ..libraries.maimaidx_user import userstore

        sender = str(event.get_sender_id())
        picture = await draw_pc50(snapshot, qqid=sender if sender.isdigit() else None,
                                  theme=userstore.get(sender).theme)
        yield event.chain_result(convert_message_segment_to_chain(picture))
    except OfficialError as exc:
        yield event.plain_result(str(exc))
    except Exception as exc:
        log.warning(f'PC50 查询失败（{type(exc).__name__}）')
        yield event.plain_result('PC50 生成失败，请检查本地数据、字体及图片资源。')
