"""Persistence import-boundary regression tests."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from settings_test_support import SettingsTestCase

import bridge.model_selection as _owner_model_selection
import bridge.sqlite_store as _sqlite_store

REPO_ROOT = Path(__file__).parents[1]


PERSISTENCE_OWNERS = {
    "get_meta": "bridge.metadata",
    "set_meta": "bridge.metadata",
    "record_failed_turn": "bridge.failed_turns",
    "latest_failed_turn": "bridge.failed_turns",
    "clear_failed_turn": "bridge.failed_turns",
    "committed_assistant_for_message": "bridge.transcript_repository",
    "bind_panel_session": "bridge.panel_bindings",
    "panel_session_for_message": "bridge.panel_bindings",
    "panel_owner_for_message": "bridge.panel_bindings",
    "operation_phase": "bridge.operations",
    "set_operation_phase": "bridge.operations",
    "begin_operation": "bridge.operations",
    "operation_was_applied": "bridge.operations",
    "record_operation": "bridge.operations",
    "enqueue_job": "bridge.job_store",
    "job_actor_id": "bridge.job_store",
    "mark_job_scheduled": "bridge.job_store",
    "mark_job_running": "bridge.job_store",
    "finish_job": "bridge.job_store",
    "recover_jobs": "bridge.job_store",
    "task_model_key": "bridge.model_selection",
    "task_model_for_session": "bridge.model_selection",
    "set_task_model": "bridge.model_selection",
    "model_target_selection_key": "bridge.model_selection",
    "set_model_target_selection": "bridge.model_selection",
    "get_model_target_selection": "bridge.model_selection",
    "clear_model_target_selection": "bridge.model_selection",
    "get_generation_settings": "bridge.generation_settings",
    "update_generation_settings": "bridge.generation_settings",
    "preset_names": "bridge.generation_settings",
    "save_generation_preset": "bridge.generation_settings",
    "load_generation_preset": "bridge.generation_settings",
    "delete_generation_preset": "bridge.generation_settings",
    "format_generation_settings": "bridge.generation_settings_values",
    "parse_generation_setting": "bridge.generation_settings_values",
    "sync_transcript_hash": "bridge.sync_state",
    "ensure_sync_binding": "bridge.sync_state",
    "native_edit_target": "bridge.transcript_repository",
}


class PersistenceImportBoundaryTests(SettingsTestCase):
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
            "import bridge.limits as limits\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert 'bridge.database' not in sys.modules\n"
            "assert limits.DEFAULT_MAX_TOKENS == 1800\n"
            "assert limits.PENDING_SETTINGS_TTL_SECONDS == 600\n"
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

    def test_defaults_have_canonical_data_owners_without_common_facade(self):
        import bridge.config as config
        import bridge.limits as limits

        self.assertFalse((REPO_ROOT / "bridge/common.py").exists())
        self.assertEqual(limits.DEFAULT_MAX_TOKENS, 1800)
        self.assertEqual(limits.PENDING_SETTINGS_TTL_SECONDS, 600)
        self.assertEqual(config.GENERATION_DEFAULTS["max_tokens"], limits.DEFAULT_MAX_TOKENS)
        self.assertEqual(config.REASONING_LEVELS["high"], 8192)

    def test_database_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.metadata as metadata\n"
            "import bridge.sqlite_store as store\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert store._DB_WRITE_LOCK is not None\n"
            "assert store._DB_CONNECTION_GATE is not None\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_database_has_no_obsolete_schema_ready_fixture_state(self):
        import bridge.metadata as metadata

        self.assertFalse(hasattr(metadata, "_DB_SCHEMA_LOCK"))
        self.assertFalse(hasattr(metadata, "_DB_SCHEMA_READY"))
        self.assertFalse(hasattr(metadata, "_DB_SCHEMA_READY_PATHS"))

    def test_database_source_has_no_exec_state_preservation_or_runtime_dependency(self):
        source = "\n".join(
            (REPO_ROOT / Path(*module.split(".")).with_suffix(".py")).read_text(encoding="utf-8")
            for module in set(PERSISTENCE_OWNERS.values())
        )

        self.assertNotIn('globals().get("_DB_WRITE_LOCK")', source)
        self.assertNotIn("import bridge.runtime", source)
        self.assertNotIn("from bridge.runtime import", source)
        self.assertNotIn("import bridge.common", source)
        self.assertNotIn("from bridge.common import", source)

    def test_database_default_path_follows_canonical_config(self):

        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "default.sqlite3"
            with patch.object(self.app_settings_builder, "db_file", expected):
                db = _sqlite_store.db_connect(app_settings=self.app_settings_builder.build())
                try:
                    self.assertEqual(
                        Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve(),
                        expected.resolve(),
                    )
                finally:
                    db.close()

    def test_database_explicit_path_overrides_canonical_default(self):

        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "configured.sqlite3"
            explicit = Path(directory) / "explicit.sqlite3"
            with patch.object(self.app_settings_builder, "db_file", configured):
                db = _sqlite_store.db_connect(explicit, app_settings=self.app_settings_builder.build())
                try:
                    self.assertEqual(
                        Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve(),
                        explicit.resolve(),
                    )
                finally:
                    db.close()

    def test_task_model_default_reads_current_canonical_config(self):

        class EmptyMetaDb:
            def execute(self, *_args, **_kwargs):
                class Cursor:
                    def fetchone(self):
                        return None

                return Cursor()

        session = {"session_id": "s", "model_id": ""}
        with patch.object(self.app_settings_builder, "default_model", "patched::model"):
            self.assertEqual(
                _owner_model_selection.task_model_for_session(
                    EmptyMetaDb(), "chat", session, "summary", app_settings=self.app_settings_builder.build()
                ),
                "patched::model",
            )

    def test_database_maintenance_uses_current_canonical_default_path(self):

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.sqlite3"
            with patch.object(self.app_settings_builder, "db_file", path):
                db = _sqlite_store.db_connect(app_settings=self.app_settings_builder.build())
                try:
                    db.execute("CREATE TABLE persistence_churn(id INTEGER PRIMARY KEY, payload TEXT)")
                    db.executemany(
                        "INSERT INTO persistence_churn(payload) VALUES(?)",
                        [("x" * 2000,) for _ in range(200)],
                    )
                    db.commit()
                    db.execute("DELETE FROM persistence_churn")
                    db.commit()
                finally:
                    db.close()

                _sqlite_store.run_database_maintenance(
                    vacuum_freelist_threshold=1, app_settings=self.app_settings_builder.build()
                )
                self.assertTrue(path.is_file())

    def test_database_and_config_have_direct_canonical_ownership(self):
        import importlib
        import inspect

        import bridge.config as config
        import bridge.help as help_module
        import bridge.sync_core as sync_core

        self.assertFalse((REPO_ROOT / "bridge/database.py").exists())
        for name, owner in PERSISTENCE_OWNERS.items():
            function = getattr(importlib.import_module(owner), name)
            self.assertEqual(function.__module__, owner, name)
        self.assertIs(
            inspect.unwrap(_sqlite_store.write_transaction).__globals__["_DB_WRITE_LOCK"], _sqlite_store._DB_WRITE_LOCK
        )
        self.assertIs(_sqlite_store.db_connect.__globals__["_DB_CONNECTION_GATE"], _sqlite_store._DB_CONNECTION_GATE)
        self.assertIs(sync_core.GENERATION_DEFAULTS, config.GENERATION_DEFAULTS)
        self.assertIs(help_module.REASONING_LEVELS, config.REASONING_LEVELS)


if __name__ == "__main__":
    unittest.main()
