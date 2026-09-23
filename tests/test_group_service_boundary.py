from __future__ import annotations

import ast
from dataclasses import MISSING
import importlib
import inspect
from pathlib import Path

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"


def imported_modules(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def group_service_module():
    path = BRIDGE / "group_service.py"
    assert path.is_file(), "GroupService module is missing"
    return importlib.import_module("bridge.group_service")


def test_group_service_is_pure_and_has_expected_methods():
    module = group_service_module()
    assert not any(
        name == "bridge" or name.startswith("bridge.")
        for name in imported_modules("group_service.py")
    )
    for name in (
        "state",
        "save",
        "user_turn_allowed",
        "claim_user_turn",
        "pass_user_turn",
        "setup_state",
        "character_option_label",
        "resolve_character",
        "member_labels",
        "current_speaker",
        "advance_turn",
    ):
        assert callable(getattr(module.GroupService, name))


def test_group_service_delegates_without_rewriting_arguments(tmp_path):
    module = group_service_module()
    calls = []

    def backend(name, result=None):
        def invoke(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return invoke

    service = module.GroupService(
        load_state=backend("state", {"enabled": True}),
        save_state=backend("save", True),
        user_turn_allowed_backend=backend("allowed", True),
        claim_user_turn_backend=backend("claim", True),
        pass_user_turn_backend=backend("pass", False),
        setup_state_backend=backend("setup", {"stage": "character"}),
        character_option_label_backend=backend("option", "Mira"),
        resolve_character_backend=backend("resolve", "mira.png"),
        member_labels_backend=backend("labels", ["Mira"]),
        current_speaker_backend=backend("speaker", ("mira.png", {"enabled": True})),
        advance_turn_backend=backend("advance", None),
    )
    db = object()
    session = {"session_id": "session"}
    state = {"enabled": True}
    path = tmp_path / "mira.png"

    assert service.state(db, "chat", "session") == {"enabled": True}
    assert service.save(db, "chat", "session", state, 7) is True
    assert service.user_turn_allowed(db, "chat", "session", "user") is True
    assert service.claim_user_turn(db, "chat", "session", "user") is True
    assert service.pass_user_turn(db, "chat", "session", "user") is False
    assert service.setup_state(db, "chat", "session") == {"stage": "character"}
    assert service.character_option_label(path) == "Mira"
    assert service.resolve_character("Mira") == "mira.png"
    assert service.member_labels(["mira.png"]) == ["Mira"]
    assert service.current_speaker(db, "chat", session, "hello")[0] == "mira.png"
    assert service.advance_turn(db, "chat", "session", 9) is None

    assert calls[0] == ("state", (db, "chat", "session"), {})
    assert calls[1] == ("save", (db, "chat", "session", state, 7), {})
    assert calls[-1] == ("advance", (db, "chat", "session", 9), {})


def test_group_service_is_required_by_composition():
    from bridge.composition import BridgeServices, build_bridge_services

    field = BridgeServices.__dataclass_fields__["group"]
    assert field.default is MISSING
    assert "None" not in str(field.type)
    param = inspect.signature(build_bridge_services).parameters["group"]
    assert param.default is inspect.Parameter.empty


def test_startup_composes_group_before_director_and_director_uses_service():
    source = (BRIDGE / "main.py").read_text(encoding="utf-8")
    assert "_GroupService(" in source
    assert "load_group_state=group.state" in source
    assert "member_labels=group.member_labels" in source


def test_conversation_forwards_group_service_to_generation():
    from types import SimpleNamespace
    from bridge.conversation_service import ConversationService, PreparedMessage

    group = object()
    memory = object()
    persona = object()
    request_context = object()
    prepared = PreparedMessage(
        stripped="hello",
        command="hello",
        fields={"name": "Mira"},
        session={"session_id": "session"},
        session_id="session",
        current_model="model",
        current_persona="",
        user_name="User",
        group_turn=None,
        group_context="",
        request_context=request_context,
    )
    generated = {}

    service = ConversationService(
        prepare_message=lambda *_args, **_kwargs: prepared,
        dispatch_command=lambda *_args, **_kwargs: False,
        generate_reply=lambda *_args, **kwargs: generated.update(kwargs),
    )
    services = SimpleNamespace(group=group, memory=memory, persona=persona)

    service.process_message(
        object(), "token", "key", "queue-model", {}, "chat", "hello", 11,
        services=services,
    )

    assert generated["group_service"] is group
    assert generated["memory_service"] is memory
    assert generated["persona_service"] is persona


def test_image_processing_requires_group_service_parameter():
    from bridge.commands import process_image_message

    param = inspect.signature(process_image_message).parameters.get("group_service")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_application_paths_do_not_import_group_core_after_service_migration():
    for filename in (
        "message_commands.py",
        "commands.py",
        "worker_orchestration.py",
        "update_routing.py",
    ):
        assert "bridge.group_core" not in imported_modules(filename), filename


def test_committed_recovery_uses_group_service_for_advance(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    import bridge.worker_orchestration as workers

    calls = []

    class Group:
        def current_speaker(self, *args):
            calls.append(("speaker", args[1:]))
            return ("mira.png", {"enabled": True})

        def advance_turn(self, *args, **kwargs):
            calls.append(("advance", args[1:], kwargs))

    jobs = SimpleNamespace(
        actor_id=lambda *_args: "actor",
        complete=lambda *_args: True,
        fail=lambda *_args: True,
    )
    services = SimpleNamespace(
        config=SimpleNamespace(
            bot_token="token",
            api_key="key",
            default_model="model",
        ),
        db_factory=lambda: sqlite3.connect(":memory:"),
        jobs=jobs,
        group=Group(),
        conversation=object(),
        telegram=SimpleNamespace(send_text=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        workers,
        "committed_assistant_for_message",
        lambda *_args: (7, "stored reply", "[]"),
    )
    monkeypatch.setattr(
        workers,
        "load_session",
        lambda *_args: {"session_id": "session"},
    )
    monkeypatch.setattr(workers, "send_reply", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workers, "clear_failed_turn", lambda *_args: None)

    workers.process_message_job(
        services,
        {"name": "Mira"},
        "chat",
        "hello",
        11,
        queued_session_id="session",
    )

    assert [item[0] for item in calls] == ["speaker", "advance"]
