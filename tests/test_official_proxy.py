import asyncio
import importlib.util
import ssl
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientHttpProxyError

from .. import Root
from ..libraries import maimaidx_official as official
from .test_official_https import Response
from .test_official_sync import sgid


class ProxyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.proxy = 'http://127.0.0.1:17890'
        self.client = official.OfficialClient(Path('unused'), {
            'official_sync_enabled': True, 'official_http_proxy': self.proxy,
        })

    async def test_proxy_is_explicit_and_keeps_tls_verification(self):
        tls = await self.client.check_available()
        async with self.client.http_session(tls) as http:
            self.assertFalse(http.trust_env)
            self.assertEqual(http.connector._ssl.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(http.connector._ssl.check_hostname)
        self.assertEqual(self.client._proxy_options(), {'proxy': self.proxy})
        direct = official.OfficialClient(Path('unused'), {})
        self.assertEqual(direct._proxy_options(), {})

    async def test_proxy_resolves_login_hosts_not_local_dns(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock(side_effect=AssertionError('local DNS'))):
            self.assertEqual(await self.client.login_server_order(), [0, 1])

    async def test_proxy_is_used_for_authorization_health_and_title_requests(self):
        http = Mock()
        http.post.return_value = Response({'errorID': 0, 'userID': 123, 'token': 'secret'})
        await self.client._resolve_session(http, sgid())
        self.assertEqual(http.post.call_args.kwargs['proxy'], self.proxy)
        http.post.return_value = Response(body=b'alive')
        await self.client.probe_login(http, 0)
        self.assertEqual(http.post.call_args.kwargs['proxy'], self.proxy)
        http.post.return_value = Response(body=official.encode_packet({'gameSetting': {}}))
        await self.client._post(http, official.TITLE_URL, 'GetGameSettingApi', 0, {})
        self.assertEqual(http.post.call_args.kwargs['proxy'], self.proxy)
        for call in http.post.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertNotIn('ssl', call.kwargs)

    async def test_invalid_proxy_never_echoes_credentials(self):
        for proxy in ('socks5://user:secret@localhost:1234', 'http://user:secret@:1234',
                      'http://localhost:0', 'http://localhost:99999',
                      'http://localhost:1234/private-secret', 'http://localhost:1234?q=secret',
                      'http://localhost:1234#secret', 'http://local\nhost:1234'):
            self.client.config['official_http_proxy'] = proxy
            with self.assertRaises(official.OfficialError) as caught:
                await self.client.check_available()
            self.assertNotIn('secret', str(caught.exception))

    async def test_proxy_rejection_does_not_fall_back_or_expose_error(self):
        http = Mock()
        http.post.side_effect = ClientHttpProxyError(Mock(), (), status=502, message='private-auth')
        with self.assertRaises(official.OfficialError) as caught:
            await self.client._resolve_session(http, sgid())
        self.assertNotIn('private-auth', str(caught.exception))
        http.post.assert_called_once()
        self.assertEqual(http.post.call_args.kwargs['proxy'], self.proxy)


class TunnelToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('official_ssh_tunnel_test',
                                                     Root / 'tools/official_ssh_tunnel.py')
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_connect_only_allows_official_https_targets(self):
        for host, port in self.tool.ALLOWED_TARGETS:
            self.assertEqual(self.tool.connect_target(f'CONNECT {host}:{port} HTTP/1.1'.encode()),
                             (host, port))
        for line in (b'GET / HTTP/1.1', b'CONNECT localhost:22 HTTP/1.1',
                     b'CONNECT ai.sys-allnet.cn:80 HTTP/1.1',
                     b'CONNECT user:secret@ai.sys-allnet.cn:443 HTTP/1.1',
                     b'CONNECT maimai-gm.wahlap.com:42081/path HTTP/1.1',
                     b'CONNECT maimai-gm.wahlap.com.attacker.test:42081 HTTP/1.1'):
            with self.assertRaises(ValueError):
                self.tool.connect_target(line)

    def test_bridge_rejects_public_or_wildcard_bindings(self):
        self.assertEqual(self.tool.private_address('172.18.0.1'), '172.18.0.1')
        self.assertEqual(self.tool.private_address('127.0.0.1'), '127.0.0.1')
        for value in ('0.0.0.0', '8.8.8.8', '::'):
            with self.assertRaises(self.tool.argparse.ArgumentTypeError):
                self.tool.private_address(value)

    def test_ssh_does_not_expose_listener_or_disable_host_key_checks(self):
        args = SimpleNamespace(remote_tool='/path with spaces/tool.py', bridge='172.18.0.1',
                               bridge_port=17890, remote_port=17891, allow_source=['172.18.0.2'],
                               identity=Path('id_ed25519'), server='user@example.test')
        command = self.tool.ssh_arguments(args, 12345)
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertIn('ExitOnForwardFailure=yes', command)
        self.assertIn('127.0.0.1:17891:127.0.0.1:12345', command)
        self.assertIn("'/path with spaces/tool.py'", command[-1])
        self.assertIn('--watch-stdin', command[-1])
        self.assertEqual(command[command.index('-p') + 1], '22')
        args.ssh_port = 2222
        custom = self.tool.ssh_arguments(args, 12345)
        self.assertEqual(custom[custom.index('-p') + 1], '2222')

    def test_bridge_only_accepts_configured_container_source(self):
        server = object.__new__(self.tool.RestrictedServer)
        server.sources = {'172.18.0.2'}
        self.assertTrue(server.verify_request(None, ('172.18.0.2', 1)))
        self.assertFalse(server.verify_request(None, ('172.18.0.3', 1)))


class DiagnosticSchemaTest(unittest.TestCase):
    def test_schema_diagnostic_only_reports_field_names_and_counts(self):
        from ..libraries import maimaidx_sync as sync
        from .test_official_sync import data, detail

        spec = importlib.util.spec_from_file_location('official_schema_test', Root / 'tools/setup_official_runtime.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        missing = detail()
        del missing['comboStatus']
        result = tool.validation_summary(data([detail(), detail(playCount=8), detail(level=11), missing]), sync)
        self.assertEqual(result, {'records': 4, 'schema_errors': {
            'conflicting_duplicate': 1, 'level_index.less_than_equal': 1, 'missing.comboStatus': 1,
        }})
        self.assertNotIn('12345', str(result))
        self.assertNotIn('11103', str(result))
