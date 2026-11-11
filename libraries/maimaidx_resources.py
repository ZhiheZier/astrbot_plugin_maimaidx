"""Resource inventory and drawing readiness, independent of optional WebUI APIs."""

from functools import wraps
from pathlib import Path

from PIL import Image, ImageFont

from .maimaidx_user import Theme


RESOURCE_NOTICE = '绘图资源尚未就绪，请管理员在 AstrBot 插件详情的「舞萌管理 → 资源」页安装或检查资源包。'
SOURCE_PACKS = (
    {'id': 'base', 'name': '基础资源包', 'version': 'CN1.55', 'kind': '完整包',
     'url': 'https://cloud.yuzuchan.moe/f/34s7/Resource%20CN1.55.7z'},
    {'id': 'rating', 'name': 'Rating 数字素材', 'version': 'CN1.56', 'kind': '增量包',
     'url': 'https://cloud.yuzuchan.moe/f/Jvhl/Resource%20CN1.56%20UPDATE.7z'},
    {'id': 'plates', 'name': '曲绘与牌子素材', 'version': '3.1.5', 'kind': '增量包',
     'url': 'https://cloud.yuzuchan.moe/f/x3f3/Resource%203.1.5%20UPDATE.7z'},
)
CORE_PICTURES = tuple(
    [f'b50_score_{name}.png' for name in ('basic', 'advanced', 'expert', 'master', 'remaster')]
    + [f'rise_score_{name}.png' for name in ('basic', 'advanced', 'expert', 'master', 'remaster')]
    + ['aurora.png', 'bg_shines.png', 'pattern.png', 'rainbow.png', 'rainbow_bottom.png']
)
CORE_FONTS = ('ResourceHanRoundedCN-Bold.ttf', 'Torus SemiBold.otf', 'FOT-NewRodin Pro EB.otf',
              'ShangguMonoSC-Regular.otf')


def scan_resources(root: Path):
    static = root / 'static'
    pic = static / 'mai/pic'

    def group(key, title, paths, *, font=False):
        missing = []
        for path in paths:
            try:
                if font:
                    ImageFont.truetype(str(path), 12)
                else:
                    with Image.open(path) as image:
                        image.verify()
            except (OSError, ValueError, Image.DecompressionBombError):
                missing.append(path.relative_to(static).as_posix())
        return {'id': key, 'name': title, 'total': len(paths), 'present': len(paths) - len(missing),
                'missing': missing, 'ready': not missing}

    groups = [group('fonts', '绘图字体', [static / 'font' / name for name in CORE_FONTS], font=True),
              group('pictures', '基础图片', [pic / name for name in CORE_PICTURES])]
    themed = []
    for name in ('title.png', 'title_lengthen.png', 'design.png'):
        preferred = pic / Theme.PRISM_PLUS.value / name
        themed.append(preferred if preferred.is_file() else pic / name)
    groups.append(group('theme', '默认主题', themed))
    tables = []
    for folder, label in (('rating_table', '定数表'), ('plate_table', '完成表')):
        directory = static / 'mai' / folder
        count = sum(1 for item in directory.glob('*.png') if item.is_file()) if directory.is_dir() else 0
        tables.append({'name': label, 'count': count, 'ready': count > 0})
    return {'ready': all(item['ready'] for item in groups), 'groups': groups, 'tables': tables,
            'path': str(static), 'cover_count': sum(1 for p in (static / 'mai/cover').glob('*.png'))}


def requires_resources(handler):
    @wraps(handler)
    async def wrapped(self, event, *args, **kwargs):
        group_id = event.message_obj.group_id
        if group_id and not self._is_group_enabled(str(group_id)):
            return
        resources = getattr(self, 'resources', None)
        if resources is not None and not resources.render_available:
            yield event.plain_result(RESOURCE_NOTICE if not resources.ready else '正在安装或重载资源，请稍后再试。')
            return
        if resources is not None:
            resources.active_draws += 1
        try:
            async for result in handler(self, event, *args, **kwargs):
                yield result
        except FileNotFoundError:
            if resources is None:
                raise
            await resources.refresh()
            yield event.plain_result(RESOURCE_NOTICE)
        finally:
            if resources is not None:
                resources.active_draws -= 1
    return wrapped


async def background_music_info(music, draw, resources=None):
    """Keep alias pushes and delayed guessing answers usable during asset installation."""
    from .. import MessageSegment

    if resources is not None and not resources.render_available:
        return MessageSegment.text(f'ID {music.song_id}: {music.song_name}')
    if resources is not None:
        resources.active_draws += 1
    try:
        return await draw(music)
    except FileNotFoundError:
        if resources is None:
            raise
        return MessageSegment.text(f'ID {music.song_id}: {music.song_name}')
    finally:
        if resources is not None:
            resources.active_draws -= 1
