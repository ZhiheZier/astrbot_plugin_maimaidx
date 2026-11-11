"""Dashboard-authenticated resource endpoints; no independent HTTP listener."""

from astrbot.api.web import error_response, json_response, request
import time

from .maimaidx_resource_archive import ResourceError
from .maimaidx_nearcade import NearcadeError


class ResourceWeb:
    def __init__(self, context, manager, management=None):
        self.manager = manager
        self.management = management
        for endpoint, handler, method in (
            ('status', self.status, 'GET'), ('download', self.download, 'POST'),
            ('upload', self.upload, 'POST'), ('cancel', self.cancel, 'POST'),
            ('rescan', self.rescan, 'POST'), ('rebuild', self.rebuild, 'POST'),
        ):
            context.register_web_api(f'/astrbot_plugin_maimaidx/resources/{endpoint}', handler, [method], '舞萌资源管理')
        if management is not None:
            for endpoint, handler, method in (
                ('status', self.manage_status, 'GET'), ('groups', self.groups, 'GET'),
                ('group', self.change_group, 'POST'), ('arcades', self.arcades, 'GET'),
                ('arcade', self.change_arcade, 'POST'), ('songs', self.songs, 'GET'),
                ('song-alias', self.change_song_alias, 'POST'),
                ('extra', self.extra_list, 'GET'), ('extra', self.extra_change, 'POST'),
                ('detail', self.extra_detail, 'GET'),
                ('activity', self.activity, 'GET'),
            ):
                context.register_web_api(f'/astrbot_plugin_maimaidx/manage/{endpoint}', handler, [method], '舞萌管理')

    def _record(self, audit, status, started):
        store = getattr(getattr(self.management, 'plugin', None), 'activity', None)
        if store is not None and audit:
            try:
                store.record(audit.get('scope', 'web'), audit['feature'], audit['label'], status,
                             duration_ms=(time.monotonic() - started) * 1000)
            except Exception:
                pass

    async def _execute(self, action, audit=None):
        if not request.username:
            return error_response('请先登录 AstrBot 管理面板。', status_code=401)
        started = time.monotonic()
        try:
            result = await action()
            response = json_response(result)
            self._record(audit, 'submitted' if audit and audit.get('submitted') else 'handled', started)
            return response
        except (ResourceError, NearcadeError) as exc:
            self._record(audit, 'error', started)
            return error_response(str(exc), status_code=409)
        except Exception as exc:
            self._record(audit, 'error', started)
            from .. import log
            log.warning(f'资源管理接口失败（{type(exc).__name__}）')
            return error_response('操作失败，请检查插件依赖、文件和目录权限。', status_code=500)

    async def status(self):
        async def action():
            return self.manager.status()
        return await self._execute(action)

    async def download(self):
        async def action():
            body = await request.json(default={})
            if not isinstance(body, dict) or not isinstance(body.get('source_id'), str):
                raise ResourceError('请选择资源包。')
            self.manager.start_download(body['source_id'])
            return self.manager.status()
        return await self._execute(action, {'scope': 'resource', 'feature': 'download', 'label': '提交资源下载', 'submitted': True})

    async def upload(self):
        async def action():
            files = await request.files()
            upload = files.get('file')
            if upload is None:
                raise ResourceError('请选择资源包文件。')
            await self.manager.upload(upload)
            return self.manager.status()
        return await self._execute(action, {'scope': 'resource', 'feature': 'upload', 'label': '提交资源安装', 'submitted': True})

    async def cancel(self):
        async def action():
            self.manager.cancel()
            return self.manager.status()
        return await self._execute(action, {'scope': 'resource', 'feature': 'cancel', 'label': '取消资源任务'})

    async def rescan(self):
        async def action():
            await self.manager.rescan()
            return self.manager.status()
        return await self._execute(action, {'scope': 'resource', 'feature': 'rescan', 'label': '重新检测资源'})

    async def rebuild(self):
        async def action():
            self.manager.start_rebuild()
            return self.manager.status()
        return await self._execute(action, {'scope': 'resource', 'feature': 'rebuild', 'label': '提交表格生成', 'submitted': True})

    async def _manage(self, method, *, post=False, asynchronous=False):
        async def action():
            if self.management is None:
                raise ResourceError('管理模块尚未就绪。')
            values = await request.json(default={}) if post else dict(request.query.items())
            if not isinstance(values, dict):
                raise ResourceError('请求格式错误。')
            result = getattr(self.management, method)(values)
            return await result if asynchronous else result
        labels = {'change_group': '修改群功能', 'change_arcade': '修改机厅与订阅', 'change_song_alias': '维护歌曲别名'}
        return await self._execute(action, {'feature': method, 'label': labels[method]} if post and method in labels else None)

    async def manage_status(self):
        async def action():
            return self.management.status()
        return await self._execute(action)

    async def groups(self):
        return await self._manage('groups')

    async def change_group(self):
        return await self._manage('change_group', post=True)

    async def arcades(self):
        return await self._manage('arcades', asynchronous=True)

    async def change_arcade(self):
        return await self._manage('change_arcade', post=True, asynchronous=True)

    async def songs(self):
        return await self._manage('songs')

    async def change_song_alias(self):
        return await self._manage('change_song_alias', post=True, asynchronous=True)

    async def _extra(self, method, *, post=False):
        audit = {'feature': 'extra_change', 'label': '管理操作'} if post else None
        async def action():
            if self.management is None:
                raise ResourceError('管理模块尚未就绪。')
            values = await request.json(default={}) if post else dict(request.query.items())
            if not isinstance(values, dict):
                raise ResourceError('请求格式错误。')
            if post:
                labels = {'mappings': '维护机台映射', 'players': '维护玩家设置', 'rankings': '维护排行榜名单',
                          'pc': '维护PC快照', 'notices': '手动发送更新通知', 'cache': '维护曲库缓存', 'limits': '维护同步额度'}
                kind = values.get('kind')
                if isinstance(kind, str) and kind in labels:
                    audit.update(feature=f'manage_{kind}', label=labels[kind])
            return await getattr(self.management.extra, method)(values)
        return await self._execute(action, audit)

    async def activity(self):
        async def action():
            store = getattr(self.management.plugin, 'activity', None)
            if store is None:
                from .maimaidx_management import page
                return {**page([], dict(request.query.items())), 'persistent': False}
            await store.load()
            return store.listing(dict(request.query.items()))
        return await self._execute(action)

    async def extra_list(self):
        return await self._extra('listing')

    async def extra_change(self):
        return await self._extra('change', post=True)

    async def extra_detail(self):
        return await self._extra('detail')
