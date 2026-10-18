import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ..command import mai_base
from ..libraries import maimaidx_lxns
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_binding import PendingBindings, normalize_binding_code
from ..libraries.maimaidx_error import DivingFishOAuthConfirmationError
from ..libraries.maimaidx_user import ServiceName, userstore


def make_event(text='', *, user='12345678', platform='onebot', bot='87654321', group='10001'):
    return SimpleNamespace(
        message_str=text,
        message_obj=SimpleNamespace(group_id=group),
        get_sender_id=lambda: user,
        get_platform_id=lambda: platform,
        get_self_id=lambda: bot,
        plain_result=lambda message: message,
    )


async def replies(event):
    return [reply async for reply in mai_base.authcode_handler(event)]


class BindingRoutingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.sessions = PendingBindings()
        self.stack.enter_context(patch.object(mai_base, 'pending_bindings', self.sessions))
        for name, value in (
            ('df_client_id', 'df-client'), ('df_client_secret', 'df-secret'),
            ('lx_client_id', 'lx-client'), ('lx_client_secret', 'lx-secret'),
        ):
            self.stack.enter_context(patch.object(maiApi.config, name, value))
        self.redeem = self.stack.enter_context(patch.object(
            maiApi, 'redeem_divingfish_binding', AsyncMock(),
        ))
        self.update = self.stack.enter_context(patch.object(userstore, 'update', AsyncMock()))
        self.lx_api = SimpleNamespace(
            oauth_fetch_token=AsyncMock(return_value=SimpleNamespace(
                access_token='lx-access', refresh_token='lx-refresh',
            )),
            player_personal=AsyncMock(return_value=SimpleNamespace(
                name='Player', friend_code=123456789,
            )),
        )
        self.lx_class = self.stack.enter_context(patch.object(
            maimaidx_lxns, 'LxnsAPI', Mock(return_value=self.lx_api),
        ))

    def start(self, provider, event=None):
        event = event or make_event()
        client_id = 'df-client' if provider == ServiceName.DIVINGFISH else 'lx-client'
        self.sessions.start(self.sessions.key(event), provider, client_id)

    async def test_dedicated_divingfish_command_normalizes_code_and_sends_success(self):
        self.start(ServiceName.DIVINGFISH)
        messages = await replies(make_event('/水鱼授权码 bcdfghjkmnpq'))
        self.redeem.assert_awaited_once_with('12345678', 'BCDF-GHJK-MNPQ')
        self.lx_class.assert_not_called()
        self.update.assert_awaited_once_with('12345678', service=ServiceName.DIVINGFISH)
        self.assertIn('水鱼查分器绑定成功', messages[0])
        self.assertNotIn('撤回', messages[0])
        self.assertFalse(self.sessions.active(self.sessions.key(make_event())))

    async def test_generic_command_routes_to_the_only_pending_provider(self):
        for provider in ServiceName:
            with self.subTest(provider=provider):
                self.start(provider)
                messages = await replies(make_event('授权码 BCDF-GHJK-MNPQ'))
                self.assertTrue('成功' in messages[0] or '授权完成' in messages[0])
        self.redeem.assert_awaited_once()
        self.lx_api.oauth_fetch_token.assert_awaited_once_with('BCDF-GHJK-MNPQ')

    async def test_simultaneous_bindings_require_explicit_provider_and_remain_independent(self):
        self.start(ServiceName.DIVINGFISH)
        self.start(ServiceName.LXNS)
        messages = await replies(make_event('code BCDF-GHJK-MNPQ'))
        self.assertIn('同时发起', messages[0])
        self.redeem.assert_not_awaited()
        self.lx_class.assert_not_called()
        await replies(make_event('落雪授权码 ABCD-EFGH-IJKL'))
        self.lx_api.oauth_fetch_token.assert_awaited_once_with('ABCD-EFGH-IJKL')
        self.assertEqual(
            set(self.sessions.active(self.sessions.key(make_event()))),
            {ServiceName.DIVINGFISH},
        )
        await replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ'))
        self.redeem.assert_awaited_once()

    async def test_missing_expired_or_wrong_provider_session_never_redeems(self):
        messages = await replies(make_event('授权码 BCDF-GHJK-MNPQ'))
        self.assertIn('没有待完成', messages[0])
        self.start(ServiceName.DIVINGFISH)
        messages = await replies(make_event('落雪授权码 BCDF-GHJK-MNPQ'))
        self.assertIn('没有待完成的落雪', messages[0])
        session = self.sessions.active(self.sessions.key(make_event()))[ServiceName.DIVINGFISH]
        session.expires_at = 0
        messages = await replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ'))
        self.assertIn('已超时', messages[0])
        self.redeem.assert_not_awaited()
        self.lx_class.assert_not_called()

    async def test_sessions_are_isolated_by_sender_platform_and_bot(self):
        self.start(ServiceName.DIVINGFISH)
        for identity in ({'user': '999'}, {'platform': 'other'}, {'bot': '999'}):
            with self.subTest(identity=identity):
                messages = await replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ', **identity))
                self.assertIn('没有待完成', messages[0])
        self.redeem.assert_not_awaited()

    async def test_same_sender_can_submit_in_private_chat_after_group_binding(self):
        self.start(ServiceName.DIVINGFISH)
        messages = await replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ', group=None))
        self.assertIn('绑定成功', messages[0])
        self.redeem.assert_awaited_once()

    async def test_failed_divingfish_code_does_not_fall_back_to_lxns(self):
        self.start(ServiceName.DIVINGFISH)
        self.start(ServiceName.LXNS)
        self.redeem.side_effect = DivingFishOAuthConfirmationError()
        messages = await replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ'))
        self.assertIn('水鱼授权码无效', messages[0])
        self.lx_class.assert_not_called()
        self.update.assert_not_awaited()
        self.assertEqual(len(self.sessions.active(self.sessions.key(make_event()))), 2)

    async def test_concurrent_duplicate_submission_only_redeems_once(self):
        self.start(ServiceName.DIVINGFISH)

        async def redeem(*_):
            await asyncio.sleep(0)

        self.redeem.side_effect = redeem
        results = await asyncio.gather(*[
            replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ')) for _ in range(2)
        ])
        self.redeem.assert_awaited_once()
        self.assertEqual(sum('绑定成功' in messages[0] for messages in results), 1)

    async def test_client_change_requires_new_binding(self):
        self.start(ServiceName.DIVINGFISH)
        with patch.object(maiApi.config, 'df_client_id', 'other-client'):
            messages = await replies(make_event('水鱼授权码 BCDF-GHJK-MNPQ'))
        self.assertIn('应用配置已变更', messages[0])
        self.redeem.assert_not_awaited()

    async def test_invalid_code_format_is_not_sent_to_either_provider(self):
        self.start(ServiceName.DIVINGFISH)
        messages = await replies(make_event('水鱼授权码 AAAA-BBBB-CCCC'))
        self.assertIn('格式错误', messages[0])
        self.redeem.assert_not_awaited()
        self.lx_class.assert_not_called()

    def test_session_restart_does_not_consume_replacement_session(self):
        event = make_event()
        self.start(ServiceName.DIVINGFISH, event)
        key = self.sessions.key(event)
        original = self.sessions.active(key)[ServiceName.DIVINGFISH]
        self.start(ServiceName.DIVINGFISH, event)
        self.sessions.consume(key, ServiceName.DIVINGFISH, original)
        self.assertIsNot(self.sessions.active(key)[ServiceName.DIVINGFISH], original)

    def test_provider_code_formats_remain_distinct(self):
        self.assertIsNone(normalize_binding_code('ABCD-EFGH-IJKL', ServiceName.DIVINGFISH))
        self.assertEqual(
            normalize_binding_code('abcd-efgh-ijkl', ServiceName.LXNS), 'ABCD-EFGH-IJKL',
        )
