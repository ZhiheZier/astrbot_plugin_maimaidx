import base64
from io import BytesIO
from typing import List, Optional, Set, Tuple, Union
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .. import SHANGGUMONO, Path, coverdir, log

# 记录在线下载失败（如 CDN 无此封面）的 ID，避免重复请求
_missing_covers: Set[int] = set()


class DrawText:

    def __init__(self, image: ImageDraw.ImageDraw, font: Path) -> None:
        self._img = image
        self._font = str(font)

    def get_box(self, text: str, size: int) -> Tuple[float, float, float, float]:
        return ImageFont.truetype(self._font, size).getbbox(text)

    def draw(
        self,
        pos_x: int,
        pos_y: int,
        size: int,
        text: Union[str, int, float],
        color: Tuple[int, int, int, int] = (255, 255, 255, 255),
        anchor: str = 'lt',
        stroke_width: int = 0,
        stroke_fill: Tuple[int, int, int, int] = (0, 0, 0, 0),
        multiline: bool = False
    ) -> None:
        font = ImageFont.truetype(self._font, size)
        if multiline:
            self._img.multiline_text(
                (pos_x, pos_y), 
                str(text), 
                color, 
                font, 
                anchor, 
                stroke_width=stroke_width, 
                stroke_fill=stroke_fill
            )
        else:
            self._img.text(
                (pos_x, pos_y), 
                str(text), 
                color, 
                font, 
                anchor, 
                stroke_width=stroke_width, 
                stroke_fill=stroke_fill
            )


def draw_text_with_font_fallback(
    image: ImageDraw.ImageDraw,
    pos_x: int,
    pos_y: int,
    size: int,
    text: Union[str, int, float],
    color: Tuple[int, int, int, int],
    primary_font: Path,
    fallback_font: Path,
    anchor: str = 'mm',
    stroke_width: int = 0,
    stroke_fill: Tuple[int, int, int, int] = (0, 0, 0, 0),
) -> None:
    primary = ImageFont.truetype(str(primary_font), size)
    fallback = ImageFont.truetype(str(fallback_font), size)
    parts: List[Tuple[str, ImageFont.FreeTypeFont]] = []

    for char in str(text):
        font = primary if char.isascii() else fallback
        if parts and parts[-1][1] is font:
            parts[-1] = (parts[-1][0] + char, font)
        else:
            parts.append((char, font))

    total_width = sum(image.textlength(part, font=font) for part, font in parts)
    if anchor[0] == 'm':
        x = pos_x - total_width / 2
        part_anchor = 'lm'
    elif anchor[0] == 'r':
        x = pos_x - total_width
        part_anchor = 'lm'
    else:
        x = pos_x
        part_anchor = 'la' if len(anchor) > 1 and anchor[1] == 'a' else 'lm'

    for part, font in parts:
        image.text(
            (x, pos_y),
            part,
            color,
            font=font,
            anchor=part_anchor,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        x += image.textlength(part, font=font)


def tricolor_gradient(
    width: int, 
    height: int, 
    color1: Tuple[int, int, int] = (124, 129, 255), 
    color2: Tuple[int, int, int] = (193, 247, 225), 
    color3: Tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """绘制渐变色"""
    array = np.zeros((height, width, 3), dtype=np.uint8)
    
    for y in range(height):
        if y < height * 0.4:
            ratio = y / (height * 0.4)
            color = (1 - ratio) * np.array(color1) + ratio * np.array(color2)
        else:
            ratio = (y - height * 0.4) / (height * 0.6)
            color = (1 - ratio) * np.array(color2) + ratio * np.array(color3)
        array[y, :] = np.clip(color, 0, 255)
    
    image = Image.fromarray(array).convert('RGBA')
    return image


def hex_to_rgb(hex_str: str) -> Tuple[int, ...]:
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))


def tricolor_gradient_prism_plus(width: int, height: int) -> Image.Image:
    """
    垂直绘制 PRiSM PLUS 渐变背景
    """
    colors_list = [
        (0.0, hex_to_rgb("#ffffff")),
        (0.14, hex_to_rgb("#ffffff")),
        (0.24, hex_to_rgb("#ffd5cf")),
        (0.46, hex_to_rgb("#ffd5cf")),
        (0.56, hex_to_rgb("#ffc5d5")),
        (0.67, hex_to_rgb("#eaabff")),
        (0.85, hex_to_rgb("#72bcfe")),
        (0.95, hex_to_rgb("#65f2df")),
        (1.0, hex_to_rgb("#65f2df")),
    ]
    line = Image.new("RGBA", (1, height))
    for y in range(height):
        t = 1.0 - (y / (height - 1)) if height > 1 else 0
        for i in range(len(colors_list) - 1):
            p1, c1 = colors_list[i]
            p2, c2 = colors_list[i + 1]
            if p1 <= t <= p2:
                rel_t = (t - p1) / (p2 - p1)
                rgb = tuple(int(c1[j] + (c2[j] - c1[j]) * rel_t) for j in range(3))
                line.putpixel((0, y), rgb)
                break
    return line.resize((width, height), resample=Image.Resampling.BICUBIC)


def generate_frosted_card(
    im: Image.Image,
    box: Tuple[int, int, int, int],
    alpha: float = 0.4,
) -> Image.Image:
    """
    绘制毛玻璃卡片效果
    """
    if alpha < 0 or alpha > 1:
        raise ValueError
    roi = im.crop(box)
    roi_w, roi_h = roi.size
    frosted = roi.filter(ImageFilter.GaussianBlur(4))
    white_layer = Image.new("RGBA", (roi_w, roi_h), (255, 255, 255, int(255 * alpha)))
    card = Image.alpha_composite(frosted, white_layer)
    mask = Image.new("L", (roi_w, roi_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, roi_w, roi_h), radius=25, fill=255)
    shadow = Image.new("RGBA", (roi_w + 10, roi_h + 10), (0, 0, 0, 0))
    draw_shadow = ImageDraw.Draw(shadow)
    draw_shadow.rounded_rectangle((8, 8, 8 + roi_w, 8 + roi_h), radius=25, fill=(0, 0, 0, 50))
    shadow_layer = shadow.filter(ImageFilter.GaussianBlur(3))
    im.paste(shadow_layer, (box[0] - 5, box[1] - 5), shadow_layer)
    im.paste(card, (box[0], box[1]), mask)
    return im


def rounded_corners(
    image: Image.Image,
    radius: int, 
    corners: Tuple[bool, bool, bool, bool] = (False, False, False, False)
) -> Image.Image:
    """
    绘制圆角
    
    Params:
        `image`: `PIL.Image.Image`
        `radius`: 圆角半径
        `corners`: 四个角是否绘制圆角，分别是左上、右上、右下、左下
    Returns:
        `PIL.Image.Image`
    """
    mask = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, image.size[0], image.size[1]), radius, fill=255, corners=corners)

    new_im = ImageOps.fit(image, mask.size)
    new_im.putalpha(mask)

    return new_im


def _download_cover(music_id: int) -> Optional[Path]:
    """当本地缺少封面且开启在线素材时，从水鱼封面 CDN 下载并缓存。

    Params:
        `music_id`: 谱面 ID（会按 `% 10000` 取标准封面编号）
    Returns:
        下载成功返回本地缓存路径，否则返回 `None`
    """
    # 延迟导入以避免导入期循环依赖
    from .maimaidx_api_data import maiApi

    if not getattr(maiApi.config, 'assets_online', True):
        return None

    # DX / 宴会場 ID 与标准封面共用同一张图（编号后四位）
    cover_id = int(music_id) % 10000
    if cover_id in _missing_covers:
        return None

    target = coverdir / f'{cover_id}.png'
    if target.exists():
        return target

    # 水鱼封面 CDN 使用未补零的 ID
    url = f'{maiApi.MaiCover}/{cover_id}.png'
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=10) as resp:
            if getattr(resp, 'status', 200) != 200:
                _missing_covers.add(cover_id)
                return None
            content = resp.read()
        if not content:
            _missing_covers.add(cover_id)
            return None
        coverdir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target
    except Exception as e:
        _missing_covers.add(cover_id)
        log.warning(f'在线下载封面失败（id={music_id} → {cover_id}）：{e}')
        return None


def music_picture(music_id: Union[int, str]) -> Path:
    """
    获取谱面图片路径。

    对齐上游：封面文件按 `song_id % 10000` 取本地文件；
    均不存在时回退 `0.png`（占位图）。
    
    Params:
        `music_id`: 谱面 ID
    Returns:
        `Path`
    """
    music_id = int(music_id)
    # 优先精确 ID（兼容本地额外存放的 DX 专用封面）
    if (_path := coverdir / f'{music_id}.png').exists():
        return _path

    cover_id = music_id % 10000
    if (_path := coverdir / f'{cover_id}.png').exists():
        return _path

    # 本地无封面时，按需在线下载并缓存（受 assets_online 控制）
    if (_online := _download_cover(music_id)) is not None:
        return _online

    fallback = coverdir / '0.png'
    if fallback.exists():
        return fallback
    # 极端情况：连占位图都没有时生成一张纯色图，避免 FileNotFoundError
    coverdir.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (200, 200), (80, 80, 80, 255)).save(fallback)
    return fallback


def text_to_image(text: str) -> Image.Image:
    font = ImageFont.truetype(str(SHANGGUMONO), 24)
    padding = 10
    margin = 4
    lines = text.strip().split('\n')
    max_width = 0
    b = 0
    for line in lines:
        l, t, r, b = font.getbbox(line)
        max_width = max(max_width, r)
    wa = max_width + padding * 2
    ha = b * len(lines) + margin * (len(lines) - 1) + padding * 2
    im = Image.new('RGB', (wa, ha), color=(255, 255, 255))
    draw = ImageDraw.Draw(im)
    for index, line in enumerate(lines):
        draw.text((padding, padding + index * (margin + b)), line, font=font, fill=(0, 0, 0))
    return im


def image_to_base64(img: Image.Image, format='PNG') -> str:
    output_buffer = BytesIO()
    img.save(output_buffer, format)
    byte_data = output_buffer.getvalue()
    base64_str = base64.b64encode(byte_data).decode()
    return 'base64://' + base64_str
