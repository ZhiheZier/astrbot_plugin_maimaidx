import base64
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from ..command.mai_base import bind_divingfish_handler
from ..libraries import maimaidx_api_data
from ..libraries.maimaidx_api_data import MaimaiAPI, maiApi
from ..libraries.maimaidx_binding import pending_bindings
from ..libraries.maimaidx_error import (
    DivingFishOAuthBindingMismatchError,
    DivingFishOAuthNotBoundError,
    DivingFishOAuthTokenExpiredError,
)


class DivingFishOAuthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pending_bindings.clear()
        self.addCleanup(pending_bindings.clear)
        self.api = MaimaiAPI()
        self.api.config.df_client_id = 'client-id'
        self.api.config.df_client_secret = 'client-secret'

    def test_subject_ref_matches_migration_formula(self):
        expected = hashlib.sha256(b'client-id:12345678').hexdigest()

        self.assertEqual(
            self.api._divingfish_subject(qqid=12345678),
            f'ref:{expected}',
        )
        self.assertEqual(
            self.api._divingfish_subject(username='someone'),
            'username:someone',
        )
        self.assertEqual(
            self.api._divingfish_subject(
                qqid=12345678,
                username='someone',
            ),
            'username:someone',
        )

    async def test_access_token_is_cached_until_near_expiry(self):
        request = AsyncMock(
            return_value={
                'access_token': 'access-token',
                'expires_in': 300,
            }
        )
        with patch.object(self.api, '_request_divingfish_oauth', request):
            first = await self.api._divingfish_access_token('ref:subject')
            second = await self.api._divingfish_access_token('ref:subject')

        self.assertEqual(first, 'access-token')
        self.assertEqual(second, 'access-token')
        request.assert_awaited_once()

    async def test_records_use_bearer_endpoint_without_identity_params(self):
        response = {
            'additional_rating': 0,
            'nickname': 'Tester',
            'plate': None,
            'rating': 15000,
            'username': 'tester',
            'records': [],
        }
        with (
            patch.object(
                self.api,
                '_divingfish_access_token',
                AsyncMock(return_value='access-token'),
            ),
            patch.object(
                self.api,
                '_requestmai',
                AsyncMock(return_value=response),
            ) as request,
        ):
            result = await self.api.query_user_get_dev(qqid=12345678)

        self.assertEqual(result.nickname, 'Tester')
        request.assert_awaited_once()
        args, kwargs = request.await_args
        self.assertEqual(args, ('GET', '/player/records'))
        self.assertEqual(
            kwargs['request_headers'],
            {'Authorization': 'Bearer access-token'},
        )
        self.assertNotIn('params', kwargs)

    async def test_public_b50_does_not_send_legacy_token(self):
        response = {
            'additional_rating': 0,
            'nickname': 'Tester',
            'plate': None,
            'rating': 15000,
            'username': 'tester',
            'charts': {'sd': [], 'dx': []},
        }
        self.api.token = 'legacy-token'
        self.api.headers = {'developer-token': 'legacy-token'}
        with patch.object(
            self.api,
            '_requestmai',
            AsyncMock(return_value=response),
        ) as request:
            await self.api.query_user_b50(qqid=12345678)

        _, kwargs = request.await_args
        self.assertEqual(kwargs['request_headers'], {})

    async def test_plate_uses_new_endpoint_and_only_sends_versions(self):
        with (
            patch.object(
                self.api,
                '_divingfish_access_token',
                AsyncMock(return_value='access-token'),
            ),
            patch.object(
                self.api,
                '_requestmai',
                AsyncMock(return_value={'verlist': []}),
            ) as request,
        ):
            result = await self.api.query_user_plate(
                qqid=12345678,
                version=['maimai'],
            )

        self.assertEqual(result, [])
        args, kwargs = request.await_args
        self.assertEqual(args, ('POST', '/player/plate'))
        self.assertEqual(kwargs['json'], {'version': ['maimai']})

    async def test_expired_bearer_token_is_replaced_once(self):
        access_token = AsyncMock(side_effect=['expired-token', 'fresh-token'])
        request = AsyncMock(
            side_effect=[
                DivingFishOAuthTokenExpiredError(),
                {'records': []},
            ]
        )
        with (
            patch.object(self.api, '_divingfish_access_token', access_token),
            patch.object(self.api, '_requestmai', request),
        ):
            result = await self.api._requestmai_oauth(
                'GET', '/player/records', qqid=12345678
            )

        self.assertEqual(result, {'records': []})
        self.assertEqual(access_token.await_count, 2)
        self.assertEqual(
            request.await_args_list[1].kwargs['request_headers'],
            {'Authorization': 'Bearer fresh-token'},
        )

    async def test_binding_uses_masked_label_and_subject_ref(self):
        request = AsyncMock(
            return_value={
                'user_code': 'BCDF-GHJK',
                'verification_uri_complete': 'https://example.test/device',
            }
        )
        with patch.object(self.api, '_request_divingfish_oauth', request):
            await self.api.start_divingfish_binding(12345678)

        _, kwargs = request.await_args
        data = kwargs['data']
        self.assertEqual(data['binding_label'], 'QQ 12****78')
        self.assertEqual(
            data['subject_ref'],
            hashlib.sha256(b'client-id:12345678').hexdigest(),
        )
        self.assertEqual(data['scope'], 'prober.records.read')
        self.assertEqual(data['handoff'], 'code')

    async def test_starting_binding_discards_cached_token(self):
        subject = self.api._divingfish_subject(qqid=12345678)
        self.api._oauth_tokens[subject] = ('old-token', float('inf'))
        with patch.object(
            self.api,
            '_request_divingfish_oauth',
            AsyncMock(return_value={'device_code': 'device-code'}),
        ):
            await self.api.start_divingfish_binding(12345678)

        self.assertNotIn(subject, self.api._oauth_tokens)

    @staticmethod
    def token(subject):
        payload = base64.urlsafe_b64encode(json.dumps({'sub': subject}).encode()).decode().rstrip('=')
        return f'header.{payload}.signature'

    async def test_confirmation_code_checks_binding_identity_and_caches_verified_token(self):
        token = self.token('player-1')
        request = AsyncMock(
            side_effect=[
                {'access_token': 'redeemed-token', 'sub': 'player-1'},
                {'access_token': token, 'expires_in': 300},
            ]
        )
        with (
            patch.object(self.api, '_request_divingfish_oauth', request),
        ):
            await self.api.redeem_divingfish_binding(12345678, 'BCDF-GHJK-MNPQ')

        subject = self.api._divingfish_subject(qqid=12345678)
        self.assertEqual(self.api._oauth_tokens[subject][0], token)
        self.assertEqual(request.await_count, 2)
        data = request.await_args_list[0].kwargs['data']
        self.assertEqual(data['grant_type'], 'urn:diving-fish:params:oauth:grant-type:confirmation-code')
        self.assertEqual(data['confirmation_code'], 'BCDF-GHJK-MNPQ')
        self.assertEqual(data['subject_ref'], self.api._divingfish_subject_ref(12345678))
        self.assertEqual(request.await_args_list[1].kwargs['data']['subject'], subject)

    async def test_confirmation_code_for_another_account_is_not_cached(self):
        for result in (
            {'access_token': self.token('other-player')},
            DivingFishOAuthNotBoundError(),
        ):
            with self.subTest(result=type(result).__name__):
                request = AsyncMock(side_effect=[
                    {'access_token': 'redeemed-token', 'sub': 'player-1'}, result,
                ])
                with patch.object(self.api, '_request_divingfish_oauth', request):
                    with self.assertRaises(DivingFishOAuthBindingMismatchError):
                        await self.api.redeem_divingfish_binding(12345678, 'BCDF-GHJK-MNPQ')
                self.assertFalse(self.api._oauth_tokens)

    async def test_group_binding_prompts_for_provider_specific_code_without_polling(self):
        class FakeEvent:
            message_obj = SimpleNamespace(group_id='10001')

            @staticmethod
            def get_sender_id():
                return '12345678'

            @staticmethod
            def plain_result(message):
                return message

        binding = {
            'device_code': 'device-code',
            'user_code': 'BCDF-GHJK',
            'verification_uri': 'https://example.test/device',
            'verification_uri_complete': 'https://example.test/device?code=1',
            'expires_in': 600,
            'interval': 5,
        }
        with (
            patch.object(
                type(maiApi),
                'divingfish_oauth_configured',
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                maiApi,
                'start_divingfish_binding',
                AsyncMock(return_value=binding),
            ),
        ):
            messages = [
                message async for message in bind_divingfish_handler(FakeEvent())
            ]

        self.assertEqual(len(messages), 1)
        self.assertIn('水鱼授权码', messages[0])
        self.assertNotIn('绑定成功', messages[0])
        self.assertNotIn('撤回', messages[0])
        self.assertTrue(pending_bindings.active(pending_bindings.key(FakeEvent())))


if __name__ == '__main__':
    unittest.main()
