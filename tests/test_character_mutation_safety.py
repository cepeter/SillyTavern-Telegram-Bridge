"""Native-card edits must preserve approved identity and original bytes."""

from __future__ import annotations

import re
import struct
from dataclasses import replace
from pathlib import Path

import pytest
from test_character_upload_confirmation import _card_png

from bridge import character_quality as quality
from bridge import native_imports as imports
from bridge.card_content import character_card_paths
from bridge.request_types import RequestContext
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect


@pytest.fixture
def card_context(tmp_path, monkeypatch):
    settings = replace(
        load_app_settings({}, home=tmp_path),
        character_dir=tmp_path / "characters",
        character_backup_dir=tmp_path / "backups",
    )
    settings.character_dir.mkdir()
    db = db_connect(app_settings=settings)
    ctx = RequestContext(db, "session", "actor", app_settings=settings)
    panels = []
    monkeypatch.setattr(imports, "send_text", lambda *a, **k: [])
    monkeypatch.setattr(
        imports, "send_panel_request", lambda _token, _method, payload, **kwargs: panels.append(payload)
    )
    try:
        yield db, ctx, panels
    finally:
        db.close()


def upload(card_context, raw):
    db, ctx, panels = card_context
    imports.import_character_card(
        db, "token", "chat", "Alice.png", raw, app_settings=ctx.app_settings, request_context=ctx
    )
    return panels


@pytest.mark.parametrize(
    "text",
    ["Sorry, I cannot evaluate this.", "Cannot rank this.", "As an analyst, I need context.", "Badly formed output."],
)
def test_rank_parser_does_not_treat_prose_as_a_tier(text):
    assert quality.parse_rank(text) is None


def test_optimizer_cannot_rewrite_identity_or_structural_fields():
    card = {"data": {"name": "Alice", "description": "original", "extensions": {"x": 1}}}
    result = quality.merge_optimized_fields(card, {"name": "Other", "description": "better", "extensions": "broken"})
    assert result["data"]["name"] == "Alice"
    assert result["data"]["extensions"] == {"x": 1}
    assert result["data"]["description"] == "better"
    assert card["data"]["description"] == "original"


def test_optimizer_refuses_truncated_png_instead_of_dropping_bytes():
    raw = _card_png("Alice", "original") + struct.pack(">I", 900) + b"IDAT" + b"broken"
    with pytest.raises(ValueError):
        quality.write_png_chara_bytes(raw, {"name": "Alice"})


def test_pending_upload_is_not_a_visible_character(card_context):
    original, updated = _card_png("Alice", "original"), _card_png("Alice", "updated")
    upload(card_context, original)
    upload(card_context, updated)
    _, ctx, _ = card_context
    assert [p.name for p in character_card_paths(app_settings=ctx.app_settings)] == ["Alice.png"]


def test_upload_confirmation_has_unique_proposal_identity(card_context):
    upload(card_context, _card_png("Alice", "original"))
    panels = upload(card_context, _card_png("Alice", "updated"))
    callback = panels[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert re.fullmatch(r"characterupload:overwrite:[0-9a-f]{24}", callback)
    assert len(callback.encode()) <= 64


def test_overwrite_backup_is_the_original_not_the_new_upload(card_context):
    _db, ctx, _ = card_context
    original, updated = _card_png("Alice", "original"), _card_png("Alice", "updated")
    upload(card_context, original)
    upload(card_context, updated)
    apply_proposal(card_context, proposal_nonce(card_context))
    assert (ctx.app_settings.character_backup_dir / "Alice.png").read_bytes() == original
    assert (ctx.app_settings.character_dir / "Alice.png").read_bytes() == updated


def proposal_nonce(card_context):
    callback = card_context[2][-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    return callback.rsplit(":", 1)[1]


def apply_proposal(card_context, nonce, action="overwrite", *, context=None, chat_id="chat"):
    db, ctx, _ = card_context
    assert hasattr(imports, "apply_character_proposal"), "missing nonce-bound character mutation boundary"
    return imports.apply_character_proposal(db, chat_id, nonce, action, request_context=context or ctx)


def prepare_replacement(card_context):
    original, updated = _card_png("Alice", "original"), _card_png("Alice", "updated")
    upload(card_context, original)
    upload(card_context, updated)
    return original, updated, proposal_nonce(card_context)


@pytest.mark.parametrize("field,value", [("actor_id", "other"), ("session_id", "other-session")])
def test_proposal_refuses_different_actor_or_session(card_context, field, value):
    original, _, nonce = prepare_replacement(card_context)
    ctx = card_context[1]
    with pytest.raises(ValueError):
        apply_proposal(card_context, nonce, context=replace(ctx, **{field: value}))
    assert (ctx.app_settings.character_dir / "Alice.png").read_bytes() == original


def test_proposal_refuses_other_chat(card_context):
    original, _, nonce = prepare_replacement(card_context)
    with pytest.raises(ValueError):
        apply_proposal(card_context, nonce, chat_id="other-chat")
    assert (card_context[1].app_settings.character_dir / "Alice.png").read_bytes() == original


def test_old_confirmation_cannot_apply_a_newer_upload(card_context):
    original, _, first = prepare_replacement(card_context)
    newest = _card_png("Alice", "newest")
    upload(card_context, newest)
    current = proposal_nonce(card_context)
    assert current != first
    with pytest.raises(ValueError):
        apply_proposal(card_context, first)
    target = card_context[1].app_settings.character_dir / "Alice.png"
    assert target.read_bytes() == original
    apply_proposal(card_context, current)
    assert target.read_bytes() == newest


def test_preview_refuses_changed_original_and_preserves_it(card_context):
    _, _, nonce = prepare_replacement(card_context)
    target = card_context[1].app_settings.character_dir / "Alice.png"
    external = _card_png("Alice", "externally edited")
    target.write_bytes(external)
    with pytest.raises(ValueError, match="changed"):
        apply_proposal(card_context, nonce)
    assert target.read_bytes() == external


def test_proposal_is_single_use(card_context):
    _, updated, nonce = prepare_replacement(card_context)
    apply_proposal(card_context, nonce)
    with pytest.raises(ValueError):
        apply_proposal(card_context, nonce)
    assert (card_context[1].app_settings.character_dir / "Alice.png").read_bytes() == updated


def test_new_version_rechecks_catalog_limit(card_context, monkeypatch):
    original, _, nonce = prepare_replacement(card_context)
    monkeypatch.setattr(imports, "CATALOG_MAX_ITEMS", 1)
    with pytest.raises(ValueError, match="catalog"):
        apply_proposal(card_context, nonce, "newversion")
    assert (card_context[1].app_settings.character_dir / "Alice.png").read_bytes() == original


def test_replace_failure_preserves_original_and_removes_temporary_file(card_context, monkeypatch):
    import os

    original, _, nonce = prepare_replacement(card_context)
    target = card_context[1].app_settings.character_dir / "Alice.png"
    real_replace = os.replace

    def fail(source, destination, *args, **kwargs):
        if Path(destination) == target:
            raise OSError("fixture atomic replacement failed")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        apply_proposal(card_context, nonce)
    assert target.read_bytes() == original
    assert not list(target.parent.glob("*.tmp"))


def test_card_mutation_refuses_enclosing_database_transaction(card_context):
    original, _, nonce = prepare_replacement(card_context)
    db, ctx, _ = card_context
    db.execute("BEGIN")
    try:
        with pytest.raises(ValueError, match="transaction"):
            apply_proposal(card_context, nonce)
        assert db.in_transaction
        assert (ctx.app_settings.character_dir / "Alice.png").read_bytes() == original
    finally:
        db.rollback()


def test_corrupt_staged_bytes_are_not_installed(card_context):
    original, _, nonce = prepare_replacement(card_context)
    ctx = card_context[1]
    staged = ctx.app_settings.character_backup_dir / ".pending" / (nonce + ".bin")
    assert staged.exists(), "staged upload must live outside the visible card catalog"
    staged.write_bytes(_card_png("Other", "unapproved"))
    with pytest.raises(ValueError, match="checksum"):
        apply_proposal(card_context, nonce)
    assert (ctx.app_settings.character_dir / "Alice.png").read_bytes() == original


def test_unknown_action_does_not_consume_proposal(card_context):
    original, updated, nonce = prepare_replacement(card_context)
    with pytest.raises(ValueError):
        apply_proposal(card_context, nonce, "unexpected")
    assert (card_context[1].app_settings.character_dir / "Alice.png").read_bytes() == original
    apply_proposal(card_context, nonce)
    assert (card_context[1].app_settings.character_dir / "Alice.png").read_bytes() == updated
