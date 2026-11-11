"""Validated local aliases with immediate publication to the merged in-memory list."""

from .maimaidx_management_store import ManagementError, read_json, write_json


def load_local_aliases():
    from .. import local_alias_file

    data = read_json(local_alias_file, {})
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or not key.isascii() or not key.isdigit()
        or not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values)
        for key, values in data.items()
    ):
        raise ManagementError('本地歌曲别名文件格式错误，原文件未修改。')
    return {key: list(dict.fromkeys(value.lower() for value in values)) for key, values in data.items()}


async def change_local_alias(sid, value, *, adding, reject_conflicts=False):
    from .. import alias_file, local_alias_file, lxns_alias_file
    from .maimaidx_model import Alias
    from .maimaidx_music import AliasList, mai

    song = mai.total_list.by_id(sid)
    if song is None:
        raise ManagementError('歌曲不存在，请先刷新曲库。')
    local = load_local_aliases()
    previous = local.get(sid, [])
    if (value in previous) == adding:
        raise ManagementError('本地已存在该别名。' if adding else '本地不存在该别名，未修改其他来源。')
    current = getattr(mai, 'total_alias_list', AliasList())
    matches = current.by_alias(value)
    if adding and (any(item.SongID == int(sid) for item in matches) or reject_conflicts and matches):
        names = '、'.join(f'{item.Name}（{item.SongID}）' for item in matches[:5])
        raise ManagementError(f'别名已被使用：{names}。')
    values = [*previous, value] if adding else [item for item in previous if item != value]
    if values:
        local[sid] = values
    else:
        local.pop(sid, None)
    original = current.by_id(sid)
    remote = [item for item in (original[0].Alias if original else []) if item not in previous]
    yuzu = read_json(alias_file, [])
    lxns = read_json(lxns_alias_file, {'aliases': []})
    if not isinstance(yuzu, list) or not isinstance(lxns, dict) or not isinstance(lxns.get('aliases'), list):
        raise ManagementError('外部别名缓存格式错误，请先刷新别名数据。')
    yuzu_ids = set()
    for item in yuzu:
        if not isinstance(item, dict):
            raise ManagementError('外部别名缓存格式错误。')
        raw_id = item.get('SongID', item.get('song_id'))
        if str(raw_id).isdigit():
            yuzu_ids.add(int(raw_id))
        names = item.get('Alias', item.get('alias', [])) or []
        if str(raw_id) == sid:
            if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                raise ManagementError('外部别名缓存格式错误。')
            remote.extend(names)
    for item in lxns['aliases']:
        if not isinstance(item, dict) or type(item.get('song_id')) is not int or not isinstance(item.get('aliases'), list):
            raise ManagementError('落雪别名缓存格式错误。')
        if (item['song_id'] == int(sid) and int(sid) in yuzu_ids
                or item['song_id'] < 10000 and item['song_id'] + 10000 == int(sid) and int(sid) in yuzu_ids):
            if any(not isinstance(name, str) for name in item['aliases']):
                raise ManagementError('落雪别名缓存格式错误。')
            remote.extend(item['aliases'])
    combined = list(dict.fromkeys((*remote, *values)))
    updated = AliasList([item for item in current if item.SongID != int(sid)])
    if combined:
        updated.append(Alias(SongID=int(sid), Name=song.song_name, Alias=combined))
    # Publish only after a complete atomic save; there is no await between save and publication.
    write_json(local_alias_file, local)
    mai.total_alias_list = updated
