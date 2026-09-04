import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from ..command.mai_base import _authorize_url, authcode_handler
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_lxns import (
    LxnsAPI,
    LxnsPlayer,
    LxnsToken,
    OAUTH_OOB_REDIRECT_URI,
)
from ..libraries.maimaidx_user import ServiceName, userstore


class LxnsOAuthTest(unittest.IsolatedAsyncioTestCase):
    def test_authorize_url_uses_minimal_scope_and_oob_redirect(self):
        with (
            patch.object(maiApi.config, 'lx_client_id', 'client-id'),
            patch.object(maiApi.config, 'lx_redirect_uri', ''),
        ):
            query = parse_qs(urlparse(_authorize_url()).query)

        self.assertEqual(query['client_id'], ['client-id'])
        self.assertEqual(query['scope'], ['read_player'])
        self.assertEqual(query['redirect_uri'], [OAUTH_OOB_REDIRECT_URI])

    def test_authorize_url_extracts_redirect_from_pasted_authorize_url(self):
        pasted_url = (
            'https://maimai.lxns.net/oauth/authorize?response_type=code'
            '&client_id=client-id'
            '&redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob'
            '&scope=read_player'
        )
        with (
            patch.object(maiApi.config, 'lx_client_id', 'client-id'),
            patch.object(maiApi.config, 'lx_redirect_uri', pasted_url),
        ):
            query = parse_qs(urlparse(_authorize_url()).query)

        self.assertEqual(query['redirect_uri'], [OAUTH_OOB_REDIRECT_URI])

    async def test_token_exchange_uses_same_oob_redirect(self):
        api = LxnsAPI()
        request = AsyncMock(
            return_value={
                'access_token': 'access-token',
                'refresh_token': 'refresh-token',
            }
        )
        with (
            patch.object(maiApi.config, 'lx_client_id', 'client-id'),
            patch.object(maiApi.config, 'lx_client_secret', 'client-secret'),
            patch.object(maiApi.config, 'lx_redirect_uri', ''),
            patch.object(api, '_request', request),
        ):
            await api.oauth_fetch_token('authorization-code')

        self.assertEqual(
            request.await_args.kwargs['json']['redirect_uri'],
            OAUTH_OOB_REDIRECT_URI,
        )

    async def test_personal_request_refreshes_rotating_token_and_retries(self):
        class FakeResponse:
            def __init__(self, status, payload):
                self.status = status
                self.payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def json(self):
                return self.payload

        class FakeSession:
            def __init__(self):
                self.responses = [
                    FakeResponse(401, {}),
                    FakeResponse(200, {'data': {'name': 'Player'}}),
                ]
                self.headers = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def request(self, _method, _url, *, headers, **_kwargs):
                self.headers.append(headers)
                return self.responses.pop(0)

        openid = 'refresh-test-openid'
        api = LxnsAPI(
            qqid=openid,
            access_token='expired-access-token',
            refresh_token='old-refresh-token',
        )
        session = FakeSession()
        refresh = AsyncMock(
            return_value=LxnsToken(
                access_token='new-access-token',
                refresh_token='new-refresh-token',
            )
        )
        update = AsyncMock()
        stored = SimpleNamespace(
            access_token='expired-access-token',
            refresh_token='old-refresh-token',
        )
        with (
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_lxns.ClientSession',
                return_value=session,
            ),
            patch.object(api, 'oauth_refresh_token', refresh),
            patch.object(userstore, 'get', return_value=stored),
            patch.object(userstore, 'update', update),
        ):
            player = await api.player_personal()

        self.assertEqual(player.name, 'Player')
        refresh.assert_awaited_once_with('old-refresh-token')
        update.assert_awaited_once_with(
            openid,
            access_token='new-access-token',
            refresh_token='new-refresh-token',
        )
        self.assertEqual(
            [headers['Authorization'] for headers in session.headers],
            ['Bearer expired-access-token', 'Bearer new-access-token'],
        )

    async def test_refresh_adopts_token_rotated_by_concurrent_request(self):
        openid = 'concurrent-refresh-openid'
        api = LxnsAPI(
            qqid=openid,
            access_token='expired-access-token',
            refresh_token='old-refresh-token',
        )
        refresh = AsyncMock()
        stored = SimpleNamespace(
            access_token='already-refreshed-access-token',
            refresh_token='already-refreshed-refresh-token',
        )
        with (
            patch.object(api, 'oauth_refresh_token', refresh),
            patch.object(userstore, 'get', return_value=stored),
        ):
            refreshed = await api._refresh_oauth_token('expired-access-token')

        self.assertTrue(refreshed)
        self.assertEqual(api.access_token, 'already-refreshed-access-token')
        self.assertEqual(api.refresh_token, 'already-refreshed-refresh-token')
        refresh.assert_not_awaited()

    async def test_nonnumeric_platform_id_can_complete_binding(self):
        openid = 'ABCDEF0123456789ABCDEF0123456789'

        class FakeEvent:
            message_str = '授权码 ABCD-EFGH-IJKL'
            message_obj = SimpleNamespace(group_id='group-id')

            @staticmethod
            def get_sender_id():
                return openid

            @staticmethod
            def plain_result(message):
                return message

        api = SimpleNamespace(
            access_token=None,
            oauth_fetch_token=AsyncMock(
                return_value=LxnsToken(
                    access_token='access-token',
                    refresh_token='refresh-token',
                )
            ),
            player_personal=AsyncMock(
                return_value=LxnsPlayer(name='Player', friend_code=123456789)
            ),
        )
        update = AsyncMock()
        with (
            patch.object(maiApi.config, 'lx_client_id', 'client-id'),
            patch.object(maiApi.config, 'lx_client_secret', 'client-secret'),
            patch.object(maiApi.config, 'lx_redirect_uri', ''),
            patch(
                'data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_lxns.LxnsAPI',
                return_value=api,
            ) as api_class,
            patch.object(userstore, 'update', update),
        ):
            messages = [
                message async for message in authcode_handler(FakeEvent())
            ]

        api_class.assert_called_once_with(qqid=openid)
        update.assert_awaited_once_with(
            openid,
            access_token='access-token',
            refresh_token='refresh-token',
            friend_code=123456789,
            service=ServiceName.LXNS,
        )
        self.assertIn('授权完成', messages[0])


if __name__ == '__main__':
    unittest.main()
