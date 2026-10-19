"""Read grouped plugin settings and migrate the former flat configuration once."""

import json
from copy import deepcopy
from functools import lru_cache

from astrbot.api import logger

from .. import Root


@lru_cache(maxsize=1)
def _config_groups():
    with (Root / '_conf_schema.json').open(encoding='utf-8') as stream:
        schema = json.load(stream)
    return {key: node['items'] for key, node in schema.items() if node['type'] == 'object'}


def load_plugin_config(config):
    groups = _config_groups()
    staged = deepcopy(dict(config))
    if staged.get('config_layout_version') != 1:
        for group, fields in groups.items():
            values = staged.get(group)
            values = dict(values) if isinstance(values, dict) else {}
            for key, node in fields.items():
                default = node['default']
                current = values.get(key, default)
                legacy = staged.get(key)
                if legacy is not None and current == default:
                    current = legacy
                values[key] = deepcopy(current)
                # Hidden root fields keep AstrBot from deleting old values before migration.
                # Clear them after moving so credentials have only one saved copy.
                staged[key] = None
            staged[group] = values
        staged['config_layout_version'] = 1
        original = dict(config)
        config.clear()
        config.update(staged)
        save = getattr(config, 'save_config', None)
        if callable(save):
            try:
                save()
                logger.info('舞萌插件配置已迁移至分类布局，原有配置已保留')
            except Exception:
                config.clear()
                config.update(original)
                logger.warning('舞萌插件分类配置保存失败，本次使用已迁移的配置；下次加载将重试，请检查配置文件权限')
    result = {}
    for group, fields in groups.items():
        values = staged.get(group)
        values = values if isinstance(values, dict) else {}
        for key, node in fields.items():
            result[key] = deepcopy(values.get(key, node['default']))
    return result
