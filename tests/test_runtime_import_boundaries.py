"""Runtime import-boundary regression tests."""

import subprocess
import sys
import unittest
from pathlib import Path

from settings_test_support import SettingsTestCase

REPO_ROOT = Path(__file__).parents[1]


class RuntimeImportBoundaryTests(SettingsTestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_schema_owns_processed_update_retention_default(self):
        self.assertFalse((REPO_ROOT / "bridge" / "runtime_defaults.py").exists())
        completed = self._run_python(
            "import sys\n"
            "import bridge.schema as schema\n"
            "assert schema.PROCESSED_UPDATE_RETENTION_SECONDS == 30 * 86400\n"
            "assert 'bridge.runtime_defaults' not in sys.modules\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_schema_import_does_not_import_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.schema as schema\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert [m.version for m in schema.SCHEMA_MIGRATIONS] == [1]\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_performance_and_native_cache_import_without_runtime(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.performance\n"
            "import bridge.native_cache\n"
            "assert 'bridge.runtime' not in sys.modules\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_boundary_modules_do_not_import_bridge_runtime(self):
        for filename in (
            "performance.py",
            "native_cache.py",
            "schema.py",
        ):
            with self.subTest(filename=filename):
                source = (REPO_ROOT / "bridge" / filename).read_text(encoding="utf-8")
                self.assertNotIn(
                    "import bridge.runtime",
                    source,
                )
                self.assertNotIn(
                    "from bridge.runtime import",
                    source,
                )

    def test_runtime_boundary_objects_are_owned_directly(self):
        import bridge.message_commands as message_commands
        import bridge.native_cache as native_cache
        import bridge.performance as performance
        import bridge.schema as schema

        self.assertTrue(callable(performance.performance_enabled))
        self.assertTrue(callable(performance.perf_span))
        self.assertIs(message_commands.timed_call, performance.timed_call)
        self.assertTrue(callable(native_cache.cached_json))
        self.assertTrue(callable(native_cache.cached_png_metadata))
        self.assertTrue(callable(native_cache.cached_text))
        self.assertTrue(schema.SCHEMA_MIGRATIONS)
        self.assertTrue(callable(schema.initialize_database_schema))

        key = "phase-7a-single-cache-state"
        native_cache._TEXT_CACHE.pop(key, None)
        try:
            first = native_cache.cached_text(key, lambda: "first")
            second = native_cache.cached_text(key, lambda: "second")
        finally:
            native_cache._TEXT_CACHE.pop(key, None)
        self.assertEqual(first, "first")
        self.assertEqual(second, "first")


if __name__ == "__main__":
    unittest.main()
