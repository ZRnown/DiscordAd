import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class FakeDb:
    def __init__(self):
        self.status_updates = []

    def get_account_by_id(self, account_id):
        return {'id': account_id, 'token': 'fake-token'}

    def update_account_status(self, account_id, status):
        self.status_updates.append((account_id, status))
        return True


class StartAccountThreadingTests(unittest.TestCase):
    def test_start_account_returns_connecting_when_existing_client_not_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                config_module = importlib.import_module('config')
                importlib.reload(config_module)
                app_module = importlib.import_module('app')
                backend_app = importlib.reload(app_module)

                class ConnectingClient:
                    account_id = 42

                    def is_closed(self):
                        return False

                    def is_ready(self):
                        return False

                class FakeLoop:
                    def is_running(self):
                        return True

                fake_db = FakeDb()

                with patch.object(backend_app, 'db', fake_db), \
                     patch.object(backend_app, 'bot_clients', [ConnectingClient()]), \
                     patch.object(backend_app, 'bot_loop', FakeLoop()):
                    result = backend_app._start_account_by_id(42)

                self.assertEqual(result, {'success': False, 'error': '账号启动中'})
                self.assertEqual(fake_db.status_updates, [])
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)

    def test_start_account_creates_client_inside_scheduled_coroutine(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                config_module = importlib.import_module('config')
                importlib.reload(config_module)
                app_module = importlib.import_module('app')
                backend_app = importlib.reload(app_module)

                created_inside_scheduled_context = []
                scheduled_context = {'value': False}

                class FakeClient:
                    def __init__(self, account_id=None, user_id=None, user_shops=None, role='both'):
                        created_inside_scheduled_context.append(scheduled_context['value'])
                        self.account_id = account_id
                        self.stop_requested = False

                    async def start_with_retries(self, token):
                        return None

                    def is_closed(self):
                        return False

                    def is_ready(self):
                        return False

                class FakeFuture:
                    def result(self, timeout=None):
                        return None

                class FakeLoop:
                    def is_running(self):
                        return True

                def fake_run_coroutine_threadsafe(coro, loop):
                    scheduled_context['value'] = True
                    try:
                        asyncio.run(coro)
                    finally:
                        scheduled_context['value'] = False
                    return FakeFuture()

                fake_db = FakeDb()

                with patch.object(backend_app, 'db', fake_db), \
                     patch.object(backend_app, 'bot_clients', []), \
                     patch.object(backend_app, 'DiscordBotClient', FakeClient), \
                     patch.object(backend_app, 'bot_loop', FakeLoop()), \
                     patch.object(backend_app.asyncio, 'run_coroutine_threadsafe', side_effect=fake_run_coroutine_threadsafe):
                    result = backend_app._start_account_by_id(42)

                self.assertTrue(result.get('success'))
                self.assertEqual(created_inside_scheduled_context, [True])
                self.assertEqual(fake_db.status_updates, [(42, 'connecting')])
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)
