import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
TAURI_CONFIG_PATH = ROOT_DIR / 'src-tauri' / 'tauri.conf.json'
TAURI_MAIN_PATH = ROOT_DIR / 'src-tauri' / 'src' / 'main.rs'


class DesktopSidecarContractTests(unittest.TestCase):
    def test_tauri_bundle_declares_backend_external_binary(self):
        config = json.loads(TAURI_CONFIG_PATH.read_text(encoding='utf-8'))
        external_bin = config['tauri']['bundle'].get('externalBin') or []

        self.assertIn('binaries/backend', external_bin)

    def test_tauri_main_spawns_backend_sidecar(self):
        source = TAURI_MAIN_PATH.read_text(encoding='utf-8')

        self.assertIn('Command::new_sidecar("backend")', source)
        self.assertIn('CommandEvent::Stdout', source)
