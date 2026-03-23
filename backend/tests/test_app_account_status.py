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
    def __init__(self, accounts):
        self.accounts = accounts

    def get_all_accounts(self):
        return [dict(account) for account in self.accounts]


class FakeClient:
    def __init__(self, account_id, ready, closed=False):
        self.account_id = account_id
        self._ready = ready
        self._closed = closed

    def is_ready(self):
        return self._ready

    def is_closed(self):
        return self._closed


class AccountStatusApiTests(unittest.TestCase):
    def _load_app_module(self):
        config_module = importlib.import_module('config')
        importlib.reload(config_module)
        app_module = importlib.import_module('app')
        return importlib.reload(app_module)

    def test_get_accounts_does_not_keep_stale_online_status_without_live_client(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                backend_app = self._load_app_module()
                fake_db = FakeDb([
                    {'id': 3, 'username': 'tester', 'status': 'online'}
                ])

                with patch.object(backend_app, 'db', fake_db), \
                     patch.object(backend_app, 'bot_clients', []):
                    with backend_app.app.test_request_context('/api/accounts'):
                        payload = backend_app.get_accounts().get_json()

                self.assertEqual(payload['accounts'][0]['status'], 'offline')
                self.assertFalse(payload['accounts'][0]['is_online'])
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)

    def test_get_accounts_marks_connecting_when_client_not_ready_yet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                backend_app = self._load_app_module()
                fake_db = FakeDb([
                    {'id': 7, 'username': 'starter', 'status': 'offline'}
                ])

                with patch.object(backend_app, 'db', fake_db), \
                     patch.object(backend_app, 'bot_clients', [FakeClient(account_id=7, ready=False)]):
                    with backend_app.app.test_request_context('/api/accounts'):
                        payload = backend_app.get_accounts().get_json()

                self.assertEqual(payload['accounts'][0]['status'], 'connecting')
                self.assertFalse(payload['accounts'][0]['is_online'])
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)

