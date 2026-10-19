import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from astrbot.core.config.astrbot_config import AstrBotConfig

from .. import Root
from ..libraries.maimaidx_nearcade import NearcadeClient
from ..libraries.maimaidx_plugin_config import load_plugin_config


class SavedConfig(dict):
    def __init__(self, values):
        super().__init__(values)
        self.save_config = Mock()


class PluginConfigTest(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((Root / '_conf_schema.json').read_text(encoding='utf-8'))
        self.groups = {key: node['items'] for key, node in self.schema.items() if node['type'] == 'object'}
        self.fields = {key: node for fields in self.groups.values() for key, node in fields.items()}
        self.defaults = {key: deepcopy(node['default']) for key, node in self.fields.items()}

    def grouped(self):
        return {group: {key: deepcopy(node['default']) for key, node in fields.items()}
                for group, fields in self.groups.items()}

    def legacy(self):
        values = deepcopy(self.defaults)
        for key, node in self.fields.items():
            value = values[key]
            if isinstance(value, bool):
                values[key] = not value
            elif isinstance(value, str):
                values[key] = 'test-value-' + key
            elif isinstance(value, list):
                values[key] = ['123=456']
            elif node['type'] == 'int':
                values[key] = 7
            else:
                values[key] = 2.5
        values['arcade_data_source'] = 'nearcade'
        return values

    def test_all_existing_settings_appear_once_in_seven_visible_object_groups(self):
        self.assertEqual(set(self.groups), {'general', 'divingfish', 'lxns', 'alias', 'images', 'arcade', 'location'})
        self.assertEqual(len(self.fields), 24)
        self.assertEqual(sum(len(fields) for fields in self.groups.values()), len(self.fields))
        for key, node in self.schema.items():
            if key in self.groups:
                self.assertFalse(node.get('invisible', False))
            else:
                self.assertTrue(node['invisible'])
                if key != 'config_layout_version':
                    self.assertIn(key, self.fields)
                    self.assertIsNone(node['default'])

    def test_new_installation_keeps_original_defaults(self):
        config = SavedConfig({})
        self.assertEqual(load_plugin_config(config), self.defaults)
        self.assertEqual(config['config_layout_version'], 1)
        config.save_config.assert_called_once_with()
        self.assertFalse(config['arcade']['nearcade_upload_enabled'])
        self.assertFalse(config['location']['nearcade_location_enabled'])

    def test_flat_configuration_migrates_all_values_and_clears_old_copies(self):
        legacy = self.legacy()
        config = SavedConfig(legacy)
        self.assertEqual(load_plugin_config(config), legacy)
        for group, fields in self.groups.items():
            for key in fields:
                self.assertEqual(config[group][key], legacy[key])
                self.assertIsNone(config[key])
        config.save_config.assert_called_once_with()

    def test_framework_inserted_group_defaults_do_not_shadow_existing_flat_values(self):
        config = SavedConfig({**self.legacy(), **self.grouped(), 'config_layout_version': 0})
        self.assertEqual(load_plugin_config(config), self.legacy())

    def test_explicit_nondefault_grouped_value_wins_over_legacy_value(self):
        config = SavedConfig({**self.legacy(), **self.grouped()})
        config['divingfish']['df_client_secret'] = 'new-grouped-secret'
        config['arcade']['nearcade_shop_bindings'] = ['123=789']
        config['general']['bot_name'] = 'new-name'
        result = load_plugin_config(config)
        self.assertEqual(result['df_client_secret'], 'new-grouped-secret')
        self.assertEqual(result['nearcade_shop_bindings'], ['123=789'])
        self.assertEqual(result['bot_name'], 'new-name')

    def test_second_load_does_not_resave_or_restore_stale_flat_values(self):
        config = SavedConfig(self.legacy())
        load_plugin_config(config)
        config['arcade']['nearcade_upload_enabled'] = False
        config['divingfish']['df_client_secret'] = ''
        config['arcade']['nearcade_shop_bindings'] = []
        config['df_client_secret'] = 'stale-legacy-secret'
        config.save_config.reset_mock()
        result = load_plugin_config(config)
        self.assertFalse(result['nearcade_upload_enabled'])
        self.assertEqual(result['df_client_secret'], '')
        self.assertEqual(result['nearcade_shop_bindings'], [])
        config.save_config.assert_not_called()

    def test_save_failure_leaves_original_available_and_retries_next_load(self):
        original = self.legacy()
        config = SavedConfig(original)
        config.save_config.side_effect = OSError('test-secret must not reach logs')
        with patch('astrbot.api.logger.warning') as warning:
            self.assertEqual(load_plugin_config(config), original)
        self.assertEqual(config, original)
        self.assertNotIn('test-secret', str(warning.call_args))
        config.save_config.side_effect = None
        self.assertEqual(load_plugin_config(config), original)
        self.assertEqual(config['config_layout_version'], 1)

    def test_framework_upgrade_preserves_flat_values_before_plugin_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plugin.json'
            legacy = self.legacy()
            path.write_text(json.dumps(legacy), encoding='utf-8')
            with patch('astrbot.core.config.astrbot_config.logger'):
                config = AstrBotConfig(str(path), schema=self.schema)
            self.assertEqual(config['df_client_secret'], legacy['df_client_secret'])
            self.assertEqual(load_plugin_config(config), legacy)
            saved = json.loads(path.read_text(encoding='utf-8-sig'))
            self.assertEqual(saved['divingfish']['df_client_secret'], legacy['df_client_secret'])
            self.assertIsNone(saved['df_client_secret'])
            self.assertEqual(saved['arcade']['nearcade_shop_bindings'], legacy['nearcade_shop_bindings'])
            self.assertEqual(load_plugin_config(AstrBotConfig(str(path), schema=self.schema)), legacy)

    def test_framework_new_installation_loads_grouped_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AstrBotConfig(str(Path(directory) / 'plugin.json'), schema=self.schema)
            self.assertEqual(load_plugin_config(config), self.defaults)

    def test_dashboard_validation_keeps_grouped_values_after_migration(self):
        from astrbot.dashboard.routes.config import validate_config

        config = SavedConfig(self.legacy())
        expected = load_plugin_config(config)
        errors, saved = validate_config(deepcopy(dict(config)), self.schema, is_core=False)
        self.assertEqual(errors, [])
        self.assertEqual(load_plugin_config(saved), expected)
        saved['arcade']['nearcade_upload_enabled'] = False
        saved['divingfish']['df_client_secret'] = ''
        self.assertFalse(load_plugin_config(saved)['nearcade_upload_enabled'])
        self.assertEqual(load_plugin_config(saved)['df_client_secret'], '')

    def test_unknown_keys_do_not_leak_into_runtime_configuration(self):
        config = {'general': {'bot_name': 'name', 'ignored': 'unused'}, 'unrelated': 'unused'}
        result = load_plugin_config(config)
        self.assertEqual(result['bot_name'], 'name')
        self.assertEqual(set(result), set(self.fields))

    def test_optional_field_conditions_reference_sibling_settings(self):
        for group, fields in self.groups.items():
            for key, node in fields.items():
                for dependency in node.get('condition', {}):
                    self.assertIn(dependency, fields, (group, key, dependency))
        self.assertEqual(self.fields['nearcade_radius_km']['condition'], {'nearcade_location_enabled': True})
        self.assertEqual(self.fields['nearcade_shop_bindings']['condition'], {'arcade_data_source': 'local'})

    def test_grouped_settings_reach_existing_nearcade_client_unchanged(self):
        config = self.grouped()
        config['arcade'].update(arcade_data_source='nearcade', nearcade_upload_enabled=True,
                                nearcade_api_token='test-token', nearcade_shop_bindings=['123=456'])
        config['location'].update(nearcade_location_enabled=True, nearcade_radius_km=7.5, nearcade_result_limit=8)
        client = NearcadeClient(load_plugin_config(config))
        self.assertTrue(client.uses_cloud_catalog)
        self.assertTrue(client.upload_enabled)
        self.assertTrue(client.location_enabled)
        self.assertEqual(client.radius, 7.5)
        self.assertEqual(client.limit, 8)
        self.assertEqual(client.config['nearcade_api_token'], 'test-token')
