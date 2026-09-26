"""Cross-boundary invariants not exercised by the original feature PRs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import pytest
from test_character_mutation_safety import _card_png, apply_proposal, prepare_replacement, upload
from test_character_mutation_safety import card_context as card_context

from bridge import character_optimizer_panels as panels
from bridge import character_proposals as proposals
from bridge import character_quality as quality
from bridge.metadata import set_meta
from bridge.provider_port import ProviderPort


def test_optimizer_apply_revalidates_preview_against_exact_card_bytes(card_context):
    db, ctx, _ = card_context
    original = _card_png("Alice", "original")
    upload(card_context, original)
    nonce = proposals.stage_character_proposal(
        db,
        "chat",
        "optimize",
        "Alice.png",
        _card_png("Other", "changed identity"),
        hashlib.sha256(original).hexdigest(),
        request_context=ctx,
        fields={"description": "harmless preview"},
    )
    with pytest.raises(ValueError, match="preview"):
        apply_proposal(card_context, nonce, "apply")
    assert (ctx.app_settings.character_dir / "Alice.png").read_bytes() == original


def test_rank_cache_is_invalidated_by_file_replacement(card_context):
    db, ctx, _ = card_context
    upload(card_context, _card_png("Alice", "original"))
    quality.store_character_rank(db, "Alice.png", "S", app_settings=ctx.app_settings)
    assert quality.character_rank(db, "Alice.png", app_settings=ctx.app_settings) == "S"
    (ctx.app_settings.character_dir / "Alice.png").write_bytes(_card_png("Alice", "changed"))
    assert quality.character_rank(db, "Alice.png", app_settings=ctx.app_settings) == ""


def test_rank_result_is_discarded_if_file_changes_during_provider_call(card_context, monkeypatch):
    db, ctx, _ = card_context
    upload(card_context, _card_png("Alice", "original"))
    monkeypatch.setattr(quality, "task_model_for_session", lambda *a, **k: "utility::model")

    def backend(*args, **kwargs):
        assert kwargs["request_timeout"] == 30.0
        assert kwargs["force_non_stream"]
        assert not db.in_transaction
        (ctx.app_settings.character_dir / "Alice.png").write_bytes(_card_png("Alice", "changed"))
        return "S — excellent"

    assert (
        quality.rank_character(
            db,
            "chat",
            {"session_id": ctx.session_id, "model_id": "story"},
            {"name": "Alice"},
            "Alice.png",
            provider_port=ProviderPort(backend),
            app_settings=ctx.app_settings,
        )
        is None
    )
    assert quality.character_rank(db, "Alice.png", app_settings=ctx.app_settings) == ""


@pytest.mark.parametrize("operation", ["rank", "optimize"])
def test_utility_calls_refuse_outer_transaction(card_context, monkeypatch, operation):
    db, ctx, _ = card_context
    upload(card_context, _card_png("Alice", "original"))
    monkeypatch.setattr(quality, "task_model_for_session", lambda *a, **k: "utility::model")
    calls = []
    port = ProviderPort(lambda *a, **k: calls.append(1) or "S")
    db.execute("BEGIN")
    try:
        with pytest.raises(ValueError, match="transaction"):
            if operation == "rank":
                quality.rank_character(
                    db,
                    "chat",
                    {"session_id": "session"},
                    {"name": "Alice"},
                    "Alice.png",
                    provider_port=port,
                    app_settings=ctx.app_settings,
                )
            else:
                quality.optimize_character(
                    db,
                    "chat",
                    {"session_id": "session"},
                    {"name": "Alice"},
                    provider_port=port,
                    app_settings=ctx.app_settings,
                )
        assert not calls
        assert db.in_transaction
    finally:
        db.rollback()


@pytest.mark.parametrize("key,value", [("kind", []), ("filename", 123), ("expires_at", float("nan")), ("fields", [])])
def test_corrupt_proposal_metadata_is_refused(card_context, key, value):
    db, ctx, _ = card_context
    original, _, nonce = prepare_replacement(card_context)
    state = asdict(proposals.load_character_proposal(db, "chat", nonce, request_context=ctx))
    state[key] = value
    set_meta(db, "character_proposal:" + nonce, json.dumps(state))
    with pytest.raises(ValueError):
        apply_proposal(card_context, nonce)
    assert (ctx.app_settings.character_dir / "Alice.png").read_bytes() == original


def test_complete_preview_is_paginated_with_safe_telegram_lengths(card_context, monkeypatch):
    _, ctx, _ = card_context
    delivered = []
    monkeypatch.setattr(
        panels, "send_panel_message", lambda _token, _chat, text, markup, *a, **k: delivered.append((text, markup))
    )
    fields = {key: key + "😀" * 700 for key in quality.OPTIMIZABLE_FIELDS}
    nonce = "a" * 24
    page = 0
    while True:
        panels.send_character_optimize_result(
            "token", "chat", "Alice.png", fields, nonce, 55, page, request_context=ctx
        )
        text, markup = delivered[-1]
        assert len(text.encode("utf-16-le")) // 2 <= 4096
        buttons = [b for row in markup["inline_keyboard"] for b in row]
        assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)
        next_button = next((b for b in buttons if b["text"] == "Next"), None)
        if next_button is None:
            break
        page = int(next_button["callback_data"].rsplit(":", 1)[1])
        assert page < 100
    joined = "".join(text.split("\n\n", 1)[1] for text, _ in delivered)
    for field, value in fields.items():
        assert f"{field}:\n{value}" in joined
    assert len(delivered) > 1


def test_consumed_proposal_metadata_does_not_accumulate_empty_rows(card_context):
    db, ctx, _ = card_context
    _original, _updated, nonce = prepare_replacement(card_context)
    apply_proposal(card_context, nonce, "keep")
    assert db.execute("SELECT key FROM meta WHERE key LIKE 'character_proposal:%'").fetchall() == []
    assert db.execute("SELECT key FROM meta WHERE key LIKE 'character_proposal_current:%'").fetchall() == []
    assert not list((ctx.app_settings.character_backup_dir / ".pending").glob("*.bin"))


def test_superseded_proposal_metadata_is_removed(card_context):
    db, _ctx, _ = card_context
    _original, _updated, nonce = prepare_replacement(card_context)
    upload(card_context, _card_png("Alice", "a later proposal"))
    assert db.execute("SELECT key FROM meta WHERE key=?", ("character_proposal:" + nonce,)).fetchone() is None
    assert db.execute("SELECT count(*) FROM meta WHERE key LIKE 'character_proposal:%'").fetchone()[0] == 1
