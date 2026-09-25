"""Native character and World Info installation and document upload routing."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from bridge.card_content import card_fields, card_fields_from_file, parse_png_chara_bytes
from bridge.character_quality import merge_optimized_fields, rank_character, write_png_chara_bytes
from bridge.group_director_service import GroupDirectorService
from bridge.limits import CATALOG_MAX_ITEMS, PENDING_SETTINGS_TTL_SECONDS, RAG_MAX_FILE_BYTES, RAG_SUPPORTED_SUFFIXES
from bridge.memory_service import MemoryService
from bridge.metadata import get_meta, set_meta
from bridge.persona_service import PersonaService
from bridge.rag_query import rag_mode
from bridge.rag_repository import data_bank_document_versions
from bridge.rag_service import RagService
from bridge.request_types import RequestContext
from bridge.session_core import ensure_session
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


def apply_optimized_character_card(path: Path, optimized: dict[str, str], *, app_settings: AppSettings) -> Path:
    """Write optimized text fields back into a card, with a verified backup."""
    original = path.read_bytes()
    card = merge_optimized_fields(parse_png_chara_bytes(original), optimized)
    new_bytes = write_png_chara_bytes(original, card)
    backup = verify_character_card_backup(path, original, app_settings=app_settings)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(new_bytes)
    if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(new_bytes).digest():
        temporary.unlink(missing_ok=True)
        raise OSError("optimized character card checksum verification failed")
    temporary.replace(path)
    prune_character_backups(path.name, app_settings=app_settings)
    return backup
_PENDING_UPLOAD_META = "character_upload_pending:"


def _pending_upload_path(chat_id: str, *, app_settings: AppSettings) -> Path:
    return app_settings.character_dir / f".pending_{chat_id}.png"


def _stage_pending_upload(
    db: sqlite3.Connection, chat_id: str, stem: str, raw: bytes, *, app_settings: AppSettings
) -> None:
    app_settings.character_dir.mkdir(parents=True, exist_ok=True)
    _pending_upload_path(chat_id, app_settings=app_settings).write_bytes(raw)
    set_meta(
        db,
        _PENDING_UPLOAD_META + chat_id,
        json.dumps({"stem": stem, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}),
    )


def _clear_pending_upload(db: sqlite3.Connection, chat_id: str, *, app_settings: AppSettings) -> None:
    set_meta(db, _PENDING_UPLOAD_META + chat_id, "")
    _pending_upload_path(chat_id, app_settings=app_settings).unlink(missing_ok=True)


def _send_upload_confirmation(
    token: str,
    chat_id: str,
    name: str,
    target_name: str,
    *,
    request_context: RequestContext,
) -> None:
    send_panel_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": (
                f'Character card "{name}" already exists as {target_name}.\n\n'
                "Overwrite it, keep the existing card, or save this upload as a new version?"
            ),
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "♻️ Overwrite", "callback_data": "characterupload:overwrite"},
                        {"text": "📁 New version", "callback_data": "characterupload:newversion"},
                    ],
                    [
                        {"text": "❌ Keep existing", "callback_data": "characterupload:keep"},
                    ],
                ]
            },
        },
        request_context=request_context,
    )


def apply_pending_upload(
    db: sqlite3.Connection,
    chat_id: str,
    action: str,
    *,
    app_settings: AppSettings,
) -> str:
    """Apply (overwrite / new version) or discard a staged upload. Returns a status message."""
    raw_state = get_meta(db, _PENDING_UPLOAD_META + chat_id)
    if not raw_state:
        return "No pending character upload; it may have expired. Upload the card again."
    try:
        state = json.loads(raw_state)
    except json.JSONDecodeError:
        return "Pending character upload was invalid; upload the card again."
    if float(state.get("expires_at", 0)) < time.time():
        _clear_pending_upload(db, chat_id, app_settings=app_settings)
        return "Pending character upload expired; upload the card again."
    stem = str(state.get("stem") or "")
    pending = _pending_upload_path(chat_id, app_settings=app_settings)
    if not pending.exists():
        _clear_pending_upload(db, chat_id, app_settings=app_settings)
        return "Pending character upload file is missing; upload the card again."
    raw = pending.read_bytes()
    if action == "keep":
        _clear_pending_upload(db, chat_id, app_settings=app_settings)
        return f'Kept the existing "{stem}" card.'
    target = app_settings.character_dir / f"{stem}.png"
    if action == "newversion":
        target = app_settings.character_dir / f"{stem}-{hashlib.sha256(raw).hexdigest()[:8]}.png"
    try:
        fields = card_fields(parse_png_chara_bytes(raw), app_settings=app_settings)
        backup = verify_character_card_backup(target, raw, app_settings=app_settings)
        target.write_bytes(raw)
        prune_character_backups(target.name, app_settings=app_settings)
    except (OSError, ValueError):
        _clear_pending_upload(db, chat_id, app_settings=app_settings)
        return "Character card write failed; no changes were made."
    _clear_pending_upload(db, chat_id, app_settings=app_settings)
    verb = "overwritten" if action == "overwrite" else "installed"
    return f"Character card {verb}: {fields['name']} ({target.name}). Backup verified: {backup.name}."


def import_character_card(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    filename: str,
    raw: bytes,
    *,
    app_settings: AppSettings,
    default_model: str = "",
    provider_port=None,
    request_context=None,
) -> None:
    if len(raw) > RAG_MAX_FILE_BYTES:
        send_text(token, chat_id, "Character card is too large. The limit is 10 MB.")
        return
    try:
        fields = card_fields(parse_png_chara_bytes(raw), app_settings=app_settings)
    except Exception:
        send_text(token, chat_id, "This PNG is not a valid SillyTavern character card; chara metadata was not found.")
        return
    stem = (
        "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in fields["name"]).strip("_")
        or Path(filename).stem
        or "character"
    )
    if len(stem.encode("utf-8")) > 80:
        stem = (
            stem.encode("utf-8")[:64].decode("utf-8", "ignore").rstrip("_-") + "-" + hashlib.sha256(raw).hexdigest()[:8]
        )
    target = app_settings.character_dir / f"{stem}.png"
    installed_count = (
        sum(1 for path in app_settings.character_dir.glob("*.png") if path.is_file())
        if app_settings.character_dir.exists()
        else 0
    )
    if not target.exists() and installed_count >= CATALOG_MAX_ITEMS:
        send_text(
            token,
            chat_id,
            f"Character catalog is full ({CATALOG_MAX_ITEMS} maximum). Delete one before uploading another.",
        )
        return
    app_settings.character_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _stage_pending_upload(db, chat_id, stem, raw, app_settings=app_settings)
        if request_context is not None:
            _send_upload_confirmation(
                token,
                chat_id,
                fields["name"],
                target.name,
                request_context=request_context,
            )
        else:
            send_text(
                token,
                chat_id,
                f"Character card {fields['name']} already exists as {target.name}; no changes made.",
            )
        return
    try:
        backup = verify_character_card_backup(target, raw, app_settings=app_settings)
        target.write_bytes(raw)
        prune_character_backups(target.name, app_settings=app_settings)
    except OSError:
        send_text(token, chat_id, "Character card backup verification failed; card was not installed.")
        return
    if provider_port is not None:
        try:
            session = ensure_session(db, chat_id, default_model, app_settings=app_settings)
            rank_character(
                db,
                chat_id,
                session,
                fields,
                target.name,
                provider_port=provider_port,
                app_settings=app_settings,
            )
        except Exception:
            logging.warning("Character rank skipped for %s", target.name, exc_info=True)
    send_text(
        token,
        chat_id,
        f"Character card imported: {fields['name']} ({target.name}). Backup verified: {backup.name}.",
    )


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
    provider_port=None,
    actor_id: str = "",
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
            session = ensure_session(db, chat_id, default_model, app_settings=app_settings)
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
            request_context = RequestContext(
                db,
                ensure_session(db, chat_id, default_model, app_settings=app_settings)["session_id"],
                actor_id,
                app_settings=app_settings,
            )
            import_character_card(
                db,
                token,
                chat_id,
                filename,
                raw,
                app_settings=app_settings,
                default_model=default_model,
                provider_port=provider_port,
                request_context=request_context,
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
        versions = data_bank_document_versions(db, chat_id, filename)
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
        send_text(token, chat_id, f"Added {filename} to Data Bank ({chunks} chunks). RAG is {rag_mode(db, chat_id)}.")
