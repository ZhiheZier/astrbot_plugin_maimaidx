import asyncio
import hashlib
import importlib.util
import io
import json
import ssl
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientConnectionError, ClientRequest
from yarl import URL

from .. import Root
from ..libraries import maimaidx_official as official
from .test_official_sync import sgid


class Response:
    def __init__(self, payload=None, *, body=None, status=200):
        self.status = status
        self.body = json.dumps(payload).encode() if body is None else body
        self.content = SimpleNamespace(iter_chunked=self.chunks)

    async def chunks(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class HTTPSTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = official.OfficialClient(Path('unused'), {'official_sync_enabled': True})

    async def resolve(self, http, *, servers=(0, 1)):
        with patch.object(self.client, 'login_server_order', AsyncMock(return_value=list(servers))):
            return await self.client._resolve_session(http, sgid())

    def test_tls_requires_hostname_validity_and_root_not_just_intermediate(self):
        tls = official.official_tls_context()
        self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(tls.check_hostname)
        self.assertFalse(tls.verify_flags & getattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN', 0))
        self.assertGreaterEqual(tls.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertGreater(len(tls.get_ca_certs()), 20)
        pem = (Root / 'libraries/certs/globalsign-r46-alphassl-2025.pem').read_text()
        self.assertEqual(hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest(), official._ISSUER_SHA256)
        self.assertNotIn('PRIVATE KEY', pem)

    def test_changed_or_missing_issuer_is_rejected_without_modifying_global_context(self):
        original = ssl._create_default_https_context
        pem = (Root / 'libraries/certs/globalsign-r46-alphassl-2025.pem').read_text()
        for value in (pem.replace('MIIFfj', 'MIIFfk', 1), 'invalid-cert-private-data'):
            with patch.object(Path, 'read_text', return_value=value):
                with self.assertRaises(official.OfficialError) as caught:
                    official.official_tls_context.__wrapped__()
            self.assertNotIn('private-data', str(caught.exception))
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError()):
            with self.assertRaises(official.OfficialError):
                official.official_tls_context.__wrapped__()
        self.assertIs(ssl._create_default_https_context, original)

    async def test_available_without_native_files_or_architecture_checks(self):
        self.assertIs(await self.client.check_available(), official.official_tls_context())
        self.assertFalse(hasattr(self.client, '_resolve'))
        self.assertEqual(self.client.keychip_id, official.DEFAULT_KEYCHIP_ID)
        self.assertEqual(self.client.client_id, official.DEFAULT_CLIENT_ID)
        for value in ('BAD', 'x\nprivate', 'A' * 20):
            client = official.OfficialClient(Path('unused'), {'official_sync_enabled': True,
                                                              'official_client_id': value})
            with self.assertRaises(official.OfficialError):
                await client.check_available()

    async def test_configured_short_or_full_id_uses_correct_wire_fields(self):
        for value, short in [('TEST0000001', 'TEST0000001'), ('A63E-01E11890000', 'A63E01E1189')]:
            client = official.OfficialClient(Path('unused'), {'official_sync_enabled': True,
                                                              'official_client_id': value})
            await client.check_available()
            self.assertEqual(client.keychip_id, value)
            self.assertEqual(client.client_id, short)
            self.assertEqual(client._login_payload(sgid())['chipID'], value)

    def test_signed_payload_uses_full_chip_id_and_only_qr_payload(self):
        credential = sgid()
        fixed = datetime(2026, 11, 11, 0, 30, tzinfo=timezone(timedelta(hours=8)))
        with patch.object(official, 'datetime') as clock:
            clock.now.return_value = fixed
            payload = self.client._login_payload(credential)
        self.assertEqual(payload['timestamp'], '261111003000')
        expected = hashlib.sha256((official.DEFAULT_KEYCHIP_ID + '261111003000'
                                   + 'XcW5FW4cPArBXEk4vzKz3CIrMuA5EVVW').encode()).hexdigest().upper()
        self.assertEqual(payload['key'], expected)
        self.assertEqual(payload['qrCode'], credential[20:])
        self.assertEqual(len(payload['qrCode']), 64)
        self.assertEqual((payload['openGameID'], payload['titlekey']), ('MAID', 'SDGB'))
        self.assertNotIn(credential, json.dumps(payload))

    async def test_success_uses_https_no_content_type_or_redirect_and_safe_session_repr(self):
        http = Mock()
        http.post.return_value = Response({'errorID': 0, 'userID': 123, 'token': 'private-session'})
        result = await self.resolve(http)
        self.assertEqual((result.user_id, result.token), (123, 'private-session'))
        args, kwargs = http.post.call_args
        self.assertEqual(args[0], 'https://ai.sys-allnet.cn/wc_aime/api/get_data')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertIn('Content-Type', kwargs['skip_auto_headers'])
        self.assertNotIn('Content-Type', kwargs['headers'])
        self.assertEqual(kwargs['headers']['User-Agent'], 'WC_AIME_LIB')
        self.assertNotIn('ssl', kwargs)
        self.assertNotIn('123', repr(result))
        self.assertNotIn('private-session', repr(result))

        wire = ClientRequest('POST', URL(args[0]), data=kwargs['data'], headers=kwargs['headers'],
                             skip_auto_headers=kwargs['skip_auto_headers'],
                             loop=asyncio.get_running_loop())
        self.assertNotIn('Content-Type', wire.headers)
        self.assertNotIn('Accept-Encoding', wire.headers)
        self.assertEqual(int(wire.headers['Content-Length']), len(kwargs['data']))

    async def test_connector_preserves_verified_tls_and_ignores_proxy_environment(self):
        tls = await self.client.check_available()
        async with self.client.http_session(tls) as http:
            self.assertIs(http.connector._ssl, tls)
            self.assertFalse(http.trust_env)
            self.assertFalse(http.auto_decompress)

    async def test_dns_skips_unresolved_host_and_reports_both_failed_without_details(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock(side_effect=[[('address',)], OSError('private')])):
            self.assertEqual(await self.client.login_server_order(), [0])
        with patch.object(loop, 'getaddrinfo', AsyncMock(side_effect=[OSError(), [('address',)]])):
            self.assertEqual(await self.client.login_server_order(), [1])
        with patch.object(loop, 'getaddrinfo', AsyncMock(side_effect=OSError('private'))):
            with self.assertRaisesRegex(official.OfficialError, 'DNS') as caught:
                await self.client.login_server_order()
        self.assertNotIn('private', str(caught.exception))

    async def test_connection_failure_retries_only_other_https_host(self):
        http = Mock()
        http.post.side_effect = [ClientConnectionError('private-network-detail'),
                                 Response({'errorID': 0, 'userID': 123, 'token': 'secret'})]
        await self.resolve(http)
        self.assertEqual([call.args[0] for call in http.post.call_args_list],
                         ['https://ai.sys-allnet.cn/wc_aime/api/get_data',
                          'https://ai.sys-all.cn/wc_aime/api/get_data'])
        http.post.reset_mock(side_effect=True)
        http.post.side_effect = asyncio.TimeoutError('private')
        with self.assertRaises(official.OfficialError) as caught:
            await self.resolve(http)
        self.assertEqual(http.post.call_count, 2)
        self.assertNotIn('private', str(caught.exception))

    async def test_certificate_failure_is_terminal_no_plaintext_fallback(self):
        http = Mock()
        http.post.side_effect = ssl.SSLCertVerificationError(1, 'private-tls-detail')
        with self.assertRaisesRegex(official.OfficialError, '不会回退') as caught:
            await self.resolve(http)
        http.post.assert_called_once()
        self.assertNotIn('private-tls-detail', str(caught.exception))

    async def test_business_error_codes_are_not_native_codes_or_retried(self):
        for code in (1, 2, 4, 5, 6, 57):
            http = Mock()
            http.post.return_value = Response({'errorID': code, 'token': 'private-response', 'userID': -1})
            with self.assertRaises(official.OfficialLoginError) as caught:
                await self.resolve(http)
            self.assertEqual(caught.exception.code, code)
            self.assertIn('接口错误码', str(caught.exception))
            self.assertNotIn('private-response', str(caught.exception))
            http.post.assert_called_once()

    async def test_redirect_and_http_failures_do_not_follow_or_retry(self):
        for status in (301, 302, 400, 401, 429, 500):
            http = Mock()
            http.post.return_value = Response(body=b'private-error-response', status=status)
            with self.assertRaises(official.OfficialError) as caught:
                await self.resolve(http)
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn('private-error-response', str(caught.exception))
            http.post.assert_called_once()

    async def test_bad_or_oversized_json_never_echoed_or_accepted(self):
        values = [b'private-error-response', b'[]', b'{}', b'{"errorID":false}',
                  b'{"errorID":-1}', b'{"errorID":65536}', b'x' * 65537]
        for body in values:
            http = Mock()
            http.post.return_value = Response(body=body)
            with self.assertRaises(official.OfficialError) as caught:
                await self.resolve(http)
            self.assertNotIn('private-error-response', str(caught.exception))
            http.post.assert_called_once()

    async def test_invalid_session_not_accepted(self):
        for changes in ({'userID': True}, {'userID': 0}, {'userID': '123'}, {'userID': 2**32},
                        {'token': ''}, {'token': 'x\nprivate'}, {'token': 'x' * 513}, {'token': 123}):
            http = Mock()
            http.post.return_value = Response({'errorID': 0, 'userID': 123, 'token': 'secret', **changes})
            with self.assertRaises(official.OfficialError) as caught:
                await self.resolve(http)
            self.assertNotIn('private', str(caught.exception))

    async def test_no_credential_probe_uses_verified_https(self):
        http = Mock()
        http.post.return_value = Response(body=b'alive\n')
        await self.client.probe_login(http, 0)
        self.assertEqual(http.post.call_args.args[0], 'https://ai.sys-allnet.cn/wc_aime/api/alive_check')
        self.assertEqual(http.post.call_args.kwargs['data'], b'{}')
        self.assertFalse(http.post.call_args.kwargs['allow_redirects'])

    async def test_cancellation_is_not_converted_to_retry_or_exposed(self):
        http = Mock()
        http.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.resolve(http)
        http.post.assert_called_once()


class DiagnosticToolTest(unittest.TestCase):
    def test_legacy_install_option_does_not_download_or_execute_native_components(self):
        path = Root / 'tools/setup_official_runtime.py'
        spec = importlib.util.spec_from_file_location('https_diagnostic_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (patch('sys.argv', [str(path), '--install', '--probe']),
              patch.object(module, 'diagnose', AsyncMock()) as diagnose,
              redirect_stdout(io.StringIO()) as output):
            self.assertEqual(module.main(), 0)
        diagnose.assert_awaited_once()
        self.assertIn('no files downloaded', output.getvalue())
