"""Cohesive responsibilities have a canonical owner with enforced dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]


def definitions(filename):
    path = ROOT / "bridge" / filename
    assert path.is_file(), f"canonical owner missing: {filename}"
    return {node.name for node in ast.parse(path.read_text()).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def test_shared_runtime_bag_is_retired():
    assert not (ROOT / "bridge/common.py").exists()


def test_topic_logging_and_scheduler_owners_are_separate():
    assert {"topic_scope_id", "parse_topic_scope", "topic_scope_from_message"} <= definitions("topic_scope.py")
    assert {"configure_logging", "enforce_runtime_permissions"} <= definitions("runtime_logging.py")
    assert {"submit_chat_background", "submit_background", "shutdown_background_executors"} <= definitions(
        "background.py"
    )


def test_sqlite_mechanics_have_one_owner():
    expected = {
        "db_connect",
        "_lightweight_db_connect",
        "_open_initialized_database",
        "run_write_txn",
        "write_transaction",
        "run_database_maintenance",
    }
    assert expected <= definitions("sqlite_store.py")
    assert not expected & definitions("database.py")


def test_grouped_panel_commands_are_not_owned_by_root_dispatch():
    expected = {"_handle_panels", "_handle_generation_panels", "_handle_memory_media", "_handle_voice_panels"}
    assert expected <= definitions("command_panels.py")
    assert not expected & definitions("command_routes.py")


def test_resource_limits_have_one_data_only_owner():
    source = ROOT / "bridge/limits.py"
    assert source.is_file()
    tree = ast.parse(source.read_text())
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)) for node in ast.walk(tree))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert {
        "IMAGE_MAX_BYTES",
        "SYNC_MAX_BYTES",
        "CATALOG_MAX_ITEMS",
        "RAG_MAX_FILE_BYTES",
        "MAX_TELEGRAM_LENGTH",
        "_BACKGROUND_MAX_QUEUED_PER_CHAT",
    } <= assigned
