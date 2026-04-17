import json
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import license_manager


class LicenseManagerDeviceIdTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.license_file = Path(self.tmpdir.name) / 'license.json'
        self.device_id_file = Path(self.tmpdir.name) / 'device_id.txt'
        self.original_get_license_file = license_manager.get_license_file
        self.original_get_device_id_file = license_manager.get_device_id_file
        license_manager.get_license_file = lambda: str(self.license_file)
        license_manager.get_device_id_file = lambda: str(self.device_id_file)

    def tearDown(self):
        license_manager.get_license_file = self.original_get_license_file
        license_manager.get_device_id_file = self.original_get_device_id_file
        self.tmpdir.cleanup()

    def test_generate_hwid_prefers_cached_device_id(self):
        cached_hwid = 'ABCDEF0123456789ABCDEF0123456789'
        self.device_id_file.write_text(cached_hwid, encoding='utf-8')

        self.assertEqual(license_manager.generate_hwid(), cached_hwid)

    def test_generate_hwid_migrates_saved_license_hwid_into_device_cache(self):
        saved_hwid = '1234567890ABCDEF1234567890ABCDEF'
        self.license_file.write_text(
            json.dumps({
                'license_key': 'SAMPLE-KEY',
                'hwid': saved_hwid,
                'days': -1,
                'activated_at': '2026-03-22T00:00:00'
            }),
            encoding='utf-8'
        )

        self.assertEqual(license_manager.generate_hwid(), saved_hwid)
        self.assertTrue(self.device_id_file.exists())
        self.assertEqual(self.device_id_file.read_text(encoding='utf-8').strip(), saved_hwid)

    def test_validate_local_license_allows_free_mode_without_saved_license(self):
        original_required = getattr(license_manager.config, 'LICENSE_REQUIRED', True)
        license_manager.config.LICENSE_REQUIRED = False

        try:
            activated, payload = license_manager.validate_local_license()
        finally:
            license_manager.config.LICENSE_REQUIRED = original_required

        self.assertTrue(activated)
        self.assertEqual(payload['mode'], 'free')
        self.assertEqual(payload['days'], -1)
