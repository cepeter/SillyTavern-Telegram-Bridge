"""Phase 7A ordinary-import boundary regression tests."""

from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).parents[1]


class RuntimeImportIslandTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_runtime_defaults_is_ordinary_only_and_has_existing_retention_value(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.runtime_defaults as defaults\n"
            "assert defaults.PROCESSED_UPDATE_RETENTION_SECONDS == 30 * 86400\n"
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
            "assert [m.version for m in schema.SCHEMA_MIGRATIONS] == [1, 2, 3, 4]\n"
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


    def test_first_import_island_is_absent_from_runtime_stages(self):
        from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "performance.py",
                "native_cache.py",
                "schema.py",
                "runtime_defaults.py",
            }.isdisjoint(loaded)
        )

    def test_runtime_facade_uses_canonical_import_island_objects(self):
        import bridge.native_cache as native_cache
        import bridge.performance as performance
        import bridge.runtime as rt
        import bridge.schema as schema

        self.assertIs(rt.performance_enabled, performance.performance_enabled)
        self.assertIs(rt.perf_span, performance.perf_span)
        self.assertIs(rt.timed_call, performance.timed_call)
        self.assertIs(rt.cached_json, native_cache.cached_json)
        self.assertIs(rt.cached_png_metadata, native_cache.cached_png_metadata)
        self.assertIs(rt.cached_text, native_cache.cached_text)
        self.assertIs(rt.SCHEMA_MIGRATIONS, schema.SCHEMA_MIGRATIONS)
        self.assertIs(
            rt.initialize_database_schema,
            schema.initialize_database_schema,
        )

    def test_runtime_load_report_has_no_import_island_sources(self):
        import bridge.runtime as rt

        loaded = {
            entry["module"]
            for entry in rt.RUNTIME_LOAD_REPORT
        }
        self.assertTrue(
            {
                "performance.py",
                "native_cache.py",
                "schema.py",
                "runtime_defaults.py",
            }.isdisjoint(loaded)
        )


if __name__ == "__main__":
    unittest.main()
