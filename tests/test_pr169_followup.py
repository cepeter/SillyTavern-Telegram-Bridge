"""Regressions found while independently reviewing the merged guided-optimizer work."""

from __future__ import annotations

import hashlib
import json

from application_test_setup import make_test_application_services
from test_character_mutation_safety import _card_png
from test_character_mutation_safety import card_context as card_context

from bridge import character_optimizer_input, character_optimizer_panels, character_quality, input_flows
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.telegram_output import telegram_safe_output


def test_telegram_safe_output_keeps_html_link_destination_and_markdown_autolinks():
    source = (
        'Read <a href="https://example.test/source">the source</a>. '
        "Keep <https://example.test/path> and <user@example.test>. "
        "<div>Then continue.</div>"
    )
    result = telegram_safe_output(source)
    assert "the source (https://example.test/source)" in result
    assert "<https://example.test/path>" in result
    assert "<user@example.test>" in result
    assert "<div>" not in result
    assert "Then continue." in result


def test_two_actors_keep_independent_optimizer_suggestion_state(card_context, monkeypatch):
    db, ctx, _ = card_context
    original = _card_png("Alice", "installed original")
    target = ctx.app_settings.character_dir / "Alice.png"
    target.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    prompts: list[str] = []
    previews: list[str] = []
    model_prompts: list[str] = []

    def generate(*args, **kwargs):
        model_prompts.append("\n".join(str(message.get("content") or "") for message in args[2]))
        return json.dumps({"description": f"draft {len(model_prompts)}"})

    port = ProviderPort(generate)
    services = make_test_application_services(app_settings=ctx.app_settings, provider=port)
    session = services.session.create(db, "chat", ctx.app_settings.default_model, session_id="session")
    monkeypatch.setattr(character_quality, "task_model_for_session", lambda *a, **k: "utility::fixture")
    monkeypatch.setattr(character_optimizer_input, "discard_panel_binding", lambda *a, **k: None)
    monkeypatch.setattr(character_optimizer_input, "close_panel_message", lambda *a, **k: None)
    monkeypatch.setattr(character_optimizer_input, "delete_pending_input_prompts", lambda *a, **k: None)
    monkeypatch.setattr(character_optimizer_input, "send_text", lambda _t, _c, text: prompts.append(text) or [700])
    monkeypatch.setattr(
        character_optimizer_panels,
        "send_panel_message",
        lambda _t, _c, text, _markup, *a, **k: previews.append(text),
    )
    callback = {"message": {"message_id": 55, "chat": {"id": "chat"}}}
    alice = RequestContext(db, session["session_id"], "alice", app_settings=ctx.app_settings)
    bob = RequestContext(db, session["session_id"], "bob", app_settings=ctx.app_settings)

    character_optimizer_input.start_character_optimizer_suggestion_input(
        db, "token", "chat", "Alice.png", digest, callback, request_context=alice
    )
    character_optimizer_input.start_character_optimizer_suggestion_input(
        db, "token", "chat", "Alice.png", digest, callback, request_context=bob
    )

    for context, suggestion in ((alice, "make her dry and sarcastic"), (bob, "make the greeting shorter")):
        handled = input_flows.handle_pending_input(
            db,
            "token",
            "chat",
            session,
            suggestion,
            fields={"name": "Alice"},
            handle_session_name=lambda *a, **k: False,
            group_service=services.group,
            provider_port=port,
            memory_service=services.memory,
            persona_service=services.persona,
            request_context=context,
            rag_service=services.rag,
        )
        assert handled is True

    assert len(model_prompts) == 2
    assert "make her dry and sarcastic" in model_prompts[0]
    assert "make the greeting shorter" in model_prompts[1]
    assert len(previews) == 2
