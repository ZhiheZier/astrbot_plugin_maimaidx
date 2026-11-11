"""Bounded, atomic JSON commits shared by dashboard management operations."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .maimaidx_resource_archive import ResourceError


local_alias_lock = asyncio.Lock()


class ManagementError(ResourceError):
    pass


def read_json(path: Path, default):
    if not path.exists():
        return default
    if path.stat().st_size > 8 * 1024 ** 2:
        raise ManagementError('管理数据文件过大，请先检查文件；原文件未修改。')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ManagementError('管理数据文件无法读取，请检查文件；原文件未修改。') from None


def revision(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def check_revision(expected, actual):
    if not isinstance(expected, str) or expected != actual:
        raise ManagementError('数据已变化，请刷新列表后重新操作。')


def write_json(path: Path, data):
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(content) > 8 * 1024 ** 2:
        raise ManagementError('管理数据超出大小限制，未保存。')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.manage-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def identifier(value, label='ID'):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 0 < int(value) < 10 ** 20:
        raise ManagementError(f'{label}必须为有效的正整数。')
    return str(int(value))


def text(value, label, maximum=150, *, empty=False):
    if not isinstance(value, str) or len(value.strip()) > maximum or any(ord(char) < 32 for char in value):
        raise ManagementError(f'{label}格式不正确或超过{maximum}个字符。')
    value = value.strip()
    if not value and not empty:
        raise ManagementError(f'{label}不能为空。')
    return value
