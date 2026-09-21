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
        from runtime_test_facade import runtime as rt
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


    def test_ordinary_modules_before_runtime_keep_canonical_identity(self):
        completed = self._run_python(
            "import bridge.performance as performance\n"
            "import bridge.native_cache as native_cache\n"
            "import bridge.schema as schema\n"
            "import bridge.runtime as rt\n"
            "assert rt.perf_span is performance.perf_span\n"
            "assert rt.cached_json is native_cache.cached_json\n"
            "assert rt.initialize_database_schema is schema.initialize_database_schema\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_before_ordinary_modules_keeps_canonical_identity(self):
        completed = self._run_python(
            "import bridge.runtime as rt\n"
            "import bridge.performance as performance\n"
            "import bridge.native_cache as native_cache\n"
            "import bridge.schema as schema\n"
            "assert rt.perf_span is performance.perf_span\n"
            "assert rt.cached_json is native_cache.cached_json\n"
            "assert rt.initialize_database_schema is schema.initialize_database_schema\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_and_native_cache_share_one_text_cache(self):
        import bridge.native_cache as native_cache
        import bridge.runtime as rt

        key = "phase-7a-single-cache-state"
        native_cache._TEXT_CACHE.pop(key, None)
        try:
            first = rt.cached_text(
                key,
                lambda: "from-runtime",
            )
            second = native_cache.cached_text(
                key,
                lambda: "from-module",
            )
        finally:
            native_cache._TEXT_CACHE.pop(key, None)

        self.assertEqual(first, "from-runtime")
        self.assertEqual(second, "from-runtime")
        self.assertIs(rt.cached_text, native_cache.cached_text)

    def test_migrated_modules_do_not_import_bridge_runtime(self):
        for filename in (
            "runtime_defaults.py",
            "performance.py",
            "native_cache.py",
            "schema.py",
        ):
            with self.subTest(filename=filename):
                source = (
                    REPO_ROOT
                    / "bridge"
                    / filename
                ).read_text(encoding="utf-8")
                self.assertNotIn(
                    "import bridge.runtime",
                    source,
                )
                self.assertNotIn(
                    "from bridge.runtime import",
                    source,
                )


if __name__ == "__main__":
    unittest.main()
