"""Character quality ranking (S–D) and LLM card optimization.

Ranking and optimization are utility-model tasks. They never touch the
roleplay transcript: they read a card, ask the utility model, and (for
optimization) produce improved text fields that the caller may write back.

This module is deliberately free of file-system and backup side effects so it
can sit below the native-import owner without import cycles. The pure PNG
re-encoding helpers (`write_png_chara_bytes`, `merge_optimized_fields`) are
exported for `bridge.native_imports` to combine with its verified-backup flow.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3
import struct
import zlib

import bridge.limits as _limits
from bridge.metadata import get_meta, set_meta
from bridge.model_selection import task_model_for_session
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction

# Rank tiers, best to worst. Telegram inline-keyboard labels cannot animate,
# so each tier is conveyed with a static emoji badge plus the letter.
RANK_TIERS = ("S", "A", "B", "C", "D")
RANK_BADGES = {"S": "🏆", "A": "🥇", "B": "🥈", "C": "🥉", "D": "⚪"}

# A rank is a property of the card file, not of any chat/session, so it is
# stored in the global meta table keyed by filename.
RANK_META_PREFIX = "character_rank:"

# The optimizer rewrites text fields only; identity and structural fields
# (name, avatar, character book, extensions) are preserved.
OPTIMIZABLE_FIELDS = (
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "system_prompt",
    "post_history_instructions",
)

_RANK_MAX_TOKENS = 120
_OPTIMIZE_MAX_TOKENS = 4000

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def rank_badge(rank: str | None) -> str:
    """Return the display badge for a rank, or "" when unranked/unknown."""
    tier = str(rank or "").strip().upper()
    if tier not in RANK_BADGES:
        return ""
    return f"{RANK_BADGES[tier]}{tier} "


def character_rank(db: sqlite3.Connection, filename: str) -> str:
    try:
        return get_meta(db, RANK_META_PREFIX + filename, "")
    except sqlite3.OperationalError:
        # The meta table is absent in minimal fixtures; an unranked card is
        # indistinguishable from "no rank stored yet".
        return ""


def store_character_rank(db: sqlite3.Connection, filename: str, rank: str) -> None:
    tier = str(rank or "").strip().upper()
    if tier not in RANK_TIERS:
        return
    with write_transaction(db):
        set_meta(db, RANK_META_PREFIX + filename, tier)


def parse_rank(raw: str | None) -> str | None:
    """Extract an S/A/B/C/D tier from a utility-model reply, or None.

    The prompt instructs the model to lead with the letter, so the first
    character is the primary signal. A "Rank: X"-style prefix is also accepted.
    No loose scan is used: a standalone "a" (the article) must not rank a card.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    first = text[0].upper()
    if first in RANK_TIERS:
        return first
    match = re.match(r"(?i)^(?:rank|tier|quality|grade)\s*[:=-]?\s*([SABCD])\b", text)
    return match.group(1).upper() if match else None


def _card_snapshot(fields: dict[str, str]) -> str:
    lines = [f"{key}: {value or '(empty)'}" for key, value in fields.items()]
    return "\n".join(lines)


def rank_prompt(fields: dict[str, str]) -> list[dict]:
    system = (
        "You are a character card analyst. Evaluate the given character card "
        "and assign a single quality tier. Reply with only the tier letter "
        "and one short sentence justifying it."
    )
    user = (
        "Assign a quality tier from S, A, B, C, or D to this character card.\n"
        "Tiers:\n"
        "- S: exceptional — rich, consistent, distinctive, engaging.\n"
        "- A: strong — good depth and voice with minor gaps.\n"
        "- B: solid — functional but generic or underdeveloped.\n"
        "- C: weak — thin, inconsistent, or low-effort.\n"
        "- D: poor — incoherent, placeholder, or broken.\n\n"
        "Character card:\n<card>\n" + _card_snapshot(fields) + "\n</card>\n\n"
        "Reply with the tier letter and a short justification, e.g. "
        '"S — the personality is distinctive and the first message hooks the reader."'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def optimize_prompt(fields: dict[str, str]) -> list[dict]:
    system = (
        "You are a character card editor. Rewrite the character card to raise "
        "its quality while preserving its core identity, voice, and unique traits."
    )
    user = (
        "Rewrite this character card to improve its quality. Preserve the "
        "character's core identity, personality, distinctive voice, and any lore "
        "that matters. Fix contradictions, fill gaps, and sharpen weak areas. "
        "Keep the same language as the original and do not change the name or "
        "introduce facts that contradict the original. Empty fields may stay empty.\n\n"
        "Return the result as a JSON object with exactly these keys:\n"
        '{"description", "personality", "scenario", "first_mes", "mes_example", '
        '"system_prompt", "post_history_instructions"}\n\n'
        "Output only the JSON object.\n\n"
        "Character card:\n<card>\n" + _card_snapshot(fields) + "\n</card>"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_optimized_fields(raw: str | None) -> dict[str, str] | None:
    """Parse the optimizer's JSON reply into a subset of text fields."""
    text = str(raw or "").strip()
    if not text:
        return None
    fenced = _JSON_FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    result: dict[str, str] = {}
    for field in OPTIMIZABLE_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            result[field] = value.strip()[: _limits.CARD_FIELD_MAX_CHARS]
    return result or None


def merge_optimized_fields(card: dict, optimized: dict[str, str]) -> dict:
    """Merge optimized text fields into a parsed card, preserving structure."""
    container = card.get("data") if isinstance(card.get("data"), dict) else card
    for field, value in optimized.items():
        if value is not None:
            container[field] = str(value)[: _limits.CARD_FIELD_MAX_CHARS]
    return card


def write_png_chara_bytes(raw: bytes, card: dict) -> bytes:
    """Re-encode a PNG, replacing the SillyTavern `chara` tEXt chunk in place."""
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("character file is not a PNG")
    encoded = base64.b64encode(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    new_chunk_data = b"chara\x00" + encoded
    new_chunk = (
        struct.pack(">I", len(new_chunk_data))
        + b"tEXt"
        + new_chunk_data
        + struct.pack(">I", zlib.crc32(b"tEXt" + new_chunk_data) & 0xFFFFFFFF)
    )
    pos = 8
    chunks: list[bytes] = []
    replaced = False
    while pos + 12 <= len(raw):
        size = struct.unpack(">I", raw[pos : pos + 4])[0]
        chunk_type = raw[pos + 4 : pos + 8]
        chunk = raw[pos + 8 : pos + 8 + size]
        total = 12 + size
        if chunk_type == b"tEXt" and chunk.startswith(b"chara\x00"):
            chunks.append(new_chunk)
            replaced = True
        else:
            chunks.append(raw[pos : pos + total])
        pos += total
    if not replaced:
        raise ValueError("PNG has no SillyTavern chara metadata")
    return raw[:8] + b"".join(chunks)


def _utility_model(db: sqlite3.Connection, chat_id: str, session: dict, *, app_settings: AppSettings) -> str:
    return task_model_for_session(db, chat_id, session, "utility", app_settings=app_settings)


def rank_character(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    fields: dict[str, str],
    filename: str,
    *,
    provider_port,
    app_settings: AppSettings,
) -> str | None:
    """Ask the utility model for a tier and persist it. Best-effort."""
    settings = {
        "temperature": 0.0,
        "max_tokens": _RANK_MAX_TOKENS,
        "reasoning_budget": 0,
        "stop_sequences": "",
    }
    try:
        model = _utility_model(db, chat_id, session, app_settings=app_settings)
        raw = provider_port.generate(
            "",
            model,
            rank_prompt(fields),
            session_id=f"character-rank:{filename}",
            settings=settings,
            force_non_stream=True,
        )
    except Exception:
        logging.warning("Character rank failed for %s", filename, exc_info=True)
        return None
    rank = parse_rank(raw)
    if rank:
        store_character_rank(db, filename, rank)
    return rank


def optimize_character(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    fields: dict[str, str],
    *,
    provider_port,
    app_settings: AppSettings,
) -> dict[str, str] | None:
    """Ask the utility model for an improved card and return its text fields."""
    settings = {
        "temperature": 0.4,
        "max_tokens": _OPTIMIZE_MAX_TOKENS,
        "reasoning_budget": 0,
        "stop_sequences": "",
    }
    try:
        model = _utility_model(db, chat_id, session, app_settings=app_settings)
        raw = provider_port.generate(
            "",
            model,
            optimize_prompt(fields),
            session_id=f"character-optimize:{fields.get('name', 'card')}",
            settings=settings,
            force_non_stream=True,
        )
    except Exception:
        logging.warning("Character optimize failed", exc_info=True)
        return None
    return parse_optimized_fields(raw)
