"""Phase 7A ordinary-import boundary regression tests."""\n\nfrom pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
