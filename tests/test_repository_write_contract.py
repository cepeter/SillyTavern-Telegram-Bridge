"""Repository writers require a caller-owned transaction without owning its commit."""

from __future__ import annotations

import sqlite3

import pytest

import bridge.meta_repository as _owner_meta_repository
from bridge import director_goal_repository, group_repository, meta_repository, operation_repository, scene_repository
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


@pytest.mark.parametrize(
    "writer,args",
    [
        (director_goal_repository.store_director_goal, ("chat", "session", "goal", 1.0)),
        (director_goal_repository.delete_director_goal, ("chat", "session")),
        (scene_repository.delete_scene_state, ("chat", "session")),
        (scene_repository.upsert_scene_state_if_fresh, ("chat", "session", "{}", 1, 1.0)),
        (meta_repository.store_meta_value, ("key", "value")),
        (
            group_repository.store_group_state_row,
            ("chat", "session", "Group", True, 0, "manual", "", "[]", "", "[]", 1.0),
        ),
        (operation_repository.claim_operation, ("operation", "group", 1.0)),
        (operation_repository.mark_operation_applied, ("operation", "group", 1.0)),
    ],
)
def test_standalone_repository_writes_fail_before_any_sql(writer, args):
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    db.commit()
    calls = []
    db.set_trace_callback(calls.append)
    try:
        with pytest.raises(RuntimeError, match=r"active.*transaction"):
            writer(db, *args)
        assert not calls
        assert not db.in_transaction
    finally:
        db.close()


def test_repository_writes_are_atomic_and_do_not_commit_caller_transaction():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    try:
        with pytest.raises(ValueError, match="abort"), write_transaction(db):
            _owner_meta_repository.store_meta_value(db, "one", "1")
            _owner_meta_repository.store_meta_value(db, "two", "2")
            assert db.in_transaction
            raise ValueError("abort")
        assert db.execute("SELECT * FROM meta").fetchall() == []
        with write_transaction(db):
            _owner_meta_repository.store_meta_value(db, "ok", "saved")
        assert db.execute("SELECT * FROM meta").fetchall() == [("ok", "saved")]
    finally:
        db.close()
