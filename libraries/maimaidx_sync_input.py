"""Bounded, in-memory credential image decoding; never follow decoded links."""

import asyncio
import base64
import io
import re
import warnings
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import aiohttp
import astrbot.api.message_components as Comp
from PIL import Image

from .maimaidx_official import OfficialError, SGID_PATTERN


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
SYNC_COMMAND = re.compile(r'^/?(?:同步成绩|更新成绩|导|更新(?i:b50)|更新乐曲数据)(?:\s+(.*))?$', re.DOTALL)


def credential_images(event):
    return [part for part in (event.message_obj.message or []) if isinstance(part, Comp.Image)]


def credential_text(value):
    value = value.strip()
    if SGID_PATTERN.fullmatch(value):
        return value
    # Only interpret the official QR payload; no request is made to this URL.
    try:
        url = urlsplit(value)
        if (url.scheme in ('http', 'https') and url.netloc == 'wq.wahlap.net'
                and re.fullmatch(r'/qrcode/req/MAID[0-9]{12}[A-Fa-f0-9]{64}\.html', url.path)):
            return 'SGWC' + url.path.removeprefix('/qrcode/req/').removesuffix('.html')
    except ValueError:
        pass
    return None


def decode_qr(data):
    try:
        import zxingcpp
    except ImportError:
        raise OfficialError('缺少二维码识别依赖，请管理员安装 requirements.txt；也可直接发送 SGID 字符串。') from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise OfficialError('二维码图片像素过大，请裁剪为二维码区域后重发。')
                with image.convert('L') as gray:
                    results = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.QRCode)
        values = {value for item in results if (value := credential_text(item.text))}
    except OfficialError:
        raise
    except Exception:
        raise OfficialError('二维码图片无法识别，请发送清晰的静态二维码图片或完整 SGID 字符串。') from None
    if len(values) > 1:
        raise OfficialError('检测到多个不同的玩家二维码，请只发送你自己的一个二维码。')
    if not values:
        raise OfficialError('未识别到有效的玩家二维码，请发送清晰的二维码原图或完整 SGID 字符串。')
    return values.pop()


def _read_local(path):
    with Path(path).open('rb') as stream:
        return stream.read(MAX_IMAGE_BYTES + 1)


async def read_credential_image(component):
    # References come from platform Image components, never from QR contents or plain text.
    ref = str(component.url or component.file or '')
    try:
        if ref.startswith('base64://'):
            encoded = ref.removeprefix('base64://')
            if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                raise OfficialError('二维码图片过大，请压缩至 12 MB 内后重发。')
            data = await asyncio.to_thread(base64.b64decode, encoded, validate=True)
        elif ref.startswith(('https://', 'http://')):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False) as http:
                async with http.get(ref, allow_redirects=False) as response:
                    if response.status != 200:
                        raise OfficialError('二维码图片下载失败，请重新发送原图或 SGID 字符串。')
                    if response.content_length and response.content_length > MAX_IMAGE_BYTES:
                        raise OfficialError('二维码图片过大，请压缩至 12 MB 内后重发。')
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_IMAGE_BYTES:
                            raise OfficialError('二维码图片过大，请压缩至 12 MB 内后重发。')
                    data = bytes(chunks)
        else:
            if ref.startswith('file://'):
                parsed = urlsplit(ref)
                if parsed.netloc not in ('', 'localhost'):
                    raise OfficialError('不支持该二维码图片来源，请重新发送图片。')
                ref = url2pathname(parsed.path)
            data = await asyncio.to_thread(_read_local, ref)
        if len(data) > MAX_IMAGE_BYTES:
            raise OfficialError('二维码图片过大，请压缩至 12 MB 内后重发。')
        return data
    except OfficialError:
        raise
    except Exception:
        raise OfficialError('读取二维码图片失败，请重新发送图片或完整 SGID 字符串。') from None
