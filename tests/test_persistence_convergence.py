"""Existing application storage operations must not commit an enclosing use case."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import bridge.failed_turns as _owner_failed_turns
import bridge.generation_settings as _owner_generation_settings
import bridge.job_store as _owner_job_store
import bridge.metadata as _owner_metadata
import bridge.operations as _owner_operations
import bridge.panel_bindings as _owner_panel_bindings
import bridge.sync_state as _owner_sync_state
from bridge.session_core import ensure_session
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect, write_transaction

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "operation",
    [
        lambda db: _owner_metadata.set_meta(db, "nested", "value"),
        lambda db: _owner_failed_turns.record_failed_turn(
            db, "chat", 10, "ordinary message", "provider::model", "failed", "default"
        ),
        lambda db: _owner_failed_turns.clear_failed_turn(db, "chat", 10),
        lambda db: _owner_panel_bindings.bind_panel_session(db, "chat", 123, "default", "user"),
        lambda db: _owner_operations.begin_operation(db, "new-op", "test"),
        lambda db: _owner_job_store.enqueue_job(db, 99, "chat", "default", 10, "generation", {"actor_id": "user"}),
        lambda db: _owner_job_store.mark_job_scheduled(db, 99),
        lambda db: _owner_job_store.mark_job_running(db, 99),
        lambda db: _owner_job_store.finish_job(db, 99, "done"),
        lambda db: _owner_job_store.recover_jobs(db),
        lambda db: _owner_generation_settings.save_generation_preset(db, "chat", "preset", {"temperature": 0.3}),
        lambda db: _owner_generation_settings.delete_generation_preset(db, "chat", "preset"),
        lambda db: _owner_sync_state.ensure_sync_binding(db, "chat", "default"),
        lambda db: _owner_generation_settings.update_generation_settings(db, "chat", "default", temperature=0.3),
    ],
)
def test_storage_call_participates_in_caller_rollback(tmp_path, operation):
    settings = load_app_settings({}, home=tmp_path)
    db = db_connect(app_settings=settings)
    ensure_session(db, "chat", "provider::model", app_settings=settings)
    tables = (
        "meta",
        "failed_turns",
        "panel_sessions",
        "operations",
        "jobs",
        "processed_updates",
        "generation_presets",
        "sync_bindings",
        "generation_settings",
    )

    def snapshot():
        return {
            table: db.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 -- fixed test table allowlist
            for table in tables
        }

    try:
        original = snapshot()
        with pytest.raises(ValueError, match="abort outer use case"):
            with write_transaction(db):
                db.execute("INSERT INTO meta(key,value) VALUES('outer-write','pending')")
                operation(db)
                assert db.in_transaction, "storage helper committed the caller-owned transaction"
                raise ValueError("abort outer use case")
        assert snapshot() == original
    finally:
        db.close()


def test_aggregate_database_and_repository_facades_are_retired():
    assert not (ROOT / "bridge/database.py").exists()
    assert not (ROOT / "bridge/repositories.py").exists()
    for path in (ROOT / "bridge").glob("*.py"):
        assert "run_write_txn" not in path.read_text(), path.name


def test_domain_repositories_have_no_transaction_or_adapter_ownership():
    expected = {
        "meta_repository.py",
        "operation_repository.py",
        "job_repository.py",
        "failure_repository.py",
        "panel_repository.py",
        "generation_settings_repository.py",
        "sync_repository.py",
        "session_repository.py",
        "group_repository.py",
        "scene_repository.py",
        "director_goal_repository.py",
        "transcript_repository.py",
    }
    assert expected <= {path.name for path in (ROOT / "bridge").glob("*_repository.py")}
    for filename in expected:
        tree = ast.parse((ROOT / "bridge" / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}, filename
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("bridge."):
                assert node.module == "bridge.repository_contracts", (filename, node.module)


def test_job_transition_failure_preserves_caller_transaction(tmp_path, monkeypatch):
    import sqlite3

    from bridge import job_store

    settings = load_app_settings({}, home=tmp_path)
    db = db_connect(app_settings=settings)

    def locked(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(job_store, "finish_job_row", locked)
    try:
        assert job_store.finish_job(db, 1, "done") is False
        assert not db.in_transaction
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with write_transaction(db):
                db.execute("INSERT INTO meta(key,value) VALUES('outer','pending')")
                job_store.finish_job(db, 1, "done")
        assert db.execute("SELECT * FROM meta WHERE key='outer'").fetchall() == []
    finally:
        db.close()
