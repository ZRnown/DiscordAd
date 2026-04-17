import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class StorageApiTests(unittest.TestCase):
    def _load_modules(self):
        config_module = importlib.import_module('config')
        importlib.reload(config_module)
        app_module = importlib.import_module('app')
        return config_module, importlib.reload(app_module)

    def test_get_storage_returns_active_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                _, backend_app = self._load_modules()
                with backend_app.app.test_client() as client:
                    response = client.get('/api/storage')

                payload = response.get_json()
                self.assertTrue(payload['success'])
                self.assertIn('data_dir', payload)
                self.assertIn('database_path', payload)
                self.assertIn('content_images_dir', payload)
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)

    def test_update_storage_can_migrate_existing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                config_module, backend_app = self._load_modules()
                source_dir = Path(config_module.config.DATA_DIR)
                target_dir = Path(temp_dir) / 'portable-data'

                source_dir.mkdir(parents=True, exist_ok=True)
                (source_dir / 'content_images').mkdir(parents=True, exist_ok=True)
                (source_dir / 'content_images' / 'demo.txt').write_text('hello', encoding='utf-8')
                (source_dir / 'license.json').write_text(
                    json.dumps({'license_key': 'TEST'}),
                    encoding='utf-8'
                )
                with sqlite3.connect(source_dir / 'metadata.db') as conn:
                    conn.execute('CREATE TABLE demo (value TEXT)')
                    conn.execute("INSERT INTO demo(value) VALUES ('ok')")
                    conn.commit()

                with patch.object(backend_app, 'get_task_status', return_value={'is_running': False, 'is_paused': False}):
                    with backend_app.app.test_client() as client:
                        response = client.post(
                            '/api/storage',
                            json={'data_dir': str(target_dir), 'migrate': True}
                        )

                payload = response.get_json()
                self.assertTrue(payload['success'])
                self.assertEqual(
                    os.path.realpath(payload['data_dir']),
                    os.path.realpath(target_dir)
                )
                self.assertTrue((target_dir / 'content_images' / 'demo.txt').exists())
                self.assertTrue((target_dir / 'license.json').exists())
                with sqlite3.connect(target_dir / 'metadata.db') as conn:
                    row = conn.execute('SELECT value FROM demo').fetchone()
                self.assertEqual(row[0], 'ok')
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)

    def test_update_storage_rejects_changes_while_sender_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_home = os.environ.get('HOME')
            old_xdg = os.environ.get('XDG_CONFIG_HOME')
            os.environ['HOME'] = temp_dir
            os.environ.pop('XDG_CONFIG_HOME', None)

            try:
                _, backend_app = self._load_modules()
                with patch.object(backend_app, 'get_task_status', return_value={'is_running': True, 'is_paused': False}):
                    with backend_app.app.test_client() as client:
                        response = client.post(
                            '/api/storage',
                            json={'data_dir': str(Path(temp_dir) / 'next-data'), 'migrate': False}
                        )

                payload = response.get_json()
                self.assertFalse(payload['success'])
                self.assertEqual(response.status_code, 409)
            finally:
                if old_home is not None:
                    os.environ['HOME'] = old_home
                else:
                    os.environ.pop('HOME', None)
                if old_xdg is not None:
                    os.environ['XDG_CONFIG_HOME'] = old_xdg
                else:
                    os.environ.pop('XDG_CONFIG_HOME', None)
