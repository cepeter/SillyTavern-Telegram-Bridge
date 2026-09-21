"""Phase 7B1 persistence ordinary-import boundary tests."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).parents[1]



DATABASE_PUBLIC_FUNCTIONS = (
    "run_write_txn",
    "write_transaction",
    "optimize_database",
    "run_database_maintenance",
    "db_connect",
    "get_meta",
    "set_meta",
    "record_failed_turn",
    "latest_failed_turn",
    "clear_failed_turn",
    "committed_assistant_for_message",
    "bind_panel_session",
    "panel_session_for_message",
    "panel_owner_for_message",
    "operation_phase",
    "set_operation_phase",
    "begin_operation",
    "operation_was_applied",
    "record_operation",
    "enqueue_job",
    "job_actor_id",
    "mark_job_scheduled",
    "mark_job_running",
    "finish_job",
    "recover_jobs",
    "task_model_key",
    "task_model_for_session",
    "set_task_model",
    "model_target_selection_key",
    "set_model_target_selection",
    "get_model_target_selection",
    "clear_model_target_selection",
    "get_generation_settings",
    "update_generation_settings",
    "preset_names",
    "save_generation_preset",
    "load_generation_preset",
    "delete_generation_preset",
    "format_generation_settings",
    "parse_generation_setting",
    "sync_transcript_hash",
    "ensure_sync_binding",
)

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


    def test_database_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.database as database\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert database._DB_WRITE_LOCK is not None\n"
            "assert database._DB_CONNECTION_GATE is not None\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_database_has_no_obsolete_schema_ready_fixture_state(self):
        import bridge.database as database

        self.assertFalse(hasattr(database, "_DB_SCHEMA_LOCK"))
        self.assertFalse(hasattr(database, "_DB_SCHEMA_READY"))
        self.assertFalse(hasattr(database, "_DB_SCHEMA_READY_PATHS"))

    def test_database_source_has_no_exec_state_preservation_or_runtime_dependency(self):
        source = (
            REPO_ROOT / "bridge" / "database.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn('globals().get("_DB_WRITE_LOCK")', source)
        self.assertNotIn("import bridge.runtime", source)
        self.assertNotIn("from bridge.runtime import", source)
        self.assertNotIn("import bridge.common", source)
        self.assertNotIn("from bridge.common import", source)


    def test_database_default_path_follows_canonical_config(self):
        import bridge.config as config
        import bridge.database as database

        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "default.sqlite3"
            with patch.object(config, "DB_FILE", expected):
                db = database.db_connect()
                try:
                    self.assertEqual(
                        Path(
                            db.execute("PRAGMA database_list").fetchone()[2]
                        ).resolve(),
                        expected.resolve(),
                    )
                finally:
                    db.close()

    def test_database_explicit_path_overrides_canonical_default(self):
        import bridge.config as config
        import bridge.database as database

        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "configured.sqlite3"
            explicit = Path(directory) / "explicit.sqlite3"
            with patch.object(config, "DB_FILE", configured):
                db = database.db_connect(explicit)
                try:
                    self.assertEqual(
                        Path(
                            db.execute("PRAGMA database_list").fetchone()[2]
                        ).resolve(),
                        explicit.resolve(),
                    )
                finally:
                    db.close()

    def test_task_model_default_reads_current_canonical_config(self):
        import bridge.config as config
        import bridge.database as database

        class EmptyMetaDb:
            def execute(self, *_args, **_kwargs):
                class Cursor:
                    def fetchone(self):
                        return None
                return Cursor()

        session = {"session_id": "s", "model_id": ""}
        with patch.object(config, "DEFAULT_MODEL", "patched::model"):
            self.assertEqual(
                database.task_model_for_session(
                    EmptyMetaDb(),
                    "chat",
                    session,
                    "summary",
                ),
                "patched::model",
            )


    def test_database_and_config_remain_ordinary_after_final_cutover(self):
        self.assertFalse((REPO_ROOT / "bridge" / "runtime_loader.py").exists())

    def test_database_maintenance_uses_current_canonical_default_path(self):
        import bridge.config as config
        import bridge.database as database

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.sqlite3"
            with patch.object(config, "DB_FILE", path):
                db = database.db_connect()
                try:
                    db.execute(
                        "CREATE TABLE phase7b1_churn("
                        "id INTEGER PRIMARY KEY, payload TEXT)"
                    )
                    db.executemany(
                        "INSERT INTO phase7b1_churn(payload) VALUES(?)",
                        [("x" * 2000,) for _ in range(200)],
                    )
                    db.commit()
                    db.execute("DELETE FROM phase7b1_churn")
                    db.commit()
                finally:
                    db.close()

                database.run_database_maintenance(
                    vacuum_freelist_threshold=1,
                )
                self.assertTrue(path.is_file())


    def test_database_and_config_have_direct_canonical_ownership(self):
        import bridge.main  # completes transitional ordinary dependency bindings
        import bridge.config as config
        import bridge.database as database
        import bridge.help as help_module
        import bridge.sync_core as sync_core

        actual_public_functions = {
            name
            for name, value in vars(database).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", None) == "bridge.database"
        }
        self.assertEqual(actual_public_functions, set(DATABASE_PUBLIC_FUNCTIONS))
        self.assertIs(
            database.run_write_txn.__globals__["_DB_WRITE_LOCK"],
            database._DB_WRITE_LOCK,
        )
        self.assertIs(
            database.db_connect.__globals__["_DB_CONNECTION_GATE"],
            database._DB_CONNECTION_GATE,
        )
        self.assertIs(sync_core.GENERATION_DEFAULTS, config.GENERATION_DEFAULTS)
        self.assertIs(help_module.REASONING_LEVELS, config.REASONING_LEVELS)

if __name__ == "__main__":
    unittest.main()
