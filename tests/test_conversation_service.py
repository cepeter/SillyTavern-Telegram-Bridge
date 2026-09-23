"""Command-versus-generation orchestration has one injected owner."""
from dataclasses import MISSING
import ast
import importlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def imports(name):
    tree = ast.parse((ROOT / "bridge" / f"{name}.py").read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def service_module():
    assert (ROOT / "bridge/conversation_service.py").is_file(), "ConversationService is missing"
    return importlib.import_module("bridge.conversation_service")


def test_message_commands_does_not_import_command_routes():
    assert "bridge.command_routes" not in imports("message_commands")


@pytest.mark.parametrize("owner", ["command_routes", "media", "worker_orchestration"])
def test_entrypoints_do_not_import_old_message_dispatch(owner):
    tree = ast.parse((ROOT / "bridge" / f"{owner}.py").read_text())
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "bridge.message_commands"
        and any(alias.name == "process_message" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_service_is_required_by_composition():
    from bridge.composition import BridgeServices, build_bridge_services
    import inspect
    assert "conversation" in BridgeServices.__dataclass_fields__
    assert BridgeServices.__dataclass_fields__["conversation"].default is MISSING
    assert inspect.signature(build_bridge_services).parameters["conversation"].default is inspect.Parameter.empty


def test_service_has_no_concrete_bridge_imports():
    service_module()
    assert not any(name == "bridge" or name.startswith("bridge.") for name in imports("conversation_service"))
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import bridge.conversation_service; "
         "assert 'bridge.telegram' not in sys.modules; "
         "assert 'bridge.command_routes' not in sys.modules"],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def make_service(*, handled=False, prepare_handled=False, route_error=False):
    module = service_module()
    events = []
    services = SimpleNamespace(memory=object(), persona=object(), group=object(), provider=object())
    context = SimpleNamespace(db=object(), session_id="queued-session", actor_id="actor")
    prepared = module.PreparedMessage(
        stripped="hello", command="hello", fields={"name": "character"},
        session={"session_id": "queued-session"}, session_id="queued-session",
        current_model="resolved-model", current_persona="persona", user_name="user",
        group_turn=None, group_context="group-context", request_context=context,
    )

    def prepare(*args, **kwargs):
        events.append(("prepare", args, kwargs))
        return None if prepare_handled else prepared

    def dispatch(*args, **kwargs):
        events.append(("command", args, kwargs))
        if route_error:
            raise RuntimeError("recognized command failed")
        return handled

    def generate(*args, **kwargs):
        events.append(("generate", args, kwargs))

    service = module.ConversationService(
        prepare_message=prepare, dispatch_command=dispatch, generate_reply=generate,
    )
    return service, services, events, prepared


def run_message(service, services):
    service.process_message(
        "db", "token", "key", "queue-default", {}, "chat", "hello", 123,
        queued_session_id="queued-session", operation_id=456, actor_id="actor",
        services=services,
    )


def test_handled_command_never_generates():
    service, services, events, prepared = make_service(handled=True)
    run_message(service, services)
    assert [event[0] for event in events] == ["prepare", "command"]
    assert events[1][2]["request_context"] is prepared.request_context
    assert events[1][2]["services"] is services


def test_pending_input_or_recovery_short_circuits_both_ports():
    service, services, events, _ = make_service(prepare_handled=True)
    run_message(service, services)
    assert [event[0] for event in events] == ["prepare"]


def test_unhandled_message_generates_with_resolved_model_and_original_identity():
    service, services, events, prepared = make_service()
    run_message(service, services)
    assert [event[0] for event in events] == ["prepare", "command", "generate"]
    assert events[0][2] == dict(
        queued_session_id="queued-session", operation_id=456,
        actor_id="actor", services=services,
    )
    args, kwargs = events[2][1:]
    assert args == (
        "db", "token", "key", prepared.fields, "chat", "hello", prepared.session,
        "queued-session", "resolved-model", None, "group-context", 123, 456,
    )
    assert kwargs == dict(group_service=services.group, provider_port=services.provider, memory_service=services.memory, persona_service=services.persona)


def test_command_exception_propagates_without_generation():
    service, services, events, _ = make_service(route_error=True)
    with pytest.raises(RuntimeError, match="recognized command failed"):
        run_message(service, services)
    assert [event[0] for event in events] == ["prepare", "command"]


def test_message_commands_has_no_dispatch_compatibility_exports():
    from bridge import message_commands

    assert not hasattr(message_commands, "process_message")
    assert not hasattr(message_commands, "handle_command_route")
