"""Explicit bridge/native SillyTavern persona interoperability."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from functools import partial as _partial
from pathlib import Path

import bridge.sillytavern_api as _st_api
from bridge.limits import CATALOG_MAX_ITEMS, IMAGE_MAX_BYTES, SYNC_MAX_BYTES
from bridge.persona_integrity import IntegrityCheckedPersonaStore as _IntegrityCheckedPersonaStore
from bridge.settings import AppSettings

_NATIVE_AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PERSONA_EDIT_LOCK = threading.RLock()


def load_personas(*, app_settings: AppSettings) -> dict[str, dict[str, object]]:
    """Load native Persona metadata; the bridge JSON catalog is not used."""
    try:
        return load_native_personas(app_settings=app_settings)
    except Exception:
        logging.warning("Could not load native Persona metadata", exc_info=True)
        return {}


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


def _native_settings(client=None, *, app_settings: AppSettings) -> dict:
    if client is None and not _st_api.live_sync_api_configured(app_settings=app_settings):
        path = app_settings.native_persona_settings_file
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _st_api.SillyTavernApiError("Native SillyTavern settings cannot be read") from exc
        if not isinstance(settings, dict):
            raise _st_api.SillyTavernApiError("Native SillyTavern settings have an invalid shape")
        return settings
    api = client or _st_api.live_sync_client(app_settings=app_settings)
    if hasattr(api, "get_settings"):
        settings = api.get_settings()
    else:
        response = api.post("/api/settings/get", {})
        raw = response.get("settings") if isinstance(response, dict) else None
        try:
            settings = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise _st_api.SillyTavernApiError("SillyTavern settings are invalid JSON") from exc
    if not isinstance(settings, dict):
        raise _st_api.SillyTavernApiError("SillyTavern settings response has an invalid shape")
    return settings


def get_persona(persona_id: str, *, app_settings: AppSettings) -> dict[str, str] | None:
    return load_personas(app_settings=app_settings).get(persona_id)


def default_persona_id(*, app_settings: AppSettings) -> str:
    """Resolve the native default Persona without exposing a private identity."""
    try:
        personas = load_personas(app_settings=app_settings)
        settings = _native_settings(app_settings=app_settings)
        power_user = settings.get("power_user") if isinstance(settings, dict) else {}
        configured = str((power_user or {}).get("default_persona") or "").strip()
        if configured in personas:
            return configured
    except Exception:
        logging.warning(
            "Could not resolve the native default Persona",
            exc_info=True,
        )
    return ""


def persona_name(persona_id: str, *, app_settings: AppSettings) -> str:
    persona = get_persona(persona_id, app_settings=app_settings)
    return str(persona.get("name") or "") if persona else ""


def load_native_personas(*, app_settings: AppSettings) -> dict[str, dict[str, object]]:
    """Read this application's native metadata without a cross-application cache."""
    settings = _native_settings(app_settings=app_settings)
    _power, native_names, native_descriptions = _native_persona_maps(settings)
    personas = {}
    for raw_avatar, raw_name in native_names.items():
        avatar = _valid_native_avatar(raw_avatar)
        name = str(raw_name or "").strip()
        descriptor = native_descriptions.get(raw_avatar, {})
        description = str(descriptor.get("description") or "").strip() if isinstance(descriptor, dict) else ""
        if avatar and 1 <= len(name) <= 120:
            personas[avatar] = {"name": name, "description": description[:4000], "sillytavern_avatar": avatar}
    return personas


def _save_native_settings(client, settings: dict, *, app_settings: AppSettings) -> None:
    if client is None and not _st_api.live_sync_api_configured(app_settings=app_settings):
        path = app_settings.native_persona_settings_file
        temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return
    client = client or _st_api.live_sync_client(app_settings=app_settings)
    if hasattr(client, "save_settings"):
        client.save_settings(settings)
        return
    result = client.post("/api/settings/save", settings)
    if not isinstance(result, dict) or result.get("result") != "ok":
        raise _st_api.SillyTavernApiError("SillyTavern refused the persona settings update")


def _backup_native_settings(expected: dict, *, app_settings: AppSettings) -> Path:
    path = app_settings.native_persona_settings_file
    if not path.is_file():
        raise OSError("SillyTavern settings file is unavailable for backup")
    raw = path.read_bytes()
    if len(raw) > SYNC_MAX_BYTES:
        raise ValueError("SillyTavern settings exceed the backup limit")
    try:
        disk_settings = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("SillyTavern settings backup source is invalid") from exc
    if not isinstance(disk_settings, dict) or _settings_hash(disk_settings) != _settings_hash(expected):
        raise ValueError("SillyTavern settings file does not match the API snapshot; retry")
    app_settings.native_persona_backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(app_settings.native_persona_backup_dir, 0o700)
    backup = app_settings.native_persona_backup_dir / f"settings-persona-{time.time_ns()}.json"
    backup.write_bytes(raw)
    os.chmod(backup, 0o600)
    if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(raw).digest():
        backup.unlink(missing_ok=True)
        raise OSError("native persona backup checksum verification failed")
    backups = sorted(
        app_settings.native_persona_backup_dir.glob("settings-persona-*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in backups[20:]:
        stale.unlink(missing_ok=True)
    return backup


def _ensure_native_avatar(avatar: str, settings: dict, *, app_settings: AppSettings) -> bool:
    target = app_settings.native_persona_avatar_dir / avatar
    if target.is_file():
        return False
    source_name = _valid_native_avatar(settings.get("user_avatar")) or "user-default.png"
    source = app_settings.native_persona_avatar_dir / source_name
    if not source.is_file():
        raise ValueError("SillyTavern has no avatar image to clone for this persona")
    raw = source.read_bytes()
    if not raw or len(raw) > IMAGE_MAX_BYTES:
        raise ValueError("SillyTavern persona avatar has an invalid size")
    app_settings.native_persona_avatar_dir.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_bytes(raw)
        os.chmod(temporary, 0o644)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _choose_native_avatar(
    persona_id: str, persona: dict, settings: dict, native_names: dict, *, app_settings: AppSettings
) -> str:
    """Allocate a native avatar without lying about the cloned image format."""
    mapped = _valid_native_avatar(persona.get("sillytavern_avatar"))
    if mapped:
        return mapped
    matches = [
        avatar
        for avatar, name in native_names.items()
        if str(name).casefold() == str(persona["name"]).casefold() and _valid_native_avatar(avatar)
    ]
    if len(matches) == 1:
        return matches[0]
    source_name = _valid_native_avatar(settings.get("user_avatar"))
    suffix = Path(source_name).suffix.casefold() if source_name else ".png"
    if suffix not in _NATIVE_AVATAR_SUFFIXES:
        suffix = ".png"
    base = f"bridge-{persona_id}"
    candidate = f"{base}{suffix}"
    for attempt in range(257):
        if candidate not in native_names and not (app_settings.native_persona_avatar_dir / candidate).exists():
            return candidate
        token = hashlib.sha256(f"{persona_id}:{attempt}".encode("utf-8")).hexdigest()[:8]
        candidate = f"{base[:54]}-{token}{suffix}"
    raise ValueError("Could not allocate a unique native persona avatar")


def _upsert_native_persona_storage(
    identifier: str, name: str, description: str, client=None, *, app_settings: AppSettings
) -> str:
    """Create or update one Persona directly in native SillyTavern settings."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(identifier or "")) and not _valid_native_avatar(
        str(identifier or "")
    ):
        raise ValueError("Persona ID must contain only letters, numbers, hyphens, or underscores")
    if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
        raise ValueError("Persona name must be 1–120 characters and description 1–4,000 characters")
    api = client or (
        _st_api.live_sync_client(app_settings=app_settings)
        if _st_api.live_sync_api_configured(app_settings=app_settings)
        else None
    )
    original = _native_settings(api, app_settings=app_settings)
    updated = copy.deepcopy(original)
    _power, native_names, native_descriptions = _native_persona_maps(updated, create=True)
    existing = identifier if _valid_native_avatar(identifier) and identifier in native_names else ""
    avatar = existing or _choose_native_avatar(
        identifier, {"name": name}, updated, native_names, app_settings=app_settings
    )
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
    if _settings_hash(_native_settings(api, app_settings=app_settings)) != _settings_hash(original):
        raise ValueError("SillyTavern settings changed during Persona update; retry")
    _backup_native_settings(original, app_settings=app_settings)
    created_avatar = _ensure_native_avatar(avatar, updated, app_settings=app_settings)
    save_error = None
    try:
        _save_native_settings(api, updated, app_settings=app_settings)
    except Exception as exc:
        save_error = exc
    try:
        verified = _native_settings(api, app_settings=app_settings)
        _power, verified_names, verified_descriptions = _native_persona_maps(verified)
    except Exception as exc:
        if created_avatar:
            (app_settings.native_persona_avatar_dir / avatar).unlink(missing_ok=True)
        raise (save_error or RuntimeError("SillyTavern Persona readback failed")) from exc
    target = verified_descriptions.get(avatar, {})
    if verified_names.get(avatar) != name or not isinstance(target, dict) or target.get("description") != description:
        if created_avatar and avatar not in verified_names:
            (app_settings.native_persona_avatar_dir / avatar).unlink(missing_ok=True)
        raise save_error or _st_api.SillyTavernApiError("SillyTavern Persona readback did not match")
    return avatar


def _delete_native_persona_storage(identifier: str, client=None, *, app_settings: AppSettings) -> bool:
    """Remove one native Persona metadata entry while preserving its avatar file."""
    api = client or (
        _st_api.live_sync_client(app_settings=app_settings)
        if _st_api.live_sync_api_configured(app_settings=app_settings)
        else None
    )
    original = _native_settings(api, app_settings=app_settings)
    updated = copy.deepcopy(original)
    _power, native_names, native_descriptions = _native_persona_maps(updated)
    avatar = _valid_native_avatar(identifier)
    if not avatar or avatar not in native_names:
        return False
    del native_names[avatar]
    native_descriptions.pop(avatar, None)
    if _settings_hash(_native_settings(api, app_settings=app_settings)) != _settings_hash(original):
        raise ValueError("SillyTavern settings changed during Persona deletion; retry")
    _backup_native_settings(original, app_settings=app_settings)
    _save_native_settings(api, updated, app_settings=app_settings)
    verified = _native_settings(api, app_settings=app_settings)
    _power, verified_names, _verified_descriptions = _native_persona_maps(verified)
    if avatar in verified_names:
        raise _st_api.SillyTavernApiError("SillyTavern Persona deletion readback did not match")
    return True


def upsert_native_persona(
    identifier: str, name: str, description: str, client=None, *, app_settings: AppSettings
) -> str:
    """Create or update one Persona through the integrity-checked native store."""
    return _persona_store(app_settings=app_settings).upsert(
        identifier,
        name,
        description,
        client=client,
    )


def delete_native_persona(identifier: str, client=None, *, app_settings: AppSettings) -> bool:
    """Delete one Persona through the integrity-checked native store."""
    return _persona_store(app_settings=app_settings).delete(
        identifier,
        client=client,
    )


def _persona_store(*, app_settings: AppSettings):
    """Create a lightweight configured integrity adapter; no mutable module binding."""
    return _IntegrityCheckedPersonaStore(
        load_personas=_partial(load_native_personas, app_settings=app_settings),
        upsert_backend=_partial(_upsert_native_persona_storage, app_settings=app_settings),
        delete_backend=_partial(_delete_native_persona_storage, app_settings=app_settings),
        valid_avatar=_valid_native_avatar,
        edit_lock=lambda: PERSONA_EDIT_LOCK,
    )
