from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import bridge.macro_commands as _owner_macro_commands

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


def top_level_functions(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_reset_panel_module_is_pure_and_builds_exact_send_payload():
    path = BRIDGE / "reset_panel.py"
    assert path.is_file()
    module = importlib.import_module("bridge.reset_panel")
    assert not any(name == "bridge" or name.startswith("bridge.") for name in imported_modules("reset_panel.py"))

    method, payload = module.reset_confirmation_request("chat")
    assert method == "sendMessage"
    assert payload == {
        "chat_id": "chat",
        "text": (
            "Reset active session and purge its memory?\n\n"
            "This will:\n"
            "• Delete this session's stored conversation, response variants, summary, and curated memory.\n"
            "• Purge Hindsight documents for this active session.\n"
            "• Attempt to delete bridge-generated Telegram replies from this session.\n"
            "• Keep your own Telegram messages, chat-scoped RAG, and this session identity.\n"
            "• Use /new when you need a completely new session.\n\n"
            "If Hindsight cleanup fails, no local session data will be deleted.\n\n"
            "This cannot be undone."
        ),
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "✅ Confirm active-session reset", "callback_data": "reset:confirm"}],
                [{"text": "❌ Cancel", "callback_data": "reset:cancel"}],
            ]
        },
    }


def test_reset_panel_builds_exact_edit_payload():
    module = importlib.import_module("bridge.reset_panel")
    method, payload = module.reset_confirmation_request("chat", 41)
    assert method == "editMessageText"
    assert payload["message_id"] == 41
    assert payload["chat_id"] == "chat"


def test_commands_and_help_do_not_import_message_commands():
    assert "bridge.message_commands" not in set().union(
        imported_modules("edit_messages.py"),
        imported_modules("image_messages.py"),
        imported_modules("macro_commands.py"),
        imported_modules("note_panels.py"),
        imported_modules("preset_actions.py"),
        imported_modules("prompt_diagnostics.py"),
    )
    assert "bridge.message_commands" not in set().union(
        imported_modules("bot_commands.py"),
        imported_modules("databank_panels.py"),
        imported_modules("document_jobs.py"),
        imported_modules("enum_callbacks.py"),
        imported_modules("memory_panels.py"),
        imported_modules("preset_panels.py"),
        imported_modules("settings_panels.py"),
        imported_modules("system_prompt_panels.py"),
        imported_modules("voice_panels.py"),
    )


def test_message_commands_no_longer_owns_reset_panel_helper():
    assert "send_reset_confirmation_menu" not in top_level_functions("message_commands.py")


def test_macro_command_requires_request_context_for_reset_panel_delivery():

    param = inspect.signature(_owner_macro_commands.handle_macro_command).parameters.get("request_context")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_macro_reset_delivers_canonical_panel_with_request_context(monkeypatch):

    calls = []
    monkeypatch.setattr(
        _owner_macro_commands,
        "send_panel_request",
        lambda token, method, payload, **kwargs: calls.append((token, method, payload, kwargs)) or {},
    )

    _owner_macro_commands.handle_macro_command(
        object(),
        "token",
        "chat",
        {"persona_id": ""},
        {},
        "/stscript reset",
        request_context="ctx",
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "sendMessage"
    assert payload["chat_id"] == "chat"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "reset:confirm"
    assert kwargs["request_context"] == "ctx"
