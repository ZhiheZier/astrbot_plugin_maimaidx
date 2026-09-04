import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from ..libraries.maimaidx_user import ServiceName, Theme, UserStore


class UserStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_updates_keep_all_fields_and_write_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'user_data.json'
            store = UserStore(path)

            await asyncio.gather(
                store.update(10001, friend_code=123456789),
                store.update(10001, service=ServiceName.LXNS),
                store.update(10001, theme=Theme.CIRCLE),
                store.update(10002, friend_code=987654321),
            )

            user = store.get(10001)
            self.assertEqual(user.friend_code, 123456789)
            self.assertEqual(user.service, ServiceName.LXNS)
            self.assertEqual(user.theme, Theme.CIRCLE)
            self.assertFalse(path.with_name(f'.{path.name}.tmp').exists())

            persisted = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(persisted['10001']['friend_code'], 123456789)
            self.assertEqual(persisted['10002']['friend_code'], 987654321)

    async def test_nonnumeric_platform_id_is_persisted(self):
        openid = 'ABCDEF0123456789ABCDEF0123456789'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'user_data.json'
            store = UserStore(path)

            await store.update(
                openid,
                friend_code=123456789,
                service=ServiceName.LXNS,
            )

            reloaded = UserStore(path)
            user = reloaded.get(openid)
            self.assertEqual(user.qqid, openid)
            self.assertEqual(user.friend_code, 123456789)
            self.assertEqual(user.service, ServiceName.LXNS)


if __name__ == '__main__':
    unittest.main()
