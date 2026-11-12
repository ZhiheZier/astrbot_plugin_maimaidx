import base64
import json
import re
import unittest
from unittest.mock import patch

from ..libraries import maimaidx_tunnel as tunnel
from ..libraries.maimaidx_management_store import ManagementError
from . import test_management_extra as fixtures


BODY = {'action': 'generate', 'server': 'ubuntu@server.example', 'bridge': '172.18.0.1',
        'source': '172.18.0.2', 'ssh_port': '2222', 'bridge_port': '17890', 'remote_port': '17891'}


class TunnelScriptTest(unittest.TestCase):
    def test_script_contains_current_helper_and_validated_arguments(self):
        result = tunnel.generate_script(BODY)
        payload = re.search(r"FromBase64String\('([^']+)'\)", result['script'])[1]
        self.assertEqual(base64.b64decode(payload), (tunnel.ROOT / 'tools/official_ssh_tunnel.py').read_bytes())
        self.assertEqual(result['proxy'], 'http://172.18.0.1:17890')
        self.assertIn("$SshPort = '2222'", result['script'])
        self.assertIn('scp -P $SshPort', result['script'])
        self.assertIn('--ssh-port $SshPort', result['script'])
        self.assertIn('StrictHostKeyChecking=ask', result['script'])
        self.assertIn('StrictHostKeyChecking=yes', result['script'])
        self.assertIn("--remote-tool '.cache/maimai-ssh/official_ssh_tunnel.py'", result['script'])
        self.assertNotIn('Invoke-Expression', result['script'])
        self.assertNotIn(str(tunnel.ROOT), result['script'])
        self.assertTrue(result['script'].startswith('& {\n'))
        self.assertTrue(result['script'].endswith('}\n'))

    def test_rejects_injection_public_addresses_and_invalid_ports(self):
        for key, values in {
            'server': ['', '-oProxyCommand=bad', 'root@host;whoami', "root@host'", 'root@$(whoami)', ['root@host']],
            'bridge': ['0.0.0.0', '8.8.8.8', '::1', '224.0.0.1', '169.254.1.1', None],
            'source': ['0.0.0.0', '8.8.8.8', "172.18.0.2';bad", ''],
            'ssh_port': ['0', '65536', '-p 22', True],
            'bridge_port': ['80', '65536', '17890;bad', True, None],
            'remote_port': ['17890', '80', '65536', ''],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ManagementError):
                    tunnel.generate_script({**BODY, key: value})

    def test_native_linux_uses_loopback(self):
        with patch.object(tunnel.platform, 'system', return_value='Linux'), patch.object(tunnel.Path, 'exists', return_value=False):
            env = tunnel.environment()
        self.assertEqual(env['bridge'], '127.0.0.1')
        self.assertEqual(env['source'], '127.0.0.1')

    def test_container_metadata_is_whitelisted(self):
        with patch.object(tunnel.platform, 'system', return_value='Linux'), patch.object(tunnel.Path, 'exists', return_value=True), \
                patch.object(tunnel, 'network_hint', return_value=('172.18.0.1', '172.18.0.2')):
            env = tunnel.environment()
        self.assertTrue(env['container'])
        self.assertEqual(env['source'], '172.18.0.2')
        self.assertEqual(set(env), {'system', 'architecture', 'container', 'bridge', 'source', 'warning'})

    def test_missing_network_and_non_linux_require_manual_addresses(self):
        with patch.object(tunnel.platform, 'system', return_value='Windows'):
            env = tunnel.environment()
        self.assertFalse(env['bridge'])
        self.assertTrue(env['warning'])
        with patch.object(tunnel.Path, 'read_text', side_effect=OSError):
            self.assertEqual(tunnel.network_hint(), ('', ''))

    def test_linux_route_is_decoded_without_sending_packets(self):
        route = 'Iface Destination Gateway Flags\neth0 00000000 010012AC 0003\n'
        with patch.object(tunnel.Path, 'read_text', return_value=route), patch.object(tunnel.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            probe.getsockname.return_value = ('172.18.0.2', 12345)
            self.assertEqual(tunnel.network_hint(), ('172.18.0.1', '172.18.0.2'))
        probe.connect.assert_called_once_with(('172.18.0.1', 9))
        probe.send.assert_not_called()
        probe.sendto.assert_not_called()


class TunnelManagementTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.ExtraManagementTest('test_unknown_modules_and_incorrect_confirmation_do_not_change_data')
        await self.fixture.asyncSetUp()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()
        self.fixture.doCleanups()

    async def test_listing_and_script_do_not_save_config_or_start_processes(self):
        extra = self.fixture.extra
        before = json.dumps(self.fixture.plugin._raw_plugin_config, sort_keys=True)
        with patch.object(tunnel, 'environment', return_value={'system': 'Linux'}):
            listing = await extra.listing({'kind': 'tunnel', 'q': 'ignored'})
        self.assertEqual(listing['items'], [])
        self.assertEqual(listing['meta'], {'system': 'Linux'})
        result = await extra.change({'kind': 'tunnel', **BODY})
        self.assertTrue(result['script'])
        self.assertEqual(before, json.dumps(self.fixture.plugin._raw_plugin_config, sort_keys=True))
        with self.assertRaises(ManagementError):
            await extra.change({'kind': 'tunnel', 'action': 'execute', **{key: value for key, value in BODY.items() if key != 'action'}})
