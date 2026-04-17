import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT_DIR / '.github' / 'workflows' / 'build.yml'


class GithubActionsWorkflowTests(unittest.TestCase):
    def test_release_build_does_not_delegate_to_tauri_action(self):
        workflow = WORKFLOW_PATH.read_text(encoding='utf-8')

        self.assertNotIn('uses: tauri-apps/tauri-action@v0', workflow)
        self.assertIn('  release:', workflow)
        self.assertIn('uses: actions/download-artifact@v4', workflow)
        self.assertIn('uses: softprops/action-gh-release@v2', workflow)
