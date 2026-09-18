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

    def test_latest_panel_has_no_confirm_action(self):
        text = rt.update_menu_text("0.2.008", "0.2.008", "ignored")
        self.assertIn("Status: Already latest", text)
        self.assertNotIn("Release notes:", text)

    def test_update_noop_skips_subprocess_when_latest(self):
        old_latest = rt.latest_bridge_release
        old_run = rt.subprocess.run
        calls = []
        rt.latest_bridge_release = lambda: (rt.installed_bridge_version(), "")
        rt.subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            self.assertIn("Already latest", rt._run_update())
        finally:
            rt.latest_bridge_release = old_latest
            rt.subprocess.run = old_run
        self.assertEqual(calls, [])

    def test_update_refuses_dirty_repository_without_side_effect(self):
        old_repo = rt.UPDATE_REPO_DIR
        old_latest = rt.latest_bridge_release
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\n", encoding="utf-8")
            rt.UPDATE_REPO_DIR = repo
            rt.latest_bridge_release = lambda: ("0.0.2", "")
            original_run = rt.subprocess.run
            rt.subprocess.run = lambda *_args, **_kwargs: type("Result", (), {"stdout": " M local.py\n"})()
            try:
                self.assertIn("uncommitted", rt._run_update())
            finally:
                rt.subprocess.run = original_run
                rt.latest_bridge_release = old_latest
                rt.UPDATE_REPO_DIR = old_repo

    def test_update_reports_missing_source_checkout(self):
        old_repo = rt.UPDATE_REPO_DIR
        old_latest = rt.latest_bridge_release
        rt.UPDATE_REPO_DIR = Path("/tmp/not-a-bridge-checkout")
        rt.latest_bridge_release = lambda: ("0.0.2", "")
        try:
            self.assertIn("source checkout not found", rt._run_update())
        finally:
            rt.latest_bridge_release = old_latest
            rt.UPDATE_REPO_DIR = old_repo

    def test_update_reports_git_stderr_without_raising(self):
        old_repo = rt.UPDATE_REPO_DIR
        old_latest = rt.latest_bridge_release
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\n", encoding="utf-8")
            rt.UPDATE_REPO_DIR = repo
            rt.latest_bridge_release = lambda: ("0.0.2", "")
            original_run = rt.subprocess.run

            def fake_run(command, **kwargs):
                if command[1:3] == ["status", "--porcelain"]:
                    return type("Result", (), {"stdout": ""})()
                raise rt.subprocess.CalledProcessError(128, command, stderr="fatal: remote unavailable")

            rt.subprocess.run = fake_run
            try:
                result = rt._run_update()
            finally:
                rt.subprocess.run = original_run
                rt.latest_bridge_release = old_latest
                rt.UPDATE_REPO_DIR = old_repo
        self.assertIn("remote unavailable", result)


    def test_update_fetches_and_merges_exact_release_tag(self):
        old_repo = rt.UPDATE_REPO_DIR
        old_live = rt.UPDATE_LIVE_DIR
        old_latest = rt.latest_bridge_release
        old_run = rt.subprocess.run
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            live = root / "live"
            (repo / ".git").mkdir(parents=True)
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\\n", encoding="utf-8")
            rt.UPDATE_REPO_DIR = repo
            rt.UPDATE_LIVE_DIR = live
            rt.latest_bridge_release = lambda: ("0.0.2", "")

            def fake_run(command, **kwargs):
                calls.append(command)
                return type("Result", (), {"stdout": "", "stderr": ""})()

            rt.subprocess.run = fake_run
            try:
                rt._run_update()
            finally:
                rt.subprocess.run = old_run
                rt.latest_bridge_release = old_latest
                rt.UPDATE_LIVE_DIR = old_live
                rt.UPDATE_REPO_DIR = old_repo

        self.assertIn(["git", "fetch", "origin", "tag", "v0.0.2"], calls)
        self.assertIn(["git", "merge", "--ff-only", "v0.0.2"], calls)
        self.assertNotIn(["git", "fetch", "origin", "main"], calls)
        self.assertNotIn(["git", "merge", "--ff-only", "origin/main"], calls)


if __name__ == "__main__":
    unittest.main()
