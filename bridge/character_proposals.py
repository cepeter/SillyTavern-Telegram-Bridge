"""Single-use, actor/session-bound native-card proposals and private staging."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bridge.limits import CARD_TOTAL_MAX_CHARS, PENDING_SETTINGS_TTL_SECONDS, RAG_MAX_FILE_BYTES
from bridge.meta_repository import delete_meta_value
from bridge.metadata import get_meta, set_meta
from bridge.request_types import RequestContext
from bridge.sqlite_store import write_transaction

_META = "character_proposal:"
_CURRENT = "character_proposal_current:"
_NONCE = re.compile(r"[0-9a-f]{24}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CharacterProposal:
    nonce: str
    kind: str
    chat_id: str
    session_id: str
    actor_id: str
    filename: str
    expected_digest: str
    content_digest: str
    expires_at: float
    fields: dict[str, str] = field(default_factory=dict)


def _require_context(db: sqlite3.Connection, context: RequestContext) -> None:
    if context.db is not db or not context.actor_id or not context.session_id:
        raise ValueError("character proposals require an identified actor and session")
    if db.in_transaction:
        raise ValueError("character file operations cannot join a database transaction")


def _scope(chat_id: str, kind: str, context: RequestContext) -> str:
    value = json.dumps([chat_id, context.session_id, context.actor_id, kind])
    return _CURRENT + hashlib.sha256(value.encode()).hexdigest()


def _path(nonce: str, context: RequestContext) -> Path:
    if not _NONCE.fullmatch(nonce):
        raise ValueError("invalid character proposal token")
    directory = context.app_settings.character_backup_dir / ".pending"
    if directory.is_symlink():
        raise ValueError("pending character directory must not be a symlink")
    return directory / f"{nonce}.bin"


def _remove_file(nonce: str, context: RequestContext) -> None:
    try:
        _path(nonce, context).unlink(missing_ok=True)
    except OSError:
        logging.warning("Pending character file cleanup deferred")


def _prune_expired(context: RequestContext) -> None:
    directory = context.app_settings.character_backup_dir / ".pending"
    cutoff = time.time() - PENDING_SETTINGS_TTL_SECONDS
    for path in directory.glob("*.bin"):
        if _NONCE.fullmatch(path.stem) and not path.is_symlink():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                logging.warning("Expired character staging cleanup deferred")


def stage_character_proposal(
    db: sqlite3.Connection,
    chat_id: str,
    kind: str,
    filename: str,
    raw: bytes,
    expected_digest: str,
    *,
    request_context: RequestContext,
    fields: dict[str, str] | None = None,
) -> str:
    _require_context(db, request_context)
    if kind not in {"upload", "optimize"} or not _DIGEST.fullmatch(expected_digest):
        raise ValueError("invalid character proposal")
    if Path(filename).name != filename or not filename.endswith(".png") or len(raw) > RAG_MAX_FILE_BYTES:
        raise ValueError("invalid character proposal file")
    preview = dict(fields or {})
    if any(not isinstance(v, str) for v in preview.values()) or sum(map(len, preview.values())) > CARD_TOTAL_MAX_CHARS:
        raise ValueError("invalid character proposal preview")
    nonce = secrets.token_hex(12)
    path = _path(nonce, request_context)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _prune_expired(request_context)
    scope = _scope(chat_id, kind, request_context)
    previous = get_meta(db, scope, "")
    state = CharacterProposal(
        nonce,
        kind,
        chat_id,
        request_context.session_id,
        request_context.actor_id,
        filename,
        expected_digest,
        hashlib.sha256(raw).hexdigest(),
        time.time() + PENDING_SETTINGS_TTL_SECONDS,
        preview,
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with write_transaction(db):
            set_meta(db, _META + nonce, json.dumps(asdict(state), ensure_ascii=False))
            set_meta(db, scope, nonce)
            if _NONCE.fullmatch(previous):
                delete_meta_value(db, _META + previous)
    except BaseException:
        _remove_file(nonce, request_context)
        raise
    if _NONCE.fullmatch(previous):
        _remove_file(previous, request_context)
    return nonce


def load_character_proposal(
    db: sqlite3.Connection,
    chat_id: str,
    nonce: str,
    *,
    request_context: RequestContext,
) -> CharacterProposal:
    _require_context(db, request_context)
    _path(nonce, request_context)
    encoded = get_meta(db, _META + nonce, "")
    if not encoded or len(encoded) > 200000:
        raise ValueError("character proposal expired or already used; reopen it")
    try:
        value = json.loads(encoded)
        if not isinstance(value, dict):
            raise ValueError("invalid state")
        state = CharacterProposal(**value)
        if any(
            not isinstance(getattr(state, key), str)
            for key in (
                "nonce",
                "kind",
                "chat_id",
                "session_id",
                "actor_id",
                "filename",
                "expected_digest",
                "content_digest",
            )
        ):
            raise ValueError("invalid proposal values")
        if not isinstance(state.fields, dict) or any(not isinstance(v, str) for v in state.fields.values()):
            raise ValueError("invalid preview")
        if sum(map(len, state.fields.values())) > CARD_TOTAL_MAX_CHARS:
            raise ValueError("oversized preview")
        if not math.isfinite(state.expires_at):
            raise ValueError("invalid expiry")
        if not _DIGEST.fullmatch(state.expected_digest) or not _DIGEST.fullmatch(state.content_digest):
            raise ValueError("invalid digest")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid character proposal; reopen it") from None
    if (state.nonce, state.chat_id, state.session_id, state.actor_id) != (
        nonce,
        chat_id,
        request_context.session_id,
        request_context.actor_id,
    ) or state.kind not in {"upload", "optimize"}:
        raise ValueError("character proposal belongs to another actor or session")
    if Path(state.filename).name != state.filename or not state.filename.endswith(".png"):
        raise ValueError("invalid character proposal path")
    if state.expires_at <= time.time() or get_meta(db, _scope(chat_id, state.kind, request_context)) != nonce:
        raise ValueError("character proposal expired or superseded; reopen it")
    return state


def read_proposal_bytes(state: CharacterProposal, *, request_context: RequestContext) -> bytes:
    path = _path(state.nonce, request_context)
    if path.is_symlink() or not path.is_file():
        raise ValueError("character proposal file is missing or invalid")
    with path.open("rb") as stream:
        raw = stream.read(RAG_MAX_FILE_BYTES + 1)
    if len(raw) > RAG_MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != state.content_digest:
        raise ValueError("character proposal checksum mismatch")
    return raw


def discard_character_proposal(
    db: sqlite3.Connection,
    chat_id: str,
    nonce: str,
    *,
    request_context: RequestContext,
) -> None:
    state = load_character_proposal(db, chat_id, nonce, request_context=request_context)
    with write_transaction(db):
        delete_meta_value(db, _META + nonce)
        delete_meta_value(db, _scope(chat_id, state.kind, request_context))
    _remove_file(nonce, request_context)
