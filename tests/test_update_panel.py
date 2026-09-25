from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.update as _m_update


class UpdatePanelTests(SettingsTestCase):
    def test_update_live_dir_defaults_under_bridge_home(self):
        from bridge.settings import load_app_settings

        self.assertEqual(
            load_app_settings({}, home=Path.home()).update_live_dir,
            Path.home() / ".local/share/sillytavern-telegram/live",
        )
        self.assertEqual(
            load_app_settings({"SILLYTAVERN_BRIDGE_HOME": "/tmp/bridge-home"}, home=Path.home()).update_live_dir,
            Path("/tmp/bridge-home/live"),
        )
        settings = load_app_settings(
            {"SILLYTAVERN_BRIDGE_HOME": "/tmp/bridge-home", "SILLYTAVERN_LIVE_BRIDGE_DIR": "/tmp/custom-live"},
            home=Path.home(),
        )
        self.assertEqual(settings.update_live_dir, Path("/tmp/custom-live"))

    def test_user_systemd_template_uses_writable_update_staging(self):
        root = Path(__file__).parents[1]
        unit = (root / "systemd" / "sillytavern-telegram.service.example").read_text(encoding="utf-8")
        self.assertIn(
            "Environment=SILLYTAVERN_LIVE_BRIDGE_DIR=%h/.local/share/sillytavern-telegram/live",
            unit,
        )
        self.assertIn(
            "ReadWritePaths=%h/sillytavern-telegram-bridge %h/.local/share/sillytavern-telegram",
            unit,
        )

    def test_empty_unreleased_section_is_not_local_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CHANGELOG.md"
            path.write_text(
                "# Changelog\n\n## [Unreleased]\n\n## [0.2.023] - 2026-09-24\n",
                encoding="utf-8",
            )
            self.assertFalse(_m_update._changelog_has_unreleased(path))

    def test_empty_unreleased_subheadings_are_not_local_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CHANGELOG.md"
            path.write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Added\n\n### Fixed\n\n## [0.2.023] - 2026-09-24\n",
                encoding="utf-8",
            )
            self.assertFalse(_m_update._changelog_has_unreleased(path))

    def test_populated_unreleased_section_is_local_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CHANGELOG.md"
            path.write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- Fixed update status.\n\n## [0.2.023] - 2026-09-24\n",
                encoding="utf-8",
            )
            self.assertTrue(_m_update._changelog_has_unreleased(path))

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

    def test_empty_unreleased_changelog_uses_latest_released_heading(self):
        old_live = self.app_settings_builder.update_live_dir
        old_repo = self.app_settings_builder.update_repo_dir
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live"
            repo = root / "repo"
            live.mkdir()
            repo.mkdir()
            (live / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.013] - 2026-09-18\n", encoding="utf-8")
            self.app_settings_builder.update_live_dir = live
            self.app_settings_builder.update_repo_dir = repo
            try:
                self.assertEqual(
                    _m_update.installed_bridge_version(app_settings=self.app_settings_builder.build()), "0.2.013"
                )
                self.assertFalse(
                    _m_update.installed_bridge_has_unreleased(app_settings=self.app_settings_builder.build())
                )
            finally:
                self.app_settings_builder.update_live_dir = old_live
                self.app_settings_builder.update_repo_dir = old_repo

    def test_update_noop_skips_subprocess_when_latest(self):
        from unittest.mock import patch

        with (
            patch.object(_m_update, "installed_bridge_version", return_value="0.2.024"),
            patch.object(_m_update, "latest_bridge_release", return_value=("0.2.024", "")),
            patch.object(_m_update, "apply_update") as apply,
        ):
            result = _m_update._run_update(app_settings=self.app_settings_builder.build())
        self.assertIs(result.status, _m_update.UpdateStatus.ALREADY_LATEST)
        apply.assert_not_called()
        self.assertIn("Already latest", _m_update.format_update_outcome(result))

    def test_update_failures_are_structured_and_redacted(self):
        result = _m_update.UpdateOutcome(_m_update.UpdateStatus.REFUSED, code="signature")
        self.assertIn("independently trusted", _m_update.format_update_outcome(result))

    def test_partial_activation_does_not_claim_rollback(self):
        result = _m_update.UpdateOutcome(_m_update.UpdateStatus.FAILED, code="activate", source_changed=True)
        text = _m_update.format_update_outcome(result)
        self.assertIn("source checkout advanced", text)
        self.assertNotIn("were not replaced", text)


if __name__ == "__main__":
    unittest.main()
