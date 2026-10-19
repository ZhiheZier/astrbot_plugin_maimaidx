"""Persistent, per-group release announcements for OneBot adapters."""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

from astrbot.api import logger
from packaging.version import InvalidVersion, Version


CHANGELOG_URL = 'https://github.com/ZhiheZier/astrbot_plugin_maimaidx/blob/master/CHANGELOG.md'


def update_message(changelog: str, previous: str | None, current: str) -> str:
    lower, upper = Version(previous) if previous is not None else None, Version(current)
    sections = []
    selected = False
    category = None
    category_started = False
    version_started = False
    version_label = ''
    for line in changelog.splitlines():
        if match := re.fullmatch(r'##\s+v?(\S+)\s*', line):
            try:
                release = Version(match[1])
                selected = release == upper if lower is None else lower < release <= upper
            except InvalidVersion:
                selected = False
            category = '更新内容'
            category_started = False
            version_started = False
            version_label = f'【v{match[1].removeprefix("v")}】'
        elif selected:
            if heading := re.fullmatch(r'###\s+(.+?)\s*', line):
                title = heading[1].strip('`* \t')
                category = None if title == '其他' else title
                category_started = False
            elif category is not None and line.strip():
                if not version_started:
                    if sections:
                        sections.append('')
                    sections.append(version_label)
                    version_started = True
                if not category_started:
                    sections.extend(('', f'━━ {category} ━━', ''))
                    category_started = True
                if heading := re.fullmatch(r'#{4,6}\s+(.+?)\s*', line):
                    sections.extend(('', f'━━ {heading[1].replace("`", "")} ━━', ''))
                else:
                    sections.append(line.replace('`', ''))
    body = '\n'.join(sections) or (
        '本次未列出可广播的更新内容，详情请查看完整更新日志。' if changelog.strip()
        else '本次更新详情请查看完整更新日志。'
    )
    header = (f'舞萌DX插件当前版本：v{current}\n\n' if previous is None
              else f'舞萌DX插件已更新：v{previous} → v{current}\n\n')
    footer = (
        f'\n\n完整更新日志：{CHANGELOG_URL}\n\n功能使用说明请通过 /maihelp 查看。'
        '\n功能建议或问题反馈，请发送邮件至 contact@zhihezier.com，或提交 Issue：'
        'https://github.com/ZhiheZier/astrbot_plugin_maimaidx/issues'
    )
    limit = 1800 - len(header) - len(footer)
    if len(body) > limit:
        body = body[:limit - 20].rsplit('\n', 1)[0] + '\n（更多内容见完整更新日志）'
    return header + body + footer


class UpdateNotifier:
    def __init__(self, path: Path, version: str, changelog: Path, *, send_delay: float = 2):
        self.path = path
        self.version = str(Version(version))
        self.changelog = changelog
        self.send_delay = send_delay
        self.state = None
        self._lock = asyncio.Lock()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             prefix='version-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.state, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _prepare(self) -> bool:
        if not self.path.exists():
            self.state = {'version': self.version, 'pending': {
                'version': self.version, 'delivered': [], 'initial': True,
            }}
            self._save()
            logger.info(f'已建立舞萌更新通知版本记录 v{self.version}，准备首次广播')
            return True
        state = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or not isinstance(state.get('version'), str):
            raise ValueError('version.json 缺少有效的 version 字段')
        recorded = Version(state['version'])
        current = Version(self.version)
        if current < recorded:
            return False
        pending = state.get('pending')
        if pending is not None:
            if not isinstance(pending, dict) or not isinstance(pending.get('version'), str):
                raise ValueError('version.json 的 pending 字段格式无效')
            if Version(pending['version']) > Version(self.version):
                return False
            delivered = pending.get('delivered', [])
            if not isinstance(delivered, list) or not all(isinstance(key, str) for key in delivered):
                raise ValueError('version.json 的 delivered 字段格式无效')
            if not isinstance(pending.get('initial', False), bool):
                raise ValueError('version.json 的 initial 字段格式无效')
        if current == recorded and (pending is None or Version(pending['version']) != current):
            return False
        if pending is None or Version(pending['version']) != Version(self.version):
            pending = {'version': self.version, 'delivered': []}
        self.state = {'version': str(recorded), 'pending': pending}
        self._save()
        return True

    async def send_pending(self, context, is_group_enabled) -> bool:
        """Return true only after all currently available adapters were enumerated and sent."""
        async with self._lock:
            pending = self.state['pending']
            if pending is None:
                return True
            manager = context.platform_manager
            platforms = [platform for platform in manager.platform_insts
                         if platform.meta().name == 'aiocqhttp']
            if not platforms:
                return False
            try:
                changelog = await asyncio.to_thread(self.changelog.read_text, encoding='utf-8')
            except OSError:
                changelog = ''
            previous = None if pending.get('initial', False) else self.state['version']
            message = update_message(changelog, previous, self.version)
            delivered = set(pending.get('delivered', []))
            expected = {str(config['id']) for config in getattr(manager, 'platforms_config', [])
                        if config.get('enable') and config.get('type') == 'aiocqhttp'}
            loaded = {str(platform.meta().id) for platform in platforms}
            complete = not (expected - loaded)
            for platform in platforms:
                try:
                    client = platform.get_client()
                    groups = await asyncio.wait_for(client.get_group_list(), timeout=30)
                    if not isinstance(groups, list):
                        raise ValueError('群列表不是列表')
                    group_ids = sorted({str(group['group_id']) for group in groups}, key=int)
                except Exception as exc:
                    logger.warning(f'舞萌更新通知等待平台 {platform.meta().id} 就绪: {exc}')
                    complete = False
                    continue
                for group_id in group_ids:
                    key = f'{platform.meta().id}:{group_id}'
                    if key in delivered or not is_group_enabled(group_id):
                        continue
                    try:
                        # Explicit text segments cannot interpret changelog content as CQ codes.
                        await asyncio.wait_for(client.send_group_msg(
                            group_id=int(group_id), message=[{'type': 'text', 'data': {'text': message}}],
                        ), timeout=30)
                    except Exception as exc:
                        logger.warning(f'舞萌更新通知发送到群 {group_id} 失败，将稍后重试: {exc}')
                        complete = False
                        continue
                    delivered.add(key)
                    pending['delivered'] = sorted(delivered)
                    await asyncio.to_thread(self._save)
                    await asyncio.sleep(self.send_delay)
            if complete:
                self.state = {'version': self.version, 'pending': None}
                await asyncio.to_thread(self._save)
                logger.info(f'舞萌 v{self.version} 更新通知发送完成')
            return complete

    async def run(self, context, is_group_enabled):
        try:
            if not await asyncio.to_thread(self._prepare):
                return
            while True:
                if await self.send_pending(context, is_group_enabled):
                    return
                await asyncio.sleep(60)
        except Exception as exc:
            logger.error(f'舞萌更新通知已停止，保留版本记录以便排查: {exc}')
