"""One background resource job per plugin, with bounded public HTTPS downloads."""

import asyncio
import copy
import hashlib
import ipaddress
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiofiles
import aiohttp
from aiohttp.resolver import DefaultResolver

from .maimaidx_resource_archive import (
    MAX_ARCHIVE, ResourceCancelled, ResourceError, ResourceRollbackError,
    cancelled, confined, extract_assets, install_assets,
)
from .maimaidx_resources import SOURCE_PACKS, scan_resources
from .. import __version__


ACTIVE_PHASES = {'uploading', 'downloading', 'extracting', 'validating', 'installing', 'reloading', 'rebuilding', 'scanning'}
MAX_UPLOAD = 1024 ** 3


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await super().resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(item['host']).is_global for item in addresses):
            raise ResourceError('下载源解析到了非公网地址，已拒绝连接。')
        return addresses


def validate_download_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ResourceError('下载地址不符合安全要求。')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ResourceError('下载源不能使用内网或本机地址。')


class ResourceManager:
    def __init__(self, root: Path, on_installed=None, rebuild=None, prepare=None, on_phase=None):
        self.root = root
        self.on_installed = on_installed
        self.rebuild = rebuild
        self.prepare = prepare
        self.on_phase = on_phase
        self.inventory = {'ready': False, 'groups': [], 'tables': [], 'path': str(root / 'static')}
        self.job = {'phase': 'idle', 'message': '暂无任务', 'current': 0, 'total': 0, 'events': []}
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._task = None
        self.active_draws = 0

    @property
    def ready(self):
        return self.inventory['ready']

    @property
    def busy(self):
        return self.job['phase'] in ACTIVE_PHASES

    @property
    def render_available(self):
        return self.ready and self.job['phase'] not in {'installing', 'reloading', 'rebuilding'}

    def progress(self, phase, message, current=0, total=0):
        with self._guard:
            previous = self.job.get('phase')
            self.job.update(phase=phase, message=message, current=current, total=total)
            if phase != previous or phase not in ACTIVE_PHASES:
                self.job['events'] = (self.job.get('events', []) + [{'at': int(time.time()), 'message': message}])[-40:]
        if phase != previous and self.on_phase is not None:
            try:
                self.on_phase(phase)
            except Exception:
                pass

    async def refresh(self):
        self.inventory = await asyncio.to_thread(scan_resources, self.root)
        return self.inventory

    def status(self):
        with self._guard:
            job = copy.deepcopy(self.job)
        job['cancellable'] = job['phase'] in ACTIVE_PHASES - {'installing', 'reloading', 'rebuilding'}
        return {'version': __version__, 'inventory': self.inventory, 'job': job, 'busy': job['phase'] in ACTIVE_PHASES,
                'sources': [{key: value for key, value in source.items() if key != 'url'} for source in SOURCE_PACKS],
                'max_upload': MAX_UPLOAD}

    def _begin(self, phase, title, source_id=None):
        if self.busy:
            raise ResourceError('已有资源任务正在进行，请等待完成或取消后再试。')
        self._stop = threading.Event()
        self.job = {'phase': phase, 'title': title, 'message': '任务已创建', 'current': 0, 'total': 0,
                    'source_id': source_id, 'events': [], 'started_at': int(time.time())}

    def _workspace(self):
        parent = self.root / 'resource_work'
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ResourceError('资源工作目录不能是符号链接或联接。')
        return Path(tempfile.mkdtemp(prefix='job-', dir=parent))

    def _cleanup(self, work):
        parent = (self.root / 'resource_work').resolve()
        if work.parent.resolve() != parent or not work.name.startswith('job-'):
            raise ResourceError('拒绝清理资源工作目录以外的路径。')
        confined(work, parent)
        shutil.rmtree(work)

    async def _thread(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._stop.set()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _download(self, source, archive):
        url = source['url']
        timeout = aiohttp.ClientTimeout(total=3600, connect=20, sock_read=60)
        connector = aiohttp.TCPConnector(resolver=PublicResolver())
        digest, downloaded = hashlib.sha256(), 0
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, trust_env=False, auto_decompress=False) as http:
            for _ in range(6):
                cancelled(self._stop)
                validate_download_url(url)
                async with http.get(url, allow_redirects=False, headers={'Accept-Encoding': 'identity'}) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get('Location')
                        if not location:
                            raise ResourceError('资源下载跳转缺少地址。')
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise ResourceError(f'下载源返回 HTTP {response.status}，请稍后重试或改用上传资源包。')
                    total = response.content_length or 0
                    if total > MAX_ARCHIVE:
                        raise ResourceError('资源包超过下载大小限制。')
                    self.progress('downloading', '正在下载资源包', 0, total)
                    async with aiofiles.open(archive, 'xb') as output:
                        async for chunk in response.content.iter_chunked(262144):
                            cancelled(self._stop)
                            downloaded += len(chunk)
                            if downloaded > MAX_ARCHIVE:
                                raise ResourceError('资源包超过下载大小限制。')
                            await output.write(chunk)
                            digest.update(chunk)
                            self.progress('downloading', '正在下载资源包', downloaded, total)
                    if not downloaded:
                        raise ResourceError('下载内容为空，请更换来源或上传资源包。')
                    self.job['sha256'] = digest.hexdigest()
                    return
        raise ResourceError('资源下载跳转次数过多，请改用上传资源包。')

    async def _wait_for_draws(self):
        deadline = asyncio.get_running_loop().time() + 120
        while self.active_draws:
            if asyncio.get_running_loop().time() > deadline:
                raise ResourceError('仍有绘图任务未结束，请稍后重试。')
            await asyncio.sleep(0.1)

    async def _run(self, work, source=None):
        preserve = False
        cache_released = False
        try:
            archive = work / 'archive.bin'
            if source is not None:
                await self._download(source, archive)
            stage = work / 'stage'
            stage.mkdir()
            self.progress('extracting', '正在读取资源包')
            files = await self._thread(extract_assets, archive, stage, self._stop, self.progress)
            cancelled(self._stop)
            self.progress('installing', '正在等待绘图结束并安装资源')
            await self._wait_for_draws()
            cancelled(self._stop)
            if self.prepare is not None:
                cache_released = True
                await self.prepare()
            await self._thread(install_assets, stage, self.root / 'static', work / 'backup', files, self.progress)
            self.progress('reloading', '正在重新检测和加载资源')
            await self.refresh()
            if self.on_installed is not None:
                await self.on_installed()
            cache_released = False
            message = f'已安装 {len(files)} 个资源文件。' + ('绘图资源已就绪。' if self.ready else '仍缺少部分基础资源，请继续安装完整包或补充字体。')
            self.progress('completed', message, len(files), len(files))
        except ResourceRollbackError as exc:
            preserve = True
            self.inventory['ready'] = False
            self.progress('failed', str(exc))
        except (ResourceCancelled, asyncio.CancelledError):
            self.progress('cancelled', '任务已取消。')
        except ResourceError as exc:
            self.progress('failed', str(exc))
        except Exception as exc:
            from .. import log
            log.warning(f'资源任务失败（{type(exc).__name__}）')
            self.progress('failed', '资源处理失败，请检查下载源、资源包和磁盘权限后重试。')
        finally:
            if cache_released and not preserve:
                final_phase, final_message = self.job['phase'], self.job['message']
                self.progress('reloading', '正在恢复资源缓存')
                try:
                    await self.refresh()
                    if self.on_installed is not None:
                        await self.on_installed()
                except Exception:
                    self.inventory['ready'] = False
                self.progress(final_phase, final_message)
            if not preserve:
                try:
                    await asyncio.to_thread(self._cleanup, work)
                except (OSError, ResourceError):
                    from .. import log
                    log.warning('资源任务临时文件清理失败，请检查持久化目录权限。')

    def start_download(self, source_id):
        source = next((source for source in SOURCE_PACKS if source['id'] == source_id), None)
        if source is None:
            raise ResourceError('请选择列表中的资源包，不支持任意下载地址。')
        self._begin('downloading', source['name'], source_id)
        try:
            work = self._workspace()
        except Exception:
            self.progress('failed', '无法创建资源工作目录，请检查目录权限。')
            raise
        self._task = asyncio.create_task(self._run(work, source))

    async def upload(self, upload):
        try:
            self._begin('uploading', '上传资源包')
        except ResourceError:
            await upload.close()
            raise
        work = None
        try:
            work = self._workspace()
            if not upload.filename or Path(upload.filename).suffix.lower() not in ('.zip', '.7z'):
                raise ResourceError('请选择 ZIP 或 7z 格式资源包。')
            size, digest = 0, hashlib.sha256()
            async with aiofiles.open(work / 'archive.bin', 'xb') as output:
                while chunk := await upload.read(262144):
                    cancelled(self._stop)
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise ResourceError('上传资源包超过1GB限制，请使用在线下载或手动安装。')
                    await output.write(chunk)
                    digest.update(chunk)
                    self.progress('uploading', '正在接收资源包', size, 0)
            self.job['sha256'] = digest.hexdigest()
            self._task = asyncio.create_task(self._run(work))
        except BaseException as exc:
            if isinstance(exc, (ResourceCancelled, asyncio.CancelledError)):
                self.progress('cancelled', '上传已取消，原有资源未改动。')
            else:
                self.progress('failed', '上传未完成或文件不符合要求，请重新选择资源包。')
            if work is not None:
                await asyncio.to_thread(self._cleanup, work)
            raise
        finally:
            await upload.close()

    def cancel(self):
        if self.job['phase'] in {'installing', 'reloading', 'rebuilding'}:
            raise ResourceError('正在安装或重载资源，暂不能取消，请等待任务完成。')
        if not self.busy:
            raise ResourceError('当前没有可取消的任务。')
        self._stop.set()
        self.job['message'] = '正在取消，请稍候。'

    async def rescan(self):
        if self.busy:
            raise ResourceError('请等待当前资源任务完成后再检测。')
        self._begin('reloading', '检查资源')
        try:
            await self._wait_for_draws()
            await self.refresh()
            if self.on_installed is not None:
                await self.on_installed()
            self.progress('completed', '资源检查完成。')
        except BaseException:
            self.progress('failed', '检查或加载资源失败，请检查文件和目录权限。')
            raise

    def start_rebuild(self):
        if not self.ready:
            raise ResourceError('请先补齐基础图片和字体。')
        if self.rebuild is None:
            raise ResourceError('当前环境不支持生成表格，请使用原有更新表格指令。')
        self._begin('rebuilding', '生成定数表和完成表')
        self._task = asyncio.create_task(self._run_rebuild())

    async def _run_rebuild(self):
        try:
            self.progress('rebuilding', '正在等待绘图结束并生成表格')
            await self._wait_for_draws()
            await self.rebuild()
            await self.refresh()
            self.progress('completed', '定数表和完成表已生成。')
        except Exception as exc:
            from .. import log
            log.warning(f'资源表格生成失败（{type(exc).__name__}）')
            self.progress('failed', '表格生成失败，请检查曲库数据和图片资源。')

    async def shutdown(self):
        self._stop.set()
        if self._task is not None and not self._task.done():
            await self._task
