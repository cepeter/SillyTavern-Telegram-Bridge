"""Exercise feature wiring across real composition, queues and callback routing."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from application_test_setup import make_test_application_services, make_test_rag_service
from test_character_mutation_safety import _card_png
from test_character_mutation_safety import card_context as card_context

from bridge import callback_dispatch, cards, character_callbacks, character_quality, document_jobs, native_imports
from bridge.callback_tokens import dynamic_callback_token
from bridge.card_content import parse_png_chara_bytes
from bridge.character_proposals import load_character_proposal
from bridge.job_service import DurableJob
from bridge.main import _build_startup_services
from bridge.model_router import ModelRouter
from bridge.panel_bindings import bind_panel_session
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.worker_orchestration import resolve_recovered_job_submission


def test_document_job_and_recovery_keep_queued_session_and_actor(card_context, monkeypatch):
    db, ctx, panels = card_context
    settings = ctx.app_settings
    original = _card_png("Alice", "original")
    (settings.character_dir / "Alice.png").write_bytes(original)
    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    services = replace(
        services,
        provider=ProviderPort(lambda *a, **k: pytest.fail("unapproved upload must not be ranked")),
        rag=make_test_rag_service(),
    )
    old = services.session.create(db, "chat", settings.default_model, session_id="queued-session")
    job_id = services.jobs.enqueue(db, 1, "chat", old["session_id"], 55, "document", {"actor_id": "actor"})
    services.session.create(db, "chat", settings.default_model, session_id="new-active-session")
    monkeypatch.setattr(native_imports, "download_telegram_file", lambda *a, **k: _card_png("Alice", "updated"))
    document = {"file_name": "Alice.png", "file_id": "fixture", "file_size": 800}
    document_jobs.process_document_job(services, "chat", document, 55, old["session_id"], job_id=job_id)
    assert panels, "queued upload must reach a confirmation instead of failing before import"
    nonce = panels[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].rsplit(":", 1)[1]
    bound_context = RequestContext(db, old["session_id"], "actor", app_settings=settings)
    pending = load_character_proposal(db, "chat", nonce, request_context=bound_context)
    assert pending.session_id == "queued-session"
    assert pending.actor_id == "actor"
    assert (settings.character_dir / "Alice.png").read_bytes() == original
    with pytest.raises(ValueError, match="another actor or session"):
        load_character_proposal(
            db, "chat", nonce, request_context=replace(bound_context, session_id="new-active-session")
        )
    recovered = resolve_recovered_job_submission(
        services,
        {},
        DurableJob(job_id, "chat", "queued-session", 55, "document", {"document": document, "resolve_active": True}),
    )
    assert recovered is not None
    assert recovered.worker is document_jobs.process_document_job
    assert recovered.args[4] == "queued-session"


@pytest.mark.parametrize("rank_fails", [False, True])
def test_optimizer_callback_preview_apply_and_replay_use_exact_proposal(card_context, monkeypatch, rank_fails):
    db, ctx, _ = card_context
    original = _card_png("Alice", "original")
    target = ctx.app_settings.character_dir / "Alice.png"
    target.write_bytes(original)
    outputs = []
    model_calls = []
    optimized = {"description": "improved description", "post_history_instructions": "Preserve the quiet voice."}

    def generate(*args, **kwargs):
        model_calls.append((args, kwargs))
        assert not db.in_transaction
        return (
            json.dumps(optimized) if str(kwargs["session_id"]).startswith("character-optimize:") else "A — consistent"
        )

    services = make_test_application_services(app_settings=ctx.app_settings, provider=ProviderPort(generate))
    session = services.session.create(db, "chat", ctx.app_settings.default_model, session_id="session")
    monkeypatch.setattr(character_quality, "task_model_for_session", lambda *a, **k: "utility::fixture")
    monkeypatch.setattr(callback_dispatch, "answer_callback", lambda *a, **k: None)

    def send_panel(_token, _method, payload, *, request_context):
        outputs.append(payload)
        bind_panel_session(db, "chat", 55, request_context.session_id, request_context.actor_id)

    monkeypatch.setattr(cards, "send_panel_request", send_panel)
    monkeypatch.setattr(character_callbacks, "send_panel_request", send_panel)
    bind_panel_session(db, "chat", 55, session["session_id"], "actor")
    token = dynamic_callback_token("character", "Alice.png", "chat", db=db)

    def callback(data, actor="actor"):
        return {
            "id": "callback",
            "from": {"id": actor},
            "data": data,
            "message": {"message_id": 55, "chat": {"id": "chat"}},
        }

    callback_dispatch.process_callback(db, "token", callback("characteroptimize:" + token), services=services)
    assert target.read_bytes() == original
    assert "post_history_instructions" in outputs[-1]["text"]
    apply_data = next(
        button["callback_data"]
        for row in outputs[-1]["reply_markup"]["inline_keyboard"]
        for button in row
        if button["text"] == "Apply"
    )
    callback_dispatch.process_callback(db, "token", callback(apply_data, "another-user"), services=services)
    assert target.read_bytes() == original
    if rank_fails:

        def fail_ranking(*args, **kwargs):
            raise OSError("fixture optional ranking failure")

        monkeypatch.setattr(character_callbacks, "rank_character", fail_ranking)
    callback_dispatch.process_callback(db, "token", callback(apply_data), services=services)
    assert "optimized" in outputs[-1]["text"]
    result_callbacks = [
        button["callback_data"] for row in outputs[-1]["reply_markup"]["inline_keyboard"] for button in row
    ]
    assert result_callbacks == ["character:menu", "character:cancel"]
    updated = target.read_bytes()
    card = parse_png_chara_bytes(updated)
    assert card["name"] == "Alice"
    assert card["description"] == optimized["description"]
    assert card["post_history_instructions"] == optimized["post_history_instructions"]
    assert (ctx.app_settings.character_backup_dir / "Alice.png").read_bytes() == original
    assert len(model_calls) == (1 if rank_fails else 2)
    callback_dispatch.process_callback(db, "token", callback(apply_data), services=services)
    assert target.read_bytes() == updated
    assert "expired or already used" in outputs[-1]["text"]
    assert len(model_calls) == (1 if rank_fails else 2)
