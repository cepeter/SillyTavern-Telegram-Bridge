"""Explicit bridge/native SillyTavern persona interoperability."""

import copy
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path


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
_NATIVE_AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_PERSONA_CATALOG_WARNING = ""


def _read_persona_catalog() -> tuple[dict[str, dict[str, object]], int]:
    """Read one catalog while normalizing isolated corrupt entries."""
    if not PERSONA_FILE.exists():
        return {}, 0
    raw = PERSONA_FILE.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("persona catalog is too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("persona catalog must be a JSON object")
    result = {}
    repaired = 0
    for persona_id, persona in data.items():
        if not isinstance(persona_id, str) or not isinstance(persona, dict):
            repaired += 1
            logging.warning("Skipping invalid persona catalog entry %r", persona_id)
            continue
        item = dict(persona)
        if not isinstance(item.get("name"), str) or not str(item.get("name") or "").strip():
            item["name"] = persona_id
            repaired += 1
        if not isinstance(item.get("description"), str):
            item["description"] = ""
            repaired += 1
        tags = item.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            item["tags"] = []
            repaired += 1
        result[persona_id] = item
    return result, repaired


def load_personas() -> dict[str, dict[str, object]]:
    """Load usable personas and retain a warning for the Persona panel."""
    global _PERSONA_CATALOG_WARNING
    try:
        personas, repaired = _read_persona_catalog()
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        _PERSONA_CATALOG_WARNING = "Persona catalog cannot be read; restore or repair its JSON file."
        logging.warning("Could not load persona catalog: %s", exc, exc_info=True)
        return {}
    _PERSONA_CATALOG_WARNING = (
        f"Normalized or skipped {repaired} invalid persona field(s). A verified backup is kept when you edit."
        if repaired else ""
    )
    return personas


def _editable_personas() -> dict[str, dict[str, object]]:
    """Load an editable catalog without letting one bad entry block all edits."""
    personas, repaired = _read_persona_catalog()
    if repaired:
        logging.warning("Normalized or skipped %d invalid persona field(s) before editing", repaired)
    return personas


def persona_catalog_warning() -> str:
    return _PERSONA_CATALOG_WARNING


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


def _save_native_settings(client, settings: dict) -> None:
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


def _bridge_id_for_avatar(avatar: str, personas: dict[str, dict]) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(avatar).stem).strip("-_")[:64] or "persona"
    for attempt in range(257):
        candidate = base if attempt == 0 else (base[:55] + "-" + hashlib.sha256(f"{avatar}:{attempt}".encode("utf-8")).hexdigest()[:8])[:64]
        if candidate not in personas or str(personas[candidate].get("sillytavern_avatar") or "") == avatar:
            return candidate
    raise ValueError("Could not allocate a unique bridge persona id")


def _choose_native_avatar(persona_id: str, persona: dict, settings: dict, native_names: dict) -> str:
    mapped = _valid_native_avatar(persona.get("sillytavern_avatar"))
    if mapped:
        return mapped
    matches = [avatar for avatar, name in native_names.items() if str(name).casefold() == str(persona["name"]).casefold() and _valid_native_avatar(avatar)]
    if len(matches) == 1:
        return matches[0]
    base = f"bridge-{persona_id}"
    candidate = f"{base}.png"
    for attempt in range(257):
        if candidate not in native_names and not (NATIVE_PERSONA_AVATAR_DIR / candidate).exists():
            return candidate
        suffix = hashlib.sha256(f"{persona_id}:{attempt}".encode("utf-8")).hexdigest()[:8]
        candidate = f"{base[:54]}-{suffix}.png"
    raise ValueError("Could not allocate a unique native persona avatar")


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


def import_native_personas(client=None) -> str:
    """Import native persona metadata into the bridge without deleting either side."""
    api = client or phase3_client()
    settings = _native_settings(api)
    _power, native_names, native_descriptions = _native_persona_maps(settings)
    imported = updated = skipped = 0
    with PERSONA_EDIT_LOCK:
        personas = _editable_personas()
        before = copy.deepcopy(personas)
        by_avatar = {str(item.get("sillytavern_avatar") or ""): key for key, item in personas.items()}
        for raw_avatar, raw_name in native_names.items():
            avatar = _valid_native_avatar(raw_avatar)
            descriptor = native_descriptions.get(raw_avatar, {})
            name = str(raw_name or "").strip()
            description = str(descriptor.get("description") or "").strip() if isinstance(descriptor, dict) else ""
            if not avatar or not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
                skipped += 1
                continue
            persona_id = by_avatar.get(avatar) or _bridge_id_for_avatar(avatar, personas)
            existing = personas.get(persona_id)
            if existing:
                updated += int(existing.get("name") != name or existing.get("description") != description or existing.get("sillytavern_avatar") != avatar)
                existing.update({"name": name, "description": description, "sillytavern_avatar": avatar})
                existing.setdefault("tags", [])
            else:
                if len(personas) >= CATALOG_MAX_ITEMS:
                    skipped += 1
                    continue
                personas[persona_id] = {"name": name, "description": description, "tags": [], "sillytavern_avatar": avatar}
                imported += 1
            by_avatar[avatar] = persona_id
        if personas != before:
            _write_personas_atomically(personas)
    return f"Native persona import complete: {imported} added, {updated} updated, {skipped} skipped."


def refresh_native_persona_cache(force: bool = False) -> str:
    """Refresh the bridge fallback cache from the native API at a bounded rate."""
    global _NATIVE_PERSONA_CACHE_LAST_REFRESH
    if not phase3_api_configured():
        return "Native persona API is not configured; using bridge fallback cache."
    now = time.time()
    if not force and now - _NATIVE_PERSONA_CACHE_LAST_REFRESH < _NATIVE_PERSONA_CACHE_SECONDS:
        return "Native persona cache is current."
    result = import_native_personas()
    _NATIVE_PERSONA_CACHE_LAST_REFRESH = now
    return result


def export_persona_to_native(persona_id: str, client=None) -> str:
    """Export one bridge persona through SillyTavern's settings API without deleting native data."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(persona_id or "")):
        raise ValueError("Select a valid bridge persona before export")
    with PERSONA_EDIT_LOCK:
        personas = _editable_personas()
        persona = personas.get(persona_id)
        if not persona:
            raise ValueError("Current bridge persona was not found")
        snapshot = copy.deepcopy(persona)
    api = client or phase3_client()
    original = _native_settings(api)
    updated_settings = copy.deepcopy(original)
    _power, native_names, native_descriptions = _native_persona_maps(updated_settings, create=True)
    avatar = _choose_native_avatar(persona_id, snapshot, updated_settings, native_names)
    if not _valid_native_avatar(avatar):
        raise ValueError("Generated SillyTavern avatar name is invalid")
    if avatar not in native_names and len(native_names) >= CATALOG_MAX_ITEMS:
        raise ValueError(f"Native persona catalog is full ({CATALOG_MAX_ITEMS} maximum)")
    descriptor = native_descriptions.get(avatar)
    descriptor = dict(descriptor) if isinstance(descriptor, dict) else {}
    descriptor["description"] = str(snapshot["description"])
    descriptor.setdefault("position", 0)
    descriptor.setdefault("depth", 2)
    descriptor.setdefault("role", 0)
    descriptor.setdefault("lorebook", "")
    descriptor.setdefault("title", "")
    native_names[avatar] = str(snapshot["name"])
    native_descriptions[avatar] = descriptor
    if _settings_hash(_native_settings(api)) != _settings_hash(original):
        raise ValueError("SillyTavern settings changed during export; retry")
    _backup_native_settings(original)
    created_avatar = _ensure_native_avatar(avatar, updated_settings)
    save_error = None
    try:
        _save_native_settings(api, updated_settings)
    except Exception as exc:
        save_error = exc
    try:
        verified = _native_settings(api)
        _vpower, verified_names, verified_descriptions = _native_persona_maps(verified)
    except Exception:
        if created_avatar:
            (NATIVE_PERSONA_AVATAR_DIR / avatar).unlink(missing_ok=True)
        if save_error is not None:
            raise save_error
        raise
    target_matches = (
        verified_names.get(avatar) == str(snapshot["name"])
        and isinstance(verified_descriptions.get(avatar), dict)
        and verified_descriptions[avatar].get("description") == str(snapshot["description"])
    )
    if not target_matches:
        if created_avatar and avatar not in verified_names:
            (NATIVE_PERSONA_AVATAR_DIR / avatar).unlink(missing_ok=True)
        if save_error is not None:
            raise save_error
        raise SillyTavernApiError("SillyTavern persona export verification failed")
    settings_changed_after_save = _settings_hash(verified) != _settings_hash(updated_settings)
    changed_during_export = False
    with PERSONA_EDIT_LOCK:
        personas = _editable_personas()
        current = personas.get(persona_id)
        if not current:
            raise ValueError("Bridge persona disappeared after native export")
        changed_during_export = current.get("name") != snapshot.get("name") or current.get("description") != snapshot.get("description")
        current["sillytavern_avatar"] = avatar
        _write_personas_atomically(personas)
    warnings = []
    if changed_during_export:
        warnings.append("bridge persona changed during export; export again")
    if settings_changed_after_save:
        warnings.append("other SillyTavern settings changed concurrently; target persona verified")
    if save_error is not None:
        warnings.append("save response failed but target persona was verified")
    suffix = "; " + "; ".join(warnings) if warnings else ""
    return f"Exported bridge persona to SillyTavern: {avatar}{suffix}"


def handle_native_persona_callback(token: str, callback: dict, answer_callback, value: str, chat_id: str, message_id: int | None, session: dict) -> bool:
    """Handle explicit native persona import/export panel actions."""
    if value not in {"native_import", "native_export"}:
        return False
    answer_callback(token, str(callback.get("id", "")), "Syncing personas")
    try:
        result = import_native_personas() if value == "native_import" else export_persona_to_native(str(session.get("persona_id") or ""))
    except (OSError, ValueError, SillyTavernApiError, json.JSONDecodeError) as exc:
        result = f"Persona sync failed: {exc}"
    except Exception:
        logging.warning("Unexpected native persona sync failure", exc_info=True)
        result = "Persona sync failed unexpectedly; no further changes were attempted."
    send_text(token, chat_id, result)
    send_persona_menu(token, chat_id, str(session.get("persona_id") or ""), message_id)
    return True
