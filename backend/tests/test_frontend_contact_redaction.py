import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
APP_PATH = ROOT_DIR / 'src' / 'App.tsx'
TAURI_CONFIG_PATH = ROOT_DIR / 'src-tauri' / 'tauri.conf.json'


class FrontendContactRedactionTests(unittest.TestCase):
    def test_app_sidebar_does_not_expose_private_support_contacts(self):
        source = '\n'.join([
            APP_PATH.read_text(encoding='utf-8'),
            TAURI_CONFIG_PATH.read_text(encoding='utf-8'),
        ])

        private_contacts = [
            'OceanSeaWang',
            'zrnown',
            '微信:',
            'Discord: zrnown',
        ]

        for contact in private_contacts:
            with self.subTest(contact=contact):
                self.assertNotIn(contact, source)
