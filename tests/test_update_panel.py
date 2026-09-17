import tempfile
import unittest
from pathlib import Path

import bridge.runtime as rt


class UpdatePanelTests(unittest.TestCase):
    def test_update_panel_uses_installed_version_and_release_notes(self):
        text = rt.update_menu_text("0.2.007", "0.2.008", "Added safer updates.")
        self.assertIn("Installed: v0.2.007", text)
        self.assertIn("Latest: v0.2.008", text)
        self.assertIn("Added safer updates.", text)

    def test_update_refuses_dirty_repository_without_side_effect(self):
        old_repo = rt.UPDATE_REPO_DIR
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\n", encoding="utf-8")
            rt.UPDATE_REPO_DIR = repo
            original_run = rt.subprocess.run
            rt.subprocess.run = lambda *_args, **_kwargs: type("Result", (), {"stdout": " M local.py\n"})()
            try:
                self.assertIn("uncommitted", rt._run_update())
            finally:
                rt.subprocess.run = original_run
                rt.UPDATE_REPO_DIR = old_repo


if __name__ == "__main__":
    unittest.main()
