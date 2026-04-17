import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
APP_PATH = ROOT_DIR / 'src' / 'App.tsx'


class FrontendSettingsNavigationTests(unittest.TestCase):
    def test_app_navigation_exposes_settings_page(self):
        source = APP_PATH.read_text(encoding='utf-8')

        self.assertIn("to=\"/settings\"", source)
        self.assertIn("<span>数据设置</span>", source)
        self.assertIn("path=\"/settings\"", source)
