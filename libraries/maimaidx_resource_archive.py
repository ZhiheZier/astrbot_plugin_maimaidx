"""Extract only passive image/font assets into staging, then install with rollback."""

import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageFont


MAX_ARCHIVE = 3 * 1024 ** 3
MAX_EXTRACTED = 8 * 1024 ** 3
MAX_FILE = 128 * 1024 ** 2
MAX_FILES = 50000
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
FONT_SUFFIXES = {'.ttf', '.otf', '.ttc'}
PIC_FOLDERS = {'pic', 'cover', 'plate', 'plate_version', 'shougou', 'shogou', 'plate_table', 'rating_table'}


class ResourceError(Exception):
    pass


class ResourceCancelled(ResourceError):
    pass


class ResourceRollbackError(ResourceError):
    pass


def cancelled(stop):
    if stop.is_set():
        raise ResourceCancelled('任务已取消，原有资源未改动。')


def asset_path(name):
    normalized = name.replace('\\', '/')
    if (normalized.startswith('/') or ':' in normalized or '\x00' in normalized
            or any(ord(char) < 32 for char in normalized)):
        raise ResourceError('资源包包含不安全路径，已拒绝安装。')
    parts = normalized.rstrip('/').split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.'))
           or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
           for part in parts):
        raise ResourceError('资源包包含不安全路径，已拒绝安装。')
    for marker in ('static', 'mai', 'font', *sorted(PIC_FOLDERS)):
        if marker in parts:
            parts = parts[parts.index(marker):]
            break
    else:
        if len(parts) > 1 and re.fullmatch(r'Resource(?:\s.*)?|资源包(?:.*)?', parts[0], re.I):
            parts = parts[1:]
    if parts[0] == 'static':
        parts = parts[1:]
    if not parts:
        return None
    suffix = PurePosixPath(parts[-1]).suffix.lower()
    if parts[0] in PIC_FOLDERS:
        parts.insert(0, 'mai')
    if len(parts) == 1:
        parts = ['font', *parts] if suffix in FONT_SUFFIXES else ['mai', 'pic', *parts]
    if parts[0] == 'font' and len(parts) >= 2 and suffix in FONT_SUFFIXES:
        return Path(*parts)
    if (parts[0] == 'mai' and len(parts) >= 3 and parts[1] in PIC_FOLDERS
            and suffix in IMAGE_SUFFIXES):
        return Path(*parts)
    return None


def confined(path, root):
    root = root.resolve()
    if not path.resolve().is_relative_to(root):
        raise ResourceError('资源目标路径越界，已停止安装。')
    current = path
    while True:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ResourceError('资源目录包含符号链接或联接，已停止安装。')
        # Windows short paths and their resolved long names can denote the same directory.
        if current.resolve() == root:
            return path
        if current == current.parent:
            raise ResourceError('无法确认资源目录边界，已停止安装。')
        current = current.parent


def extract_assets(archive, stage, stop, progress):
    records = []
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    raise ResourceError('资源包包含链接或特殊文件，已拒绝安装。')
                if entry.flag_bits & 1:
                    raise ResourceError('不支持加密资源包。')
                records.append((entry.filename, entry.file_size, entry.is_dir()))
            targets = _targets(records, stage, stop)
            for index, (name, relative) in enumerate(targets.items(), 1):
                cancelled(stop)
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with bundle.open(name) as source, destination.open('xb') as output:
                    while chunk := source.read(65536):
                        cancelled(stop)
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise ResourceError('单个资源文件过大，已停止解压。')
                        output.write(chunk)
                progress('extracting', f'正在解压 {index}/{len(targets)}', index, len(targets))
    else:
        try:
            import py7zr
        except ImportError:
            raise ResourceError('缺少7z解压依赖，请安装插件 requirements.txt，或上传 ZIP 格式资源包。') from None
        if not py7zr.is_7zfile(archive):
            raise ResourceError('文件不是有效的 ZIP 或 7z 资源包。')
        with py7zr.SevenZipFile(archive, mode='r', max_extract_size=MAX_EXTRACTED, mp=False) as bundle:
            if bundle.needs_password():
                raise ResourceError('不支持加密资源包。')
            for entry in bundle.list():
                if entry.is_symlink or not (entry.is_file or entry.is_directory):
                    raise ResourceError('资源包包含链接或特殊文件，已拒绝安装。')
                records.append((entry.filename, entry.uncompressed, entry.is_directory))
            targets = _targets(records, stage, stop)
            from py7zr.io import Py7zIO, WriterFactory

            class Writer(Py7zIO):
                def __init__(self, filename):
                    if filename not in targets:
                        raise ResourceError('资源包解压路径不匹配。')
                    self.path = stage / targets[filename]
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.stream = self.path.open('xb+')
                    self.length = 0

                def write(self, data):
                    cancelled(stop)
                    if self.stream.tell() + len(data) > MAX_FILE:
                        raise ResourceError('单个资源文件过大，已停止解压。')
                    written = self.stream.write(data)
                    self.length = max(self.length, self.stream.tell())
                    return written

                def read(self, size=None):
                    return self.stream.read(-1 if size is None else size)

                def seek(self, offset, whence=0):
                    return self.stream.seek(offset, whence)

                def flush(self):
                    self.stream.flush()

                def close(self):
                    self.stream.close()

                def size(self):
                    return self.length

            class Factory(WriterFactory):
                def __init__(self):
                    self.writers = []

                def create(self, filename):
                    writer = Writer(filename)
                    self.writers.append(writer)
                    progress('extracting', f'正在解压 {len(self.writers)}/{len(targets)}', len(self.writers), len(targets))
                    return writer

            factory = Factory()
            try:
                bundle.extract(targets=list(targets), factory=factory)
            finally:
                for writer in factory.writers:
                    writer.stream.close()
    cancelled(stop)
    for index, relative in enumerate(targets.values(), 1):
        cancelled(stop)
        path = stage / relative
        try:
            if path.suffix.lower() in FONT_SUFFIXES:
                ImageFont.truetype(str(path), 12)
            else:
                with Image.open(path) as image:
                    if image.width * image.height > 128_000_000:
                        raise ValueError('oversized image')
                    image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ResourceError('资源包内存在损坏或不支持的图片、字体，原有资源未改动。') from None
        progress('validating', f'正在检查资源 {index}/{len(targets)}', index, len(targets))
    return list(targets.values())


def _targets(records, stage, stop):
    if len(records) > MAX_FILES or sum(size for _, size, _ in records) > MAX_EXTRACTED:
        raise ResourceError('资源包解压体积或文件数量超出限制。')
    targets, seen = {}, set()
    for name, size, directory in records:
        cancelled(stop)
        relative = asset_path(name)
        if size < 0 or (not directory and size > MAX_FILE):
            raise ResourceError('资源包包含过大的文件。')
        if directory or relative is None:
            continue
        key = relative.as_posix().casefold()
        if key in seen:
            raise ResourceError('资源包存在重复或大小写冲突的目标路径。')
        seen.add(key)
        targets[name] = relative
    if not targets:
        raise ResourceError('未找到可安装的图片或字体；配置文件和用户数据不会导入。')
    if shutil.disk_usage(stage).free < sum(size for _, size, _ in records) + 128 * 1024 ** 2:
        raise ResourceError('可用磁盘空间不足，请清理后重试。')
    return targets


def install_assets(stage, static, backup, files, progress):
    static.mkdir(parents=True, exist_ok=True)
    if static.is_symlink() or (hasattr(static, 'is_junction') and static.is_junction()):
        raise ResourceError('资源根目录不能是符号链接或联接。')
    journal = []
    try:
        for index, relative in enumerate(files, 1):
            target = confined(static / relative, static)
            original = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            if existed and not target.is_file():
                raise ResourceError('资源目标不是普通文件，已停止安装。')
            if existed:
                original.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, original)
            journal.append((target, original, existed))
            os.replace(stage / relative, target)
            progress('installing', f'正在安装 {index}/{len(files)}', index, len(files))
    except BaseException:
        failures = []
        for target, original, existed in reversed(journal):
            try:
                if target.exists():
                    target.unlink()
                if existed:
                    os.replace(original, target)
            except OSError:
                failures.append(target)
        if failures:
            raise ResourceRollbackError('安装失败且回滚未完全完成，备份已保留，请管理员检查资源工作目录。') from None
        raise
