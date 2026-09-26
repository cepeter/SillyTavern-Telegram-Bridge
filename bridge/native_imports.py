"""Native character and World Info installation and document upload routing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from bridge.card_content import card_fields, card_fields_from_file, parse_png_chara_bytes
from bridge.character_proposals import (
    discard_character_proposal,
    load_character_proposal,
    read_proposal_bytes,
    stage_character_proposal,
)
from bridge.character_quality import OPTIMIZABLE_FIELDS, merge_optimized_fields, rank_character, write_png_chara_bytes
from bridge.group_director_service import GroupDirectorService
from bridge.limits import CATALOG_MAX_ITEMS, RAG_MAX_FILE_BYTES, RAG_SUPPORTED_SUFFIXES
from bridge.memory_service import MemoryService
from bridge.metadata import get_meta, set_meta
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag_service import RagService
from bridge.request_types import RequestContext
from bridge.session_core import load_session
from bridge.settings import AppSettings
from bridge.telegram import download_telegram_file, send_panel_request, send_text
from bridge.world_storage import install_world_info_document


def verify_character_card_backup(target: Path, raw: bytes, *, app_settings: AppSettings) -> Path:
    app_settings.character_backup_dir.mkdir(parents=True, exist_ok=True)
    backup = app_settings.character_backup_dir / target.name
    versioned = app_settings.character_backup_dir / f"{target.stem}.{time.time_ns()}{target.suffix}"
    for destination in (versioned, backup):
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(raw)
        if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(raw).digest():
            temporary.unlink(missing_ok=True)
            raise OSError("character-card backup checksum verification failed")
        temporary.replace(destination)
    return backup


def character_backup_versions(name: str, *, app_settings: AppSettings) -> list[Path]:
    target = Path(name)
    return sorted(app_settings.character_backup_dir.glob(f"{target.stem}.*{target.suffix}"), reverse=True)


def character_delete_references(db: sqlite3.Connection, filename: str) -> list[str]:
    references = []
    for chat_id, session_id in db.execute(
        "SELECT chat_id,session_id FROM sessions WHERE character_file=?", (filename,)
    ).fetchall():
        references.append(f"session:{chat_id}/{session_id}")
    for chat_id, session_id, members_json in db.execute(
        "SELECT chat_id,session_id,members_json FROM group_sessions"
    ).fetchall():
        try:
            members = json.loads(members_json or "[]")
        except json.JSONDecodeError:
            members = []
        if filename in members:
            references.append(f"group:{chat_id}/{session_id}")
    return references


def prune_character_backups(name: str, keep: int = 10, *, app_settings: AppSettings) -> None:
    versions = character_backup_versions(name, app_settings=app_settings)
    for stale in versions[keep:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            logging.warning("Could not prune character backup %s", stale, exc_info=True)


_CHARACTER_WRITE_LOCK = threading.RLock()


def _character_target(filename: str, *, app_settings: AppSettings) -> Path:
    if Path(filename).name != filename or filename.startswith(".") or Path(filename).suffix.casefold() != ".png":
        raise ValueError("invalid character filename")
    target = app_settings.character_dir / filename
    if target.is_symlink() or target.parent.resolve() != app_settings.character_dir.resolve():
        raise ValueError("character path must be an ordinary file in the character directory")
    return target


def _current_digest(target: Path) -> str:
    if not target.exists():
        return ""
    if target.is_symlink() or not target.is_file() or target.stat().st_size > RAG_MAX_FILE_BYTES:
        raise ValueError("character file is unavailable or exceeds the size limit")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _install_character_bytes(
    filename: str,
    raw: bytes,
    expected_digest: str,
    *,
    app_settings: AppSettings,
) -> Path:
    """One in-process serialized, backed-up compare-and-replace of a card."""
    if len(raw) > RAG_MAX_FILE_BYTES:
        raise ValueError("character file exceeds the size limit")
    with _CHARACTER_WRITE_LOCK:
        target = _character_target(filename, app_settings=app_settings)
        app_settings.character_dir.mkdir(parents=True, exist_ok=True)
        if _current_digest(target) != expected_digest:
            raise ValueError("character changed since preview; reopen it")
        if not target.exists():
            count = sum(
                1
                for path in app_settings.character_dir.glob("*.png")
                if path.is_file() and not path.name.startswith(".")
            )
            if count >= CATALOG_MAX_ITEMS:
                raise ValueError("character catalog is full; delete a card before adding a version")
        original = target.read_bytes() if target.exists() else raw
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.chmod(temporary, mode)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(raw).digest():
                raise OSError("character temporary-write checksum mismatch")
            backup = verify_character_card_backup(target, original, app_settings=app_settings)
            # Recheck after backup I/O so an intervening file change is not lost.
            if _current_digest(target) != expected_digest:
                raise ValueError("character changed since preview; reopen it")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        try:
            prune_character_backups(target.name, app_settings=app_settings)
        except OSError:
            logging.warning("Character backup pruning deferred")
        return backup


def apply_character_proposal(
    db: sqlite3.Connection,
    chat_id: str,
    nonce: str,
    action: str,
    *,
    request_context: RequestContext,
) -> tuple[str, str | None]:
    """Consume the exact approved proposal, then atomically replace/install it."""
    app_settings = request_context.app_settings
    with _CHARACTER_WRITE_LOCK:
        state = load_character_proposal(db, chat_id, nonce, request_context=request_context)
        allowed = {"overwrite", "newversion", "keep"} if state.kind == "upload" else {"apply", "cancel"}
        if action not in allowed:
            raise ValueError("invalid character proposal action")
        if action in {"keep", "cancel"}:
            discard_character_proposal(db, chat_id, nonce, request_context=request_context)
            return "Kept the existing character card.", None
        target = _character_target(state.filename, app_settings=app_settings)
        if _current_digest(target) != state.expected_digest:
            raise ValueError("character changed since preview; reopen it")
        raw = read_proposal_bytes(state, request_context=request_context)
        fields = card_fields(parse_png_chara_bytes(raw), app_settings=app_settings)
        expected_digest = state.expected_digest
        if action == "newversion":
            target = _character_target(f"{target.stem}-{state.content_digest[:8]}.png", app_settings=app_settings)
            if target.exists():
                raise ValueError("this character version already exists")
            count = sum(
                1
                for path in app_settings.character_dir.glob("*.png")
                if path.is_file() and not path.name.startswith(".")
            )
            if count >= CATALOG_MAX_ITEMS:
                raise ValueError("character catalog is full; delete a card before adding a version")
            expected_digest = ""
        if state.kind == "optimize":
            if not set(state.fields) <= set(OPTIMIZABLE_FIELDS):
                raise ValueError("optimizer preview contains unsupported fields")
            original = target.read_bytes()
            expected = write_png_chara_bytes(
                original, merge_optimized_fields(parse_png_chara_bytes(original), state.fields)
            )
            if raw != expected:
                raise ValueError("optimizer preview does not match the approved card bytes")
        # Consume first: a crash may require a new preview, but never replay an
        # already-approved file mutation. Filesystem and SQLite are not one transaction.
        discard_character_proposal(db, chat_id, nonce, request_context=request_context)
        backup = _install_character_bytes(target.name, raw, expected_digest, app_settings=app_settings)
    verb = "installed" if action == "newversion" else ("overwritten" if action == "overwrite" else "optimized")
    return (
        f"Character card {verb}: {fields['name']} ({target.name}). Original backup verified: {backup.name}.",
        target.name,
    )


def import_character_card(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    filename: str,
    raw: bytes,
    *,
    app_settings: AppSettings,
    request_context: RequestContext,
) -> Path | None:
    if request_context.db is not db or request_context.app_settings != app_settings or not request_context.actor_id:
        raise ValueError("character import requires the request's actor and database context")
    if db.in_transaction:
        raise ValueError("character file import cannot join a database transaction")
    if len(raw) > RAG_MAX_FILE_BYTES:
        send_text(token, chat_id, "Character card is too large. The limit is 10 MB.")
        return None
    try:
        fields = card_fields(parse_png_chara_bytes(raw), app_settings=app_settings)
    except (ValueError, TypeError, AttributeError, KeyError):
        send_text(token, chat_id, "This PNG is not a valid SillyTavern character card; chara metadata was not found.")
        return None
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in fields["name"]).strip("_") or "character"
    if len(stem.encode("utf-8")) > 80:
        stem = (
            stem.encode("utf-8")[:64].decode("utf-8", "ignore").rstrip("_-") + "-" + hashlib.sha256(raw).hexdigest()[:8]
        )
    target = _character_target(f"{stem}.png", app_settings=app_settings)
    try:
        with _CHARACTER_WRITE_LOCK:
            expected_digest = _current_digest(target)
            if expected_digest:
                nonce = stage_character_proposal(
                    db,
                    chat_id,
                    "upload",
                    target.name,
                    raw,
                    expected_digest,
                    request_context=request_context,
                )
                pending = True
            else:
                backup = _install_character_bytes(target.name, raw, "", app_settings=app_settings)
                pending = False
    except (OSError, ValueError) as exc:
        message = (
            f"Character catalog is full ({CATALOG_MAX_ITEMS} maximum). Delete one before uploading another."
            if "catalog" in str(exc)
            else "Character card could not be installed or staged; the existing card was preserved."
        )
        send_text(token, chat_id, message)
        return None
    if pending:
        send_panel_request(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    f'Character card "{fields["name"]}" already exists as {target.name}.\n\n'
                    "Overwrite it, keep it, or install this upload as a new version?"
                ),
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Overwrite", "callback_data": f"characterupload:overwrite:{nonce}"},
                            {"text": "New version", "callback_data": f"characterupload:newversion:{nonce}"},
                        ],
                        [{"text": "Keep existing", "callback_data": f"characterupload:keep:{nonce}"}],
                    ]
                },
            },
            request_context=request_context,
        )
        return None
    send_text(
        token, chat_id, f"Character card imported: {fields['name']} ({target.name}). Backup verified: {backup.name}."
    )
    return target


def _consume_world_upload(db: sqlite3.Connection, chat_id: str) -> bool:
    raw_state = get_meta(db, f"world_upload:{chat_id}")
    if not raw_state:
        return False
    set_meta(db, f"world_upload:{chat_id}", "")
    try:
        state = json.loads(raw_state)
    except json.JSONDecodeError:
        return False
    if float(state.get("expires_at", 0)) < time.time():
        return False
    return True


def import_world_info_document(
    db: sqlite3.Connection, token: str, chat_id: str, filename: str, raw: bytes, *, app_settings: AppSettings
) -> None:
    try:
        target = install_world_info_document(filename, raw, app_settings=app_settings)
    except FileExistsError:
        send_text(
            token, chat_id, f"World Info already exists: {Path(filename).name}. Delete it first, then upload again."
        )
    except (OSError, ValueError) as exc:
        send_text(token, chat_id, f"World Info upload refused: {exc}")
    else:
        send_text(token, chat_id, f"World Info imported: {target.name}. Open /world to enable it.")


def import_telegram_document(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    document: dict,
    default_model: str,
    telegram_message_id: int | None = None,
    *,
    api_key: str,
    process_image: Callable[..., None],
    memory_service: MemoryService,
    persona_service: PersonaService,
    group_director_service: GroupDirectorService,
    app_settings: AppSettings,
    rag_service: RagService,
    provider_port: ProviderPort,
    request_context: RequestContext,
) -> None:
    filename = str(document.get("file_name") or "document")
    suffix = Path(filename).suffix.casefold()
    file_size = int(document.get("file_size") or 0)
    if _consume_world_upload(db, chat_id):
        if suffix != ".json":
            send_text(token, chat_id, "World Info upload expects a .json document. Use /world and try again.")
            return
        if file_size > RAG_MAX_FILE_BYTES:
            send_text(token, chat_id, "World Info file is too large. The limit is 10 MB.")
            return
        raw = download_telegram_file(token, str(document.get("file_id") or ""), RAG_MAX_FILE_BYTES)
        import_world_info_document(db, token, chat_id, filename, raw, app_settings=app_settings)
        return
    if suffix == ".png":
        raw = download_telegram_file(token, str(document.get("file_id") or ""), RAG_MAX_FILE_BYTES)
        try:
            parse_png_chara_bytes(raw)
        except Exception:
            session = load_session(db, chat_id, request_context.session_id, default_model, app_settings=app_settings)
            fields = card_fields_from_file(session["character_file"], app_settings=app_settings)
            process_image(
                db,
                token,
                api_key,
                session,
                fields,
                chat_id,
                str(document.get("caption") or ""),
                raw,
                mime_type="image/png",
                telegram_message_id=telegram_message_id,
                memory_service=memory_service,
                persona_service=persona_service,
                group_director_service=group_director_service,
            )
        else:
            installed = import_character_card(
                db,
                token,
                chat_id,
                filename,
                raw,
                app_settings=app_settings,
                request_context=request_context,
            )
            if installed is not None:
                session = load_session(
                    db, chat_id, request_context.session_id, default_model, app_settings=app_settings
                )
                rank_character(
                    db,
                    chat_id,
                    session,
                    card_fields_from_file(installed.name, app_settings=app_settings),
                    installed.name,
                    provider_port=provider_port,
                    app_settings=app_settings,
                )
        return
    if suffix not in RAG_SUPPORTED_SUFFIXES:
        send_text(
            token, chat_id, "Unsupported Data Bank format. Use PDF, TXT, MD, JSON, YAML, CSV, HTML, XML, or DOCX."
        )
        return
    if file_size > RAG_MAX_FILE_BYTES:
        send_text(token, chat_id, "Data Bank file is too large. The limit is 10 MB.")
        return
    raw = download_telegram_file(token, str(document.get("file_id")), RAG_MAX_FILE_BYTES)
    status, chunks = rag_service.add_document(db, chat_id, filename, raw)
    if status == "duplicate":
        send_text(token, chat_id, f"Data Bank already contains {filename} ({chunks} chunks).")
    elif status == "versioned":
        versions = rag_service.versions(db, chat_id, filename)
        active_version = next((int(row[1]) for row in versions if row[2]), len(versions))
        send_text(
            token,
            chat_id,
            (
                "Added "
                f"""{filename}"""
                " v"
                f"""{active_version}"""
                " ("
                f"""{chunks}"""
                " chunks). Previous versions are retained but excluded from RAG."
            ),
        )
    else:
        send_text(
            token, chat_id, f"Added {filename} to Data Bank ({chunks} chunks). RAG is {rag_service.mode(db, chat_id)}."
        )
