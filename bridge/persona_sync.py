"""Explicit bridge/native SillyTavern persona interoperability."""

import copy
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from bridge.persona_service import PersonaService as _PersonaService
from bridge.repositories import (
    count_persona_references as _repo_count_persona_references,
)


NATIVE_PERSONA_SETTINGS_FILE = Path(os.environ.get(
    "SILLYTAVERN_NATIVE_SETTINGS_FILE",
    str(SILLYTAVERN_DIR / "data/default-user/settings.json"),
))
NATIVE_PERSONA_AVATAR_DIR = Path(os.environ.get(
    "SILLYTAVERN_NATIVE_AVATAR_DIR",
    str(SILLYTAVERN_DIR / "data/default-user/User Avatars"),
))
NATIVE_PERSONA_BACKUP_DIR = BRIDGE_HOME / "backups" / "sillytavern" / "personas"
_NATIVE_PERSONA_CACHE_LAST_REFRESH = 0.0
_NATIVE_PERSONA_CACHE_SECONDS = 15.0
_NATIVE_PERSONA_CACHE: dict[str, dict[str, object]] = {}
_NATIVE_AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def load_personas() -> dict[str, dict[str, object]]:
    """Load native Persona metadata; the bridge JSON catalog is not used."""
    try:
        return load_native_personas()
    except Exception:
        logging.warning("Could not load native Persona metadata", exc_info=True)
        return {}


def compatibility_persona_service() -> _PersonaService:
    """Build a late-bound PersonaService over the final runtime collaborators."""
    return _PersonaService(
        load_personas=load_personas,
        load_default_persona=default_persona_id,
        upsert_persona=upsert_native_persona,
        delete_persona=delete_native_persona,
        update_session_persona=update_session,
        persona_reference_count=_repo_count_persona_references,
    )


def resolve_persona_service(persona_service=None) -> _PersonaService:
    return (
        persona_service
        if persona_service is not None
        else compatibility_persona_service()
    )


def _settings_hash(settings: dict) -> str:
    raw = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _valid_native_avatar(value: object) -> str:
    avatar = str(value or "").strip()
    if not avatar or len(avatar) > 255 or Path(avatar).name != avatar:
        return ""
    if Path(avatar).suffix.casefold() not in _NATIVE_AVATAR_SUFFIXES:
        return ""
    return avatar


def _native_persona_maps(settings: dict, create: bool = False) -> tuple[dict, dict, dict]:
    if not isinstance(settings, dict):
        raise ValueError("SillyTavern settings have an invalid shape")
    power = settings.get("power_user")
    if not isinstance(power, dict):
        if not create:
            raise ValueError("SillyTavern settings do not contain power_user data")
        power = settings["power_user"] = {}
    personas = power.get("personas")
    descriptions = power.get("persona_descriptions")
    if not isinstance(personas, dict):
        if not create:
            raise ValueError("SillyTavern persona names have an invalid shape")
        personas = power["personas"] = {}
    if not isinstance(descriptions, dict):
        if not create:
            raise ValueError("SillyTavern persona descriptions have an invalid shape")
        descriptions = power["persona_descriptions"] = {}
    return power, personas, descriptions


def _native_settings(client=None) -> dict:
    if client is None and not phase3_api_configured():
        path = NATIVE_PERSONA_SETTINGS_FILE
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SillyTavernApiError("Native SillyTavern settings cannot be read") from exc
        if not isinstance(settings, dict):
            raise SillyTavernApiError("Native SillyTavern settings have an invalid shape")
        return settings
    api = client or phase3_client()
    if hasattr(api, "get_settings"):
        settings = api.get_settings()
    else:
        response = api.post("/api/settings/get", {})
        raw = response.get("settings") if isinstance(response, dict) else None
        try:
            settings = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise SillyTavernApiError("SillyTavern settings are invalid JSON") from exc
    if not isinstance(settings, dict):
        raise SillyTavernApiError("SillyTavern settings response has an invalid shape")
    return settings


def load_native_personas(force: bool = False) -> dict[str, dict[str, object]]:
    """Load Persona metadata directly from native SillyTavern settings."""
    global _NATIVE_PERSONA_CACHE_LAST_REFRESH, _NATIVE_PERSONA_CACHE
    now = time.time()
    if not force and _NATIVE_PERSONA_CACHE and now - _NATIVE_PERSONA_CACHE_LAST_REFRESH < _NATIVE_PERSONA_CACHE_SECONDS:
        return copy.deepcopy(_NATIVE_PERSONA_CACHE)
    settings = _native_settings()
    _power, native_names, native_descriptions = _native_persona_maps(settings)
    personas = {}
    for raw_avatar, raw_name in native_names.items():
        avatar = _valid_native_avatar(raw_avatar)
        name = str(raw_name or "").strip()
        descriptor = native_descriptions.get(raw_avatar, {})
        description = str(descriptor.get("description") or "").strip() if isinstance(descriptor, dict) else ""
        if avatar and 1 <= len(name) <= 120:
            personas[avatar] = {
                "name": name,
                "description": description[:4000],
                "sillytavern_avatar": avatar,
            }
    _NATIVE_PERSONA_CACHE = personas
    _NATIVE_PERSONA_CACHE_LAST_REFRESH = now
    return copy.deepcopy(personas)


def _save_native_settings(client, settings: dict) -> None:
    if client is None and not phase3_api_configured():
        path = NATIVE_PERSONA_SETTINGS_FILE
        temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return
    client = client or phase3_client()
    if hasattr(client, "save_settings"):
        client.save_settings(settings)
        return
    result = client.post("/api/settings/save", settings)
    if not isinstance(result, dict) or result.get("result") != "ok":
        raise SillyTavernApiError("SillyTavern refused the persona settings update")


def _backup_native_settings(expected: dict) -> Path:
    path = NATIVE_PERSONA_SETTINGS_FILE
    if not path.is_file():
        raise OSError("SillyTavern settings file is unavailable for backup")
    raw = path.read_bytes()
    if len(raw) > SYNC_MAX_PAYLOAD_BYTES:
        raise ValueError("SillyTavern settings exceed the backup limit")
    try:
        disk_settings = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("SillyTavern settings backup source is invalid") from exc
    if not isinstance(disk_settings, dict) or _settings_hash(disk_settings) != _settings_hash(expected):
        raise ValueError("SillyTavern settings file does not match the API snapshot; retry")
    NATIVE_PERSONA_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(NATIVE_PERSONA_BACKUP_DIR, 0o700)
    backup = NATIVE_PERSONA_BACKUP_DIR / f"settings-persona-{time.time_ns()}.json"
    backup.write_bytes(raw)
    os.chmod(backup, 0o600)
    if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(raw).digest():
        backup.unlink(missing_ok=True)
        raise OSError("native persona backup checksum verification failed")
    backups = sorted(NATIVE_PERSONA_BACKUP_DIR.glob("settings-persona-*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    for stale in backups[20:]:
        stale.unlink(missing_ok=True)
    return backup


def _ensure_native_avatar(avatar: str, settings: dict) -> bool:
    target = NATIVE_PERSONA_AVATAR_DIR / avatar
    if target.is_file():
        return False
    source_name = _valid_native_avatar(settings.get("user_avatar")) or "user-default.png"
    source = NATIVE_PERSONA_AVATAR_DIR / source_name
    if not source.is_file():
        raise ValueError("SillyTavern has no avatar image to clone for this persona")
    raw = source.read_bytes()
    if not raw or len(raw) > IMAGE_MAX_BYTES:
        raise ValueError("SillyTavern persona avatar has an invalid size")
    NATIVE_PERSONA_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_bytes(raw)
        os.chmod(temporary, 0o644)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def upsert_native_persona(identifier: str, name: str, description: str, client=None) -> str:
    """Create or update one Persona directly in native SillyTavern settings."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(identifier or "")) and not _valid_native_avatar(str(identifier or "")):
        raise ValueError("Persona ID must contain only letters, numbers, hyphens, or underscores")
    if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
        raise ValueError("Persona name must be 1–120 characters and description 1–4,000 characters")
    api = client or (phase3_client() if phase3_api_configured() else None)
    original = _native_settings(api)
    updated = copy.deepcopy(original)
    _power, native_names, native_descriptions = _native_persona_maps(updated, create=True)
    existing = identifier if _valid_native_avatar(identifier) and identifier in native_names else ""
    avatar = existing or _choose_native_avatar(identifier, {"name": name}, updated, native_names)
    if avatar not in native_names and len(native_names) >= CATALOG_MAX_ITEMS:
        raise ValueError(f"Native persona catalog is full ({CATALOG_MAX_ITEMS} maximum)")
    descriptor = native_descriptions.get(avatar)
    descriptor = dict(descriptor) if isinstance(descriptor, dict) else {}
    descriptor["description"] = description
    descriptor.setdefault("position", 0)
    descriptor.setdefault("depth", 2)
    descriptor.setdefault("role", 0)
    descriptor.setdefault("lorebook", "")
    descriptor.setdefault("title", "")
    native_names[avatar] = name
    native_descriptions[avatar] = descriptor
    if _settings_hash(_native_settings(api)) != _settings_hash(original):
        raise ValueError("SillyTavern settings changed during Persona update; retry")
    _backup_native_settings(original)
    created_avatar = _ensure_native_avatar(avatar, updated)
    save_error = None
    try:
        _save_native_settings(api, updated)
    except Exception as exc:
        save_error = exc
    try:
        verified = _native_settings(api)
        _power, verified_names, verified_descriptions = _native_persona_maps(verified)
    except Exception:
        if created_avatar:
            (NATIVE_PERSONA_AVATAR_DIR / avatar).unlink(missing_ok=True)
        raise save_error or RuntimeError("SillyTavern Persona readback failed")
    target = verified_descriptions.get(avatar, {})
    if verified_names.get(avatar) != name or not isinstance(target, dict) or target.get("description") != description:
        if created_avatar and avatar not in verified_names:
            (NATIVE_PERSONA_AVATAR_DIR / avatar).unlink(missing_ok=True)
        raise save_error or SillyTavernApiError("SillyTavern Persona readback did not match")
    _NATIVE_PERSONA_CACHE.clear()
    return avatar


def delete_native_persona(identifier: str, client=None) -> bool:
    """Remove one native Persona metadata entry while preserving its avatar file."""
    api = client or (phase3_client() if phase3_api_configured() else None)
    original = _native_settings(api)
    updated = copy.deepcopy(original)
    _power, native_names, native_descriptions = _native_persona_maps(updated)
    avatar = _valid_native_avatar(identifier)
    if not avatar or avatar not in native_names:
        return False
    del native_names[avatar]
    native_descriptions.pop(avatar, None)
    if _settings_hash(_native_settings(api)) != _settings_hash(original):
        raise ValueError("SillyTavern settings changed during Persona deletion; retry")
    _backup_native_settings(original)
    _save_native_settings(api, updated)
    verified = _native_settings(api)
    _power, verified_names, _verified_descriptions = _native_persona_maps(verified)
    if avatar in verified_names:
        raise SillyTavernApiError("SillyTavern Persona deletion readback did not match")
    _NATIVE_PERSONA_CACHE.clear()
    return True
