"""Recover session character references after native SillyTavern renames."""

import hashlib
import logging
from pathlib import Path
import sqlite3
import struct

from bridge.card_content import character_display_name


def character_image_fingerprint(path: Path) -> str:
    """Hash PNG image chunks while ignoring mutable character metadata."""
    try:
        if not path.is_file() or path.stat().st_size > IMAGE_MAX_BYTES:
            return ""
        raw = path.read_bytes()
    except OSError:
        return ""
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return ""
    digest = hashlib.sha256()
    seen_header = seen_image = False
    position = 8
    while position + 12 <= len(raw):
        size = struct.unpack(">I", raw[position:position + 4])[0]
        end = position + 12 + size
        if end > len(raw):
            return ""
        chunk_type = raw[position + 4:position + 8]
        chunk = raw[position + 8:position + 8 + size]
        if chunk_type in {b"IHDR", b"PLTE", b"IDAT"}:
            digest.update(chunk_type)
            digest.update(chunk)
            seen_header = seen_header or chunk_type == b"IHDR"
            seen_image = seen_image or chunk_type == b"IDAT"
        position = end
        if chunk_type == b"IEND":
            break
    return digest.hexdigest() if seen_header and seen_image else ""


def _old_character_backups(filename: str) -> list[Path]:
    """Return safe exact/versioned backups for one missing character filename."""
    if not filename or Path(filename).name != filename or Path(filename).suffix.casefold() != ".png":
        return []
    candidates = [CHARACTER_BACKUP_DIR / filename]
    candidates.extend(sorted(CHARACTER_BACKUP_DIR.glob(f"{Path(filename).stem}.*.png"), reverse=True))
    return [path for path in candidates if path.is_file()]


def resolve_renamed_character(filename: str) -> str:
    """Resolve one missing card only when a unique native rename can be proven."""
    current = safe_character_path(filename)
    if current is not None:
        return current.name
    cards = character_card_paths()
    if not cards:
        return ""
    old_fingerprints = {character_image_fingerprint(path) for path in _old_character_backups(filename)} - {""}
    if old_fingerprints:
        fingerprint_matches = [path.name for path in cards if character_image_fingerprint(path) in old_fingerprints]
        if len(fingerprint_matches) == 1:
            return fingerprint_matches[0]
    expected = " ".join(Path(filename).stem.replace("_", " ").split()).casefold()
    name_matches = [path.name for path in cards if " ".join(character_display_name(path).split()).casefold() == expected]
    return name_matches[0] if len(name_matches) == 1 else ""


def reconcile_session_character(db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> dict[str, str]:
    """Rebind a stale session filename after a uniquely identified native rename."""
    old_name = str(session.get("character_file") or "")
    replacement = resolve_renamed_character(old_name)
    if not replacement or replacement == old_name:
        return session
    update_session(db, chat_id, session["session_id"], character_file=replacement)
    updated = dict(session)
    updated["character_file"] = replacement
    logging.info("Rebound renamed character for session %s: %s -> %s", session["session_id"], old_name, replacement)
    return updated
