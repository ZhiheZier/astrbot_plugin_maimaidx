import json
import tempfile
import unittest
from pathlib import Path

from ..main import MaimaiDXPlugin


class ArcadeSwitchPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)
        self.plugin.arcade_switch_file = (
            Path(self.temp_dir.name) / 'enabled_arcade_groups.json'
        )
        self.plugin.arcade_enabled_groups = set()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load_enabled_groups(self):
        self.plugin.arcade_enabled_groups = {'200', '100'}
        self.plugin._save_arcade_enabled_groups()

        saved = json.loads(
            self.plugin.arcade_switch_file.read_text(encoding='utf-8')
        )
        self.assertEqual(saved, {'enabled_groups': ['100', '200']})

        self.plugin.arcade_enabled_groups = set()
        self.plugin._load_arcade_enabled_groups()
        self.assertTrue(self.plugin._is_arcade_enabled('100'))
        self.assertFalse(self.plugin._is_arcade_enabled('300'))

    def test_load_normalizes_numeric_group_ids(self):
        self.plugin.arcade_switch_file.write_text(
            json.dumps({'enabled_groups': [12345]}),
            encoding='utf-8',
        )
        self.plugin._load_arcade_enabled_groups()
        self.assertTrue(self.plugin._is_arcade_enabled('12345'))

    def test_missing_file_defaults_to_disabled(self):
        self.plugin._load_arcade_enabled_groups()
        self.assertFalse(self.plugin._is_arcade_enabled('12345'))


if __name__ == '__main__':
    unittest.main()
