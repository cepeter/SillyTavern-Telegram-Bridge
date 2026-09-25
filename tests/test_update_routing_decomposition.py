from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"


def top_level_functions(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def function_source(filename: str, function_name: str) -> str:
    source = (BRIDGE / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function_name
    )
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def route_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE processed_updates(update_id INTEGER PRIMARY KEY,processed_at REAL NOT NULL)")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    return db


def minimal_services():
    from types import SimpleNamespace

    return SimpleNamespace(
        config=SimpleNamespace(
            bot_token="token",
            default_model="model",
        )
    )


def test_focused_routing_modules_exist_and_own_expected_functions():
    callback_path = BRIDGE / "update_callback_routing.py"
    message_path = BRIDGE / "update_message_routing.py"

    assert callback_path.is_file()
    assert message_path.is_file()

    assert "route_callback_update" in top_level_functions("update_callback_routing.py")
    message_functions = top_level_functions("update_message_routing.py")
    assert "route_edited_message_update" in message_functions
    assert "route_message_update" in message_functions
    assert "is_long_running_command" in message_functions


def test_route_update_is_only_a_coordinator():
    route_source = function_source("update_routing.py", "route_update")
    update_functions = top_level_functions("update_routing.py")

    assert "is_long_running_command" not in update_functions
    assert "services.jobs.enqueue(" not in route_source
    assert "services.jobs.submit(" not in route_source
    assert "send_text(" not in route_source
    assert "answer_callback(" not in route_source
    assert "ensure_session(" not in route_source
    assert "route_callback_update(" in route_source
    assert "route_edited_message_update(" in route_source
    assert "route_message_update(" in route_source
    assert "complete_update(" in route_source


def test_focused_helpers_never_own_update_completion():
    for filename in (
        "update_callback_routing.py",
        "update_message_routing.py",
    ):
        source = (BRIDGE / filename).read_text(encoding="utf-8")
        assert "complete_update(" not in source
        assert "processed_updates" not in source
        assert "telegram_offset" not in source


def test_duplicate_update_skips_helpers_and_completes_once(monkeypatch):
    import bridge.update_routing as routing

    db = route_db()
    db.execute(
        "INSERT INTO processed_updates(update_id, processed_at) VALUES(?, ?)",
        (10, 1.0),
    )
    db.commit()
    calls = []
    completions = []

    monkeypatch.setattr(
        routing,
        "route_callback_update",
        lambda *_args, **_kwargs: calls.append("callback"),
    )
    monkeypatch.setattr(
        routing,
        "route_edited_message_update",
        lambda *_args, **_kwargs: calls.append("edit"),
    )
    monkeypatch.setattr(
        routing,
        "route_message_update",
        lambda *_args, **_kwargs: calls.append("message") or True,
    )
    monkeypatch.setattr(
        routing,
        "complete_update",
        lambda _db, update_id, offset: completions.append((update_id, offset)),
    )

    try:
        result = routing.route_update(
            minimal_services(),
            db,
            {},
            {"update_id": 10, "message": {"chat": {"id": "chat"}}},
            3,
            frozenset(),
        )
    finally:
        db.close()

    assert result == 11
    assert calls == []
    assert completions == [(10, 11)]


@pytest.mark.parametrize(
    ("update", "expected_helper"),
    [
        (
            {
                "update_id": 11,
                "callback_query": {
                    "id": "cb",
                    "from": {"id": "u"},
                    "message": {"chat": {"id": "chat"}},
                },
            },
            "callback",
        ),
        (
            {
                "update_id": 12,
                "edited_message": {
                    "from": {"id": "u"},
                    "chat": {"id": "chat"},
                    "text": "edited",
                },
            },
            "edit",
        ),
    ],
)
def test_callback_and_edit_success_complete_once(
    monkeypatch,
    update,
    expected_helper,
):
    import bridge.update_routing as routing

    db = route_db()
    calls = []
    completions = []

    monkeypatch.setattr(
        routing,
        "route_callback_update",
        lambda *_args, **_kwargs: calls.append("callback"),
    )
    monkeypatch.setattr(
        routing,
        "route_edited_message_update",
        lambda *_args, **_kwargs: calls.append("edit"),
    )
    monkeypatch.setattr(
        routing,
        "route_message_update",
        lambda *_args, **_kwargs: calls.append("message") or True,
    )
    monkeypatch.setattr(
        routing,
        "complete_update",
        lambda _db, update_id, offset: completions.append((update_id, offset)),
    )

    try:
        result = routing.route_update(
            minimal_services(),
            db,
            {},
            update,
            0,
            frozenset({"u"}),
        )
    finally:
        db.close()

    assert result == update["update_id"] + 1
    assert calls == [expected_helper]
    assert completions == [(update["update_id"], update["update_id"] + 1)]


def test_handled_ordinary_message_completes_once(monkeypatch):
    import bridge.update_routing as routing

    db = route_db()
    completions = []
    monkeypatch.setattr(
        routing,
        "route_callback_update",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        routing,
        "route_edited_message_update",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        routing,
        "route_message_update",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        routing,
        "complete_update",
        lambda _db, update_id, offset: completions.append((update_id, offset)),
    )

    try:
        result = routing.route_update(
            minimal_services(),
            db,
            {},
            {
                "update_id": 13,
                "message": {"chat": {"id": "chat"}},
            },
            0,
            frozenset(),
        )
    finally:
        db.close()

    assert result == 14
    assert completions == [(13, 14)]


def test_unroutable_ordinary_message_does_not_complete(monkeypatch):
    import bridge.update_routing as routing

    db = route_db()
    completions = []
    monkeypatch.setattr(
        routing,
        "route_message_update",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        routing,
        "complete_update",
        lambda _db, update_id, offset: completions.append((update_id, offset)),
    )

    try:
        result = routing.route_update(
            minimal_services(),
            db,
            {},
            {"update_id": 14, "message": {}},
            0,
            frozenset(),
        )
    finally:
        db.close()

    assert result == 15
    assert completions == []


def test_helper_exception_propagates_without_completion(monkeypatch):
    import bridge.update_routing as routing

    db = route_db()
    completions = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("routing failed")

    monkeypatch.setattr(routing, "route_message_update", boom)
    monkeypatch.setattr(
        routing,
        "complete_update",
        lambda _db, update_id, offset: completions.append((update_id, offset)),
    )

    try:
        with pytest.raises(RuntimeError, match="routing failed"):
            routing.route_update(
                minimal_services(),
                db,
                {},
                {
                    "update_id": 15,
                    "message": {"chat": {"id": "chat"}},
                },
                0,
                frozenset(),
            )
    finally:
        db.close()

    assert completions == []
