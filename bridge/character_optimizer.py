"""Character optimizer application use case, independent of Telegram panels."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from bridge.card_content import card_fields, parse_png_chara_bytes, safe_character_path
from bridge.character_proposals import stage_character_proposal
from bridge.character_quality import merge_optimized_fields, optimize_character, write_png_chara_bytes
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext


@dataclass(frozen=True)
class CharacterOptimizationDraft:
    filename: str
    fields: dict[str, str]
    nonce: str


def prepare_character_optimization(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    filename: str,
    *,
    provider_port: ProviderPort,
    request_context: RequestContext,
    suggestion: str = "",
    expected_digest: str | None = None,
) -> CharacterOptimizationDraft:
    """Create one digest-bound optimizer proposal without changing the installed card."""
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    if path is None or path.is_symlink():
        raise ValueError("Character is unavailable; reopen the optimizer.")
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise ValueError("Character changed since the suggestion panel opened; reopen the optimizer.")
    card = parse_png_chara_bytes(original)
    info = card_fields(card, app_settings=request_context.app_settings)
    optimized = optimize_character(
        db,
        chat_id,
        session,
        info,
        provider_port=provider_port,
        app_settings=request_context.app_settings,
        suggestion=suggestion,
    )
    if not optimized:
        raise ValueError("Optimization unavailable. Check the utility-model configuration and retry.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Character changed during optimization; reopen the preview.")
    candidate = write_png_chara_bytes(original, merge_optimized_fields(card, optimized))
    nonce = stage_character_proposal(
        db,
        chat_id,
        "optimize",
        filename,
        candidate,
        digest,
        request_context=request_context,
        fields=optimized,
    )
    return CharacterOptimizationDraft(filename, optimized, nonce)
