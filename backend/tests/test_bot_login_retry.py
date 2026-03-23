import asyncio
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import bot


class FakeRetryClient:
    def __init__(self):
        self.stop_requested = False
        self.account_id = 3
        self.running = False
        self.current_token = None
        self._login_ready_event = None
        self.closed = True
        self.clear_calls = 0
        self.start_closed_states = []

    def _reset_login_ready_event(self):
        self._login_ready_event = asyncio.Event()

    async def _wait_for_login_ready(self):
        if self._login_ready_event is None:
            self._reset_login_ready_event()
        await self._login_ready_event.wait()

    def is_closed(self):
        return self.closed

    def clear(self):
        self.clear_calls += 1
        self.closed = False

    async def close(self):
        self.closed = True

    async def start(self, token, reconnect=True):
        self.start_closed_states.append(self.closed)
        self._login_ready_event.set()
        self.stop_requested = True
        await asyncio.sleep(0)


class StartWithRetriesTests(unittest.IsolatedAsyncioTestCase):
    async def test_clears_closed_client_before_retrying_login(self):
        client = FakeRetryClient()

        await bot.DiscordBotClient.start_with_retries(
            client,
            'fake-token',
            max_retries=1,
            timeout=0.1,
            retry_delay=0
        )

        self.assertEqual(client.clear_calls, 1)
        self.assertEqual(client.start_closed_states, [False])
