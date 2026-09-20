"""Ordinary Persona-store integrity decorator."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrityCheckedPersonaStore:
    load_personas: Callable[..., dict[str, dict[str, object]]]
    upsert_backend: Callable[..., str]
    delete_backend: Callable[..., bool]
    valid_avatar: Callable[[object], str]
    edit_lock: Callable[[], AbstractContextManager[object]]

    def _logical_id_is_taken(self, identifier: str) -> bool:
        value = str(identifier or "")
        if self.valid_avatar(value):
            return False
        expected_stem = f"bridge-{value}"
        return any(
            Path(str(avatar)).stem == expected_stem
            for avatar in self.load_personas(force=True)
        )

    def upsert(
        self,
        identifier: str,
        name: str,
        description: str,
        *,
        client=None,
    ) -> str:
        with self.edit_lock():
            if self._logical_id_is_taken(identifier):
                raise ValueError("Persona ID already exists")
            return str(
                self.upsert_backend(
                    identifier,
                    name,
                    description,
                    client=client,
                )
            )

    def delete(
        self,
        identifier: str,
        *,
        client=None,
    ) -> bool:
        with self.edit_lock():
            return bool(
                self.delete_backend(
                    identifier,
                    client=client,
                )
            )
