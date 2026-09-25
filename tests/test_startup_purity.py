from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]


class StartupPurityTests(unittest.TestCase):
    def _run(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_importing_common_does_not_create_log_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "bridge-home"
            source = f"""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

root = Path({str(root)!r})
os.environ["SILLYTAVERN_BRIDGE_HOME"] = str(root)

import bridge.common as common

assert not (root / "logs").exists()
assert common._GENERATION_EXECUTOR is None
assert common._UTILITY_EXECUTOR is None
assert not any(
    isinstance(handler, RotatingFileHandler)
    for handler in logging.getLogger().handlers
)
"""
            completed = self._run(source)

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_configure_logging_is_idempotent_for_same_target(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "logs" / "bridge.log"
            source = f"""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import stat

import bridge.common as common

assert hasattr(common, "configure_logging")
target = Path({str(log_file)!r})
common.configure_logging(target)
common.configure_logging(target)

matching = [
    handler
    for handler in logging.getLogger().handlers
    if isinstance(handler, RotatingFileHandler)
    and Path(handler.baseFilename).resolve() == target.resolve()
]
assert len(matching) == 1
assert target.exists()
if os.name == "posix":
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode & 0o077 == 0
"""
            completed = self._run(source)

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_configure_logging_does_not_create_executors(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "bridge.log"
            source = f"""
from pathlib import Path
import bridge.common as common

common.configure_logging(Path({str(log_file)!r}))
assert common._GENERATION_EXECUTOR is None
assert common._UTILITY_EXECUTOR is None
"""
            completed = self._run(source)

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_configure_logging_does_not_install_duplicate_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "bridge.log"
            source = f"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import bridge.common as common

assert hasattr(common, "configure_logging")
target = Path({str(log_file)!r})
common.configure_logging(target)
before = tuple(logging.getLogger().handlers)
common.configure_logging(target)
after = tuple(logging.getLogger().handlers)

assert before == after
assert sum(
    isinstance(handler, RotatingFileHandler)
    and Path(handler.baseFilename).resolve() == target.resolve()
    for handler in after
) == 1
"""
            completed = self._run(source)

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
