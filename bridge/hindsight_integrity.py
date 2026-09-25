"""Ordinary stale-snapshot guard for Hindsight session memory."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass


@dataclass(frozen=True)
class HindsightStaleGuard:
    open_db: Callable[[], sqlite3.Connection]
    session_lock: Callable[
        [str, str],
        AbstractContextManager[object],
    ]
    memory_enabled: Callable[
        [sqlite3.Connection, str],
        bool,
    ]
    submit_background: Callable[..., object]
    session_exists: Callable[
        [sqlite3.Connection, str, str],
        bool,
    ]
    read_epoch: Callable[
        [sqlite3.Connection, str, str],
        int,
    ]
    snapshot: Callable[
        [sqlite3.Connection, str, str],
        tuple[str, str],
    ]
    retain_backend: Callable[..., None]
    purge_backend: Callable[..., int]
    write_successful_purge_state: Callable[
        [sqlite3.Connection, str, str],
        None,
    ]
    run_post_retain_hooks: Callable[..., None]

    def retain(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
        *,
        provider_port,
    ) -> None:
        if self.memory_enabled(db, chat_id):
            conversation, snapshot_hash = self.snapshot(
                db,
                chat_id,
                session["session_id"],
            )
            if conversation:
                snapshot_epoch = self.read_epoch(
                    db,
                    chat_id,
                    session["session_id"],
                )
                self.submit_background(
                    "hindsight_retain",
                    self._retain_if_current,
                    chat_id,
                    dict(session),
                    fields["name"],
                    conversation,
                    snapshot_hash,
                    snapshot_epoch,
                )
        self.run_post_retain_hooks(
            db,
            chat_id,
            session,
            fields,
            provider_port,
        )

    def _retain_if_current(
        self,
        chat_id: str,
        session: dict[str, str],
        character_name: str,
        conversation: str,
        snapshot_hash: str = "",
        snapshot_epoch: int | None = None,
    ) -> None:
        session_id = str(session["session_id"])
        with self.session_lock(chat_id, session_id):
            session_db = self.open_db()
            try:
                if not self.session_exists(
                    session_db,
                    chat_id,
                    session_id,
                ):
                    return
                current_epoch = self.read_epoch(
                    session_db,
                    chat_id,
                    session_id,
                )
                (
                    current_conversation,
                    current_hash,
                ) = self.snapshot(
                    session_db,
                    chat_id,
                    session_id,
                )
            finally:
                session_db.close()

            if snapshot_epoch is not None and current_epoch != int(snapshot_epoch):
                logging.info(
                    "Skipping stale Hindsight retain after session-memory purge for %s/%s",
                    chat_id,
                    session_id,
                )
                return
            if snapshot_hash and current_hash != snapshot_hash:
                logging.info(
                    "Skipping stale Hindsight retain after transcript change for %s/%s",
                    chat_id,
                    session_id,
                )
                return

            payload = current_conversation if snapshot_hash else conversation
            if not payload:
                return

            self.retain_backend(
                chat_id,
                session,
                character_name,
                payload,
            )

    def purge(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> int:
        with self.session_lock(chat_id, session_id):
            deleted = self.purge_backend(
                db,
                chat_id,
                session_id,
            )
            self.write_successful_purge_state(
                db,
                chat_id,
                session_id,
            )
            return int(deleted)
