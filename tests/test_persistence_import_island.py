"""Phase 7B1 persistence ordinary-import boundary tests."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).parents[1]


class PersistenceImportIslandTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_config_imports_without_runtime_common_or_database(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.config as config\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert 'bridge.database' not in sys.modules\n"
            "assert config.DEFAULT_MAX_TOKENS == 1800\n"
            "assert config.PENDING_SETTINGS_TTL_SECONDS == 600\n"
            "assert config.REASONING_LEVELS == {"
            "'none': 0, 'low': 1024, 'medium': 4096, "
            "'high': 8192, 'max': 16384}\n"
            "assert config.GENERATION_DEFAULTS['max_tokens'] == 1800\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_common_reexports_canonical_mutable_defaults(self):
        completed = self._run_python(
            "import bridge.config as config\n"
            "import bridge.common as common\n"
            "assert common.BRIDGE_HOME == config.BRIDGE_HOME\n"
            "assert common.DB_FILE == config.DB_FILE\n"
            "assert common.DEFAULT_MODEL == config.DEFAULT_MODEL\n"
            "assert common.DEFAULT_MAX_TOKENS == config.DEFAULT_MAX_TOKENS\n"
            "assert common.PENDING_SETTINGS_TTL_SECONDS == "
            "config.PENDING_SETTINGS_TTL_SECONDS\n"
            "assert common.GENERATION_DEFAULTS is config.GENERATION_DEFAULTS\n"
            "assert common.REASONING_LEVELS is config.REASONING_LEVELS\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
