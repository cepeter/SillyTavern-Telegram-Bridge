import tempfile
import unittest
from pathlib import Path

import subprocess
from dependency_patch import dependency_module

_m_update = dependency_module("bridge.update")


class UpdatePanelTests(unittest.TestCase):
    def test_update_panel_uses_installed_version_and_release_notes(self):
        text = _m_update.update_menu_text("0.2.007", "0.2.008", "Added safer updates.")
        self.assertIn("Installed: v0.2.007", text)
        self.assertIn("Latest: v0.2.008", text)
        self.assertIn("Added safer updates.", text)

    def test_latest_panel_has_no_confirm_action(self):
        text = _m_update.update_menu_text("0.2.008", "0.2.008", "ignored")
        self.assertIn("Status: Already latest", text)
        self.assertNotIn("Release notes:", text)

    def test_unreleased_panel_shows_release_base_and_local_state(self):
        text = _m_update.update_menu_text("0.2.013", "0.2.013", "ignored", unreleased=True)
        self.assertIn("Installed: v0.2.013 (unreleased local changes)", text)
        self.assertIn("Status: Local unreleased changes", text)
        self.assertNotIn("Confirm update", text)

    def test_unreleased_changelog_uses_latest_released_heading(self):
        old_live = _m_update.UPDATE_LIVE_DIR
        old_repo = _m_update.UPDATE_REPO_DIR
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live"
            repo = root / "repo"
            live.mkdir()
            repo.mkdir()
            (live / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.013] - 2026-09-18\n", encoding="utf-8")
            _m_update.UPDATE_LIVE_DIR = live
            _m_update.UPDATE_REPO_DIR = repo
            try:
                self.assertEqual(_m_update.installed_bridge_version(), "0.2.013")
                self.assertTrue(_m_update.installed_bridge_has_unreleased())
            finally:
                _m_update.UPDATE_LIVE_DIR = old_live
                _m_update.UPDATE_REPO_DIR = old_repo

    def test_update_noop_skips_subprocess_when_latest(self):
        old_latest = _m_update.latest_bridge_release
        old_run = subprocess.run
        calls = []
        _m_update.latest_bridge_release = lambda: (_m_update.installed_bridge_version(), "")
        subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            self.assertIn("Already latest", _m_update._run_update())
        finally:
            _m_update.latest_bridge_release = old_latest
            subprocess.run = old_run
        self.assertEqual(calls, [])

    def test_update_refuses_dirty_repository_without_side_effect(self):
        old_repo = _m_update.UPDATE_REPO_DIR
        old_latest = _m_update.latest_bridge_release
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\n", encoding="utf-8")
            _m_update.UPDATE_REPO_DIR = repo
            _m_update.latest_bridge_release = lambda: ("0.0.2", "")
            original_run = subprocess.run
            subprocess.run = lambda *_args, **_kwargs: type("Result", (), {"stdout": " M local.py\n"})()
            try:
                self.assertIn("uncommitted", _m_update._run_update())
            finally:
                subprocess.run = original_run
                _m_update.latest_bridge_release = old_latest
                _m_update.UPDATE_REPO_DIR = old_repo

    def test_update_reports_missing_source_checkout(self):
        old_repo = _m_update.UPDATE_REPO_DIR
        old_latest = _m_update.latest_bridge_release
        _m_update.UPDATE_REPO_DIR = Path("/tmp/not-a-bridge-checkout")
        _m_update.latest_bridge_release = lambda: ("0.0.2", "")
        try:
            self.assertIn("source checkout not found", _m_update._run_update())
        finally:
            _m_update.latest_bridge_release = old_latest
            _m_update.UPDATE_REPO_DIR = old_repo

    def test_update_reports_git_stderr_without_raising(self):
        old_repo = _m_update.UPDATE_REPO_DIR
        old_latest = _m_update.latest_bridge_release
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\n", encoding="utf-8")
            _m_update.UPDATE_REPO_DIR = repo
            _m_update.latest_bridge_release = lambda: ("0.0.2", "")
            original_run = subprocess.run

            def fake_run(command, **kwargs):
                if command[1:3] == ["status", "--porcelain"]:
                    return type("Result", (), {"stdout": ""})()
                raise subprocess.CalledProcessError(128, command, stderr="fatal: remote unavailable")

            subprocess.run = fake_run
            try:
                result = _m_update._run_update()
            finally:
                subprocess.run = original_run
                _m_update.latest_bridge_release = old_latest
                _m_update.UPDATE_REPO_DIR = old_repo
        self.assertIn("remote unavailable", result)


    def test_update_fetches_and_merges_exact_release_tag(self):
        old_repo = _m_update.UPDATE_REPO_DIR
        old_live = _m_update.UPDATE_LIVE_DIR
        old_latest = _m_update.latest_bridge_release
        old_run = subprocess.run
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            live = root / "live"
            (repo / ".git").mkdir(parents=True)
            (repo / "CHANGELOG.md").write_text("## [0.0.1]\\n", encoding="utf-8")
            _m_update.UPDATE_REPO_DIR = repo
            _m_update.UPDATE_LIVE_DIR = live
            _m_update.latest_bridge_release = lambda: ("0.0.2", "")

            def fake_run(command, **kwargs):
                calls.append(command)
                return type("Result", (), {"stdout": "", "stderr": ""})()

            subprocess.run = fake_run
            try:
                _m_update._run_update()
            finally:
                subprocess.run = old_run
                _m_update.latest_bridge_release = old_latest
                _m_update.UPDATE_LIVE_DIR = old_live
                _m_update.UPDATE_REPO_DIR = old_repo

        fetched_ref = "refs/bridge-release/v0.0.2"
        git_bin = _m_update._resolve_command("git")
        rsync_bin = _m_update._resolve_command("rsync")
        cp_bin = _m_update._resolve_command("cp")
        systemctl_bin = _m_update._resolve_command("systemctl")
        self.assertIn(
            [
                git_bin,
                "fetch",
                "--force",
                "--no-tags",
                _m_update.UPDATE_CANONICAL_GIT_URL,
                f"refs/tags/v0.0.2:{fetched_ref}",
            ],
            calls,
        )
        self.assertIn([git_bin, "merge", "--ff-only", fetched_ref], calls)
        self.assertIn([rsync_bin, "-a", "--delete", f"{repo}/bridge/", f"{live}/bridge/"], calls)
        self.assertIn([cp_bin, str(repo / "sillytavern_telegram_bridge.py"), str(live / "sillytavern_telegram_bridge.py")], calls)
        self.assertIn([cp_bin, str(repo / "CHANGELOG.md"), str(live / "CHANGELOG.md")], calls)
        self.assertIn([systemctl_bin, "--user", "restart", "sillytavern-telegram.service"], calls)
        self.assertNotIn(["git", "fetch", "origin", "main"], calls)
        self.assertNotIn(["git", "merge", "--ff-only", "origin/main"], calls)


if __name__ == "__main__":
    unittest.main()
