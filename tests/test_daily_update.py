import unittest
from unittest.mock import AsyncMock, patch

from ..main import MaimaiDXPlugin


class DailyUpdateTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_update_refreshes_all_maimai_data(self):
        plugin = MaimaiDXPlugin.__new__(MaimaiDXPlugin)

        with patch(
            'data.plugins.astrbot_plugin_maimaidx.main.mai.update',
            new=AsyncMock(),
        ) as update:
            await plugin._daily_update()

        update.assert_awaited_once_with()


if __name__ == '__main__':
    unittest.main()
