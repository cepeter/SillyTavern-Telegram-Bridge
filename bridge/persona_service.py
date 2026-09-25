"""Persona lifecycle application service."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

_PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validated_text(name: str, description: str) -> tuple[str, str]:
    name = str(name)
    description = str(description)
    if not 1 <= len(name) <= 120:
        raise ValueError("Persona name must be 1–120 characters")
    if not 1 <= len(description) <= 4000:
        raise ValueError("Persona description must be 1–4,000 characters")
    return name, description


@dataclass(frozen=True)
class PersonaService:
    load_personas: Callable[[], dict[str, dict[str, object]]]
    load_default_persona: Callable[[], str]
    upsert_persona: Callable[[str, str, str], str]
    delete_persona: Callable[[str], bool]
    update_session_persona: Callable[..., None]
    persona_reference_count: Callable[[sqlite3.Connection, str], int]
    persona_edit_lock: Callable[[], AbstractContextManager[object]] = lambda: nullcontext()

    def list(self) -> dict[str, dict[str, object]]:
        return dict(self.load_personas())

    def get(self, persona_id: str) -> dict[str, object] | None:
        return self.list().get(str(persona_id))

    def name(self, persona_id: str) -> str:
        persona = self.get(persona_id)
        return str(persona.get("name") or "") if persona else ""

    def default_id(self) -> str:
        return str(self.load_default_persona() or "")

    def create_and_select(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        logical_id: str,
        name: str,
        description: str,
        *,
        operation_id: int | str | None = None,
    ) -> str:
        logical_id = str(logical_id)
        if not _PERSONA_ID_RE.fullmatch(logical_id):
            raise ValueError("Persona ID must contain only letters, numbers, hyphens, or underscores")
        name, description = _validated_text(name, description)
        with self.persona_edit_lock():
            personas = self.list()
            expected_stem = f"bridge-{logical_id}"
            existing_avatar = next(
                (
                    str(persona_id)
                    for persona_id, persona in personas.items()
                    if str(persona_id) == logical_id or Path(str(persona_id)).stem == expected_stem
                ),
                "",
            )
            created = not bool(existing_avatar)
            if existing_avatar:
                existing = personas.get(existing_avatar) or {}
                if str(existing.get("name") or "") != name or str(existing.get("description") or "") != description:
                    raise ValueError("Persona ID already exists")
                avatar = existing_avatar
            else:
                avatar = str(self.upsert_persona(logical_id, name, description))
            try:
                self.update_session_persona(
                    db,
                    str(chat_id),
                    str(session_id),
                    persona_id=avatar,
                    operation_id=operation_id,
                    operation_kind="persona_create",
                )
            except Exception:
                if created:
                    try:
                        current = self.get(avatar) or {}
                        if (
                            Path(avatar).stem == expected_stem
                            and str(current.get("name") or "") == name
                            and str(current.get("description") or "") == description
                            and not self.persona_reference_count(db, avatar)
                        ):
                            self.delete_persona(avatar)
                    except Exception:
                        logging.warning("Could not clean up unreferenced Persona after failed selection", exc_info=True)
                raise
            return avatar

    def update(
        self,
        persona_id: str,
        name: str,
        description: str,
    ) -> str:
        persona_id = str(persona_id)
        if self.get(persona_id) is None:
            raise ValueError("Persona not found")
        name, description = _validated_text(name, description)
        with self.persona_edit_lock():
            return str(self.upsert_persona(persona_id, name, description))

    def select(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        persona_id: str,
        *,
        operation_id: int | str | None = None,
    ) -> bool:
        persona_id = str(persona_id)
        with self.persona_edit_lock():
            if self.get(persona_id) is None:
                return False
            self.update_session_persona(
                db,
                str(chat_id),
                str(session_id),
                persona_id=persona_id,
                operation_id=operation_id,
                operation_kind="persona_select",
            )
        return True

    def disable(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        *,
        operation_id: int | str | None = None,
    ) -> None:
        with self.persona_edit_lock():
            self.update_session_persona(
                db,
                str(chat_id),
                str(session_id),
                persona_id="",
                operation_id=operation_id,
                operation_kind="persona_select",
            )

    def delete_if_unused(
        self,
        db: sqlite3.Connection,
        persona_id: str,
    ) -> bool:
        persona_id = str(persona_id)
        with self.persona_edit_lock():
            if self.persona_reference_count(db, persona_id):
                raise ValueError("Persona is used by another session")
            return bool(self.delete_persona(persona_id))
