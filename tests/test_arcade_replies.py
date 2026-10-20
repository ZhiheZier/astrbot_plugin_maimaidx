import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as Comp

from ..command import mai_arcade
from ..command.mai_arcade_replies import ARCADE_LINE_LIMIT, ARCADE_NODE_LIMIT, ARCADE_TEXT_LIMIT, arcade_list_results
from ..libraries import maimaidx_arcade as a
from ..libraries.maimaidx_arcade_games import DEFAULT_ARCADE_GAME
from ..libraries.maimaidx_arcade_messages import ArcadeQueryGame, ArcadeQueryResult
from .test_arcade_games import arcade
from .test_nearcade import collect, event


def reply_event(**kwargs):
    result = event(**kwargs)
    result.get_self_id = lambda: '654321'
    result.chain_result = lambda chain: chain
    return result


def forward_texts(result):
    return [node.content[0].text for node in result[0].nodes]


class ArcadeListReplyTest(unittest.IsolatedAsyncioTestCase):
    def test_short_and_exact_limit_lists_remain_plain_text(self):
        for text in ('店名：测试\n  - 舞萌：3人', 'x' * ARCADE_TEXT_LIMIT):
            with self.subTest(length=len(text)):
                self.assertEqual(list(arcade_list_results(reply_event(), [text])), [text])

    def test_combined_length_decides_forwarding_before_pagination(self):
        blocks = ['A' * (ARCADE_TEXT_LIMIT // 2 + 1), 'B' * (ARCADE_TEXT_LIMIT // 2 + 1)]
        results = list(arcade_list_results(reply_event(), blocks))
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0][0], Comp.Nodes)
        self.assertEqual(forward_texts(results[0]), blocks)
        self.assertTrue(all(node.uin == '654321' for node in results[0][0].nodes))

    async def test_oversized_shop_is_complete_and_cq_text_stays_plain(self):
        text = '[CQ:image,file=test]' + '长地址' * 2000
        results = list(arcade_list_results(reply_event(), [text]))
        texts = forward_texts(results[0])
        self.assertEqual(''.join(texts), text)
        self.assertTrue(all(0 < len(chunk) <= ARCADE_NODE_LIMIT for chunk in texts))
        wire = await results[0][0].to_dict()
        content = wire['messages'][0]['data']['content']
        self.assertEqual(content[0]['type'], 'text')
        self.assertIn('[CQ:image,file=test]', content[0]['data']['text'])

    def test_non_qq_platform_splits_plain_text_instead_of_forwarding(self):
        blocks = ['A' * (ARCADE_TEXT_LIMIT // 2 + 1), 'B' * (ARCADE_TEXT_LIMIT // 2 + 1)]
        results = list(arcade_list_results(reply_event(platform='telegram'), blocks))
        self.assertEqual(results, blocks)

    def test_empty_list_sends_nothing(self):
        self.assertEqual(list(arcade_list_results(reply_event(), [])), [])

    def test_line_limit_folds_short_but_tall_lists(self):
        for line_count in (ARCADE_LINE_LIMIT, ARCADE_LINE_LIMIT + 1):
            with self.subTest(lines=line_count):
                text = '\n'.join('短行' for _ in range(line_count))
                results = list(arcade_list_results(reply_event(), [text]))
                if line_count <= ARCADE_LINE_LIMIT:
                    self.assertEqual(results, [text])
                else:
                    self.assertEqual(forward_texts(results[0]), [text])

    def test_text_above_new_limit_folds_even_on_one_line(self):
        text = 'x' * (ARCADE_TEXT_LIMIT + 1)
        results = list(arcade_list_results(reply_event(), [text]))
        self.assertEqual(forward_texts(results[0]), [text])


class ArcadeLongListHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.records = a.ArcadeList([
            arcade(name=f'测试购物中心四层机厅{i}', id=str(1000 + i)) for i in range(80)
        ])
        for record in self.records:
            record.location = '商场四层详细地址' * 5
        hidden = arcade(name='未订阅机厅秘密名称', id='9999')
        hidden.group = [300]
        self.records.append(hidden)
        self.cloud = SimpleNamespace(
            uses_cloud_catalog=True, catalog=self.records,
            games_for=lambda identity: [], search_catalog=AsyncMock(),
            read_attendance=AsyncMock(return_value={
                record.id: ArcadeQueryResult(record.name, (
                    ArcadeQueryGame(DEFAULT_ARCADE_GAME, 3, 2),
                ), source='nearcade') for record in self.records
            }),
        )

    async def test_local_and_cloud_subscriptions_send_one_forward_with_shop_nodes(self):
        for api in (None, self.cloud):
            with self.subTest(source='nearcade' if api else 'local'):
                with patch.object(mai_arcade.arcade, 'total', self.records, create=True):
                    results = [reply async for reply in mai_arcade.check_subscribe_handler(
                        reply_event(text='查看订阅'), nearcade=api,
                    )]
                self.assertEqual(len(results), 1)
                texts = forward_texts(results[0])
                shops = texts if api else texts[1:]
                self.assertEqual(len(shops), 80)
                for record, text in zip(self.records[:80], shops):
                    self.assertIn(record.name, text)
                    self.assertIn(record.location, text)
                self.assertNotIn('未订阅机厅秘密名称', ''.join(texts))
                if not api:
                    self.assertIn('群200订阅机厅信息如下', texts[0])
        self.assertEqual(self.cloud.search_catalog.await_count, 80)

    async def test_local_and_cloud_overviews_send_one_forward_with_shop_nodes(self):
        for api in (None, self.cloud):
            with self.subTest(source='nearcade' if api else 'local'):
                with patch.object(mai_arcade.arcade, 'total', self.records, create=True):
                    results = await collect(mai_arcade.arcade_query_multiple_handler,
                                            reply_event(text='机厅几人'), api)
                self.assertEqual(len(results), 1)
                texts = forward_texts(results[0])
                self.assertEqual(len(texts), 80)
                for record, text in zip(self.records[:80], texts):
                    self.assertTrue(text.startswith(f'店名：{record.name}\n'))
                    self.assertIn('舞萌：3人' if api else '舞萌：2人', text)
                self.assertNotIn('未订阅机厅秘密名称', ''.join(texts))
        self.cloud.read_attendance.assert_awaited_once()

    async def test_cloud_subscription_failure_warning_is_kept_in_forward_nodes(self):
        from ..libraries.maimaidx_nearcade import NearcadeError

        self.cloud.search_catalog.side_effect = NearcadeError('offline')
        results = [reply async for reply in mai_arcade.check_subscribe_handler(
            reply_event(text='查看订阅'), nearcade=self.cloud,
        )]
        texts = forward_texts(results[0])
        self.assertEqual(len(texts), 80)
        self.assertTrue(all('云端查询失败，以上为订阅缓存信息' in text for text in texts))
