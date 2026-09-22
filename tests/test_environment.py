from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bridge.environment import (
    DEFAULT_BRIDGE_HOME,
    bootstrap_environment,
    environment_file,
    load_environment_file,
)


class EnvironmentBootstrapTests(unittest.TestCase):
    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            target = {}
            path = Path(directory) / "missing.env"
            load_environment_file(path, target)
            self.assertEqual(target, {})

    def test_parses_supported_assignments_without_overriding_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "\ufeff"
                "# comment\n"
                "PLAIN=value\n"
                "export SPACED =  around value  \n"
                "SINGLE='one two'\n"
                'DOUBLE="three four"\n'
                "EQUALS=a=b=c\n"
                "KEEP=file-value\n",
                encoding="utf-8",
            )
            target = {"KEEP": "process-value"}
            load_environment_file(path, target)

        self.assertEqual(target["PLAIN"], "value")
        self.assertEqual(target["SPACED"], "around value")
        self.assertEqual(target["SINGLE"], "one two")
        self.assertEqual(target["DOUBLE"], "three four")
        self.assertEqual(target["EQUALS"], "a=b=c")
        self.assertEqual(target["KEEP"], "process-value")

    def test_rejects_malformed_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("NOT_AN_ASSIGNMENT\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid environment assignment"):
                load_environment_file(path, {})

    def test_rejects_invalid_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("BAD-NAME=value\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid environment name"):
                load_environment_file(path, {})

    def test_rejects_unterminated_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('BROKEN="value\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unterminated quoted value"):
                load_environment_file(path, {})

    def test_rejects_directory_as_environment_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                load_environment_file(Path(directory), {})

    def test_custom_environment_file_wins_over_default_location(self):
        target = {"SILLYTAVERN_ENV_FILE": "~/custom.env"}
        self.assertEqual(
            environment_file(target),
            Path("~/custom.env").expanduser(),
        )

    def test_bridge_home_does_not_change_bootstrap_default(self):
        target = {"SILLYTAVERN_BRIDGE_HOME": "/tmp/other-home"}
        self.assertEqual(
            environment_file(target),
            DEFAULT_BRIDGE_HOME / ".env",
        )

    def test_bootstrap_loads_resolved_environment_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.env"
            path.write_text("BOOTSTRAPPED=yes\n", encoding="utf-8")
            target = {"SILLYTAVERN_ENV_FILE": str(path)}

            resolved = bootstrap_environment(target)

        self.assertEqual(resolved, path)
        self.assertEqual(target["BOOTSTRAPPED"], "yes")


if __name__ == "__main__":
    unittest.main()
