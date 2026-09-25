"""Ordinary integrity adapter for Live Sync snapshot imports."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from bridge.port_contracts import RetainSessionMemory


@dataclass(frozen=True)
class SyncSnapshotIntegrityAdapter:
    apply_backend: Callable[..., str]
    update_session: Callable[..., object]
    load_session: Callable[..., dict[str, str]]
    retain_memory: RetainSessionMemory
    card_fields: Callable[[str], dict[str, str]]
    default_model: str
    log_warning: Callable[..., None]

    def apply(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        metadata: dict,
        messages: list[tuple[str, str]],
        variants: dict[int, tuple[list[str], int]],
    ) -> str:
        imported_hash = self.apply_backend(
            db,
            chat_id,
            session,
            metadata,
            messages,
            variants,
        )

        updates = {}
        if "persona" in metadata and not str(metadata.get("persona") or "").strip():
            updates["persona_id"] = ""

        if "world_info" in metadata:
            raw_worlds = metadata.get("world_info")
            candidates = raw_worlds if isinstance(raw_worlds, list) else ([raw_worlds] if raw_worlds else [])
            if not candidates:
                updates["world_file"] = ""

        if updates:
            self.update_session(
                db,
                chat_id,
                session["session_id"],
                **updates,
            )

        try:
            refreshed = self.load_session(
                db,
                chat_id,
                session["session_id"],
                self.default_model,
            )
            fields = self.card_fields(refreshed["character_file"])
            self.retain_memory(
                db,
                chat_id,
                refreshed,
                fields,
            )
        except Exception:
            self.log_warning(
                "Could not refresh Hindsight after Live Sync import",
                exc_info=True,
            )

        return imported_hash
