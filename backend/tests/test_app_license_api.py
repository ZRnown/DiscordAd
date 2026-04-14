import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class LicenseApiTests(unittest.TestCase):
    def _load_app_module(self):
        config_module = importlib.import_module('config')
        importlib.reload(config_module)
        app_module = importlib.import_module('app')
        return importlib.reload(app_module)

    def test_activate_license_allows_empty_key_when_license_not_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                backend_app = self._load_app_module()
                original_required = getattr(backend_app.config, 'LICENSE_REQUIRED', True)
                backend_app.config.LICENSE_REQUIRED = False

                try:
                    with backend_app.app.test_request_context(
                        '/api/license/activate',
                        method='POST',
                        json={}
                    ):
                        response = backend_app.activate_license_api()
                finally:
                    backend_app.config.LICENSE_REQUIRED = original_required

                if isinstance(response, tuple):
                    response = response[0]
                payload = response.get_json()
                self.assertTrue(payload['success'])
                self.assertEqual(payload['message'], '当前版本无需激活')
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)
