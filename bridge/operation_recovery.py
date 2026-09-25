"""Ordinary crash-recovery mechanics for durable bridge operations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass


@dataclass(frozen=True)
class OperationRecovery:
    operation_phase: Callable[..., str]
    begin_operation: Callable[..., bool]
    record_operation: Callable[..., None]
    write_transaction: Callable[[sqlite3.Connection], AbstractContextManager[sqlite3.Connection]]
    get_meta: Callable[..., str]
    telegram_request: Callable[..., object]
    delete_outgoing_message_row: Callable[..., None]
    log_info: Callable[..., None]

    @staticmethod
    def _payload_key(operation_id) -> str:
        return f"operation_payload:{operation_id}"

    def set_payload(self, db, operation_id, payload) -> None:
        if operation_id is None:
            return

        def write():
            db.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (
                    self._payload_key(operation_id),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

        with self.write_transaction(db):
            write()

    def get_payload(self, db, operation_id) -> dict:
        if operation_id is None:
            return {}
        raw = self.get_meta(
            db,
            self._payload_key(operation_id),
            "",
        )
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def finish(self, db, operation_id, kind) -> None:
        if operation_id is None:
            return

        def write():
            self.record_operation(db, operation_id, kind)
            db.execute(
                "DELETE FROM meta WHERE key=?",
                (self._payload_key(operation_id),),
            )

        with self.write_transaction(db):
            write()

    def begin_or_recover(
        self,
        db,
        operation_id,
        kind,
        deliver_recovered,
    ) -> bool:
        if operation_id is None:
            return True
        phase = self.operation_phase(db, operation_id)
        if phase == "applied":
            return False
        if phase == "local_committed":
            deliver_recovered()
            return False
        return bool(self.begin_operation(db, operation_id, kind))

    @staticmethod
    def message_ids_from_rows(rows) -> list[str]:
        result = []
        for legacy_id, encoded_ids in rows:
            if legacy_id not in {None, ""}:
                result.append(str(legacy_id))
            try:
                decoded = json.loads(encoded_ids or "[]")
            except (TypeError, json.JSONDecodeError):
                decoded = []
            if isinstance(decoded, list):
                result.extend(str(item) for item in decoded if item not in {None, ""})
        return list(dict.fromkeys(result))

    def outgoing_ids_after(
        self,
        db,
        chat_id,
        session_id,
        rowid,
    ) -> list[str]:
        rows = db.execute(
            "SELECT telegram_message_id,telegram_message_ids "
            "FROM messages WHERE chat_id=? AND session_id=? "
            "AND role='assistant' AND rowid>? ORDER BY rowid",
            (chat_id, session_id, int(rowid)),
        ).fetchall()
        return self.message_ids_from_rows(rows)

    def delete_stored_telegram_ids(
        self,
        token,
        chat_id,
        message_ids,
    ) -> None:
        for message_id in message_ids or []:
            try:
                self.telegram_request(
                    token,
                    "deleteMessage",
                    {
                        "chat_id": chat_id,
                        "message_id": int(message_id),
                    },
                )
            except Exception:
                self.log_info(
                    "Recovery cleanup could not delete Telegram message %s",
                    message_id,
                    exc_info=True,
                )

    def prepare_delivery(
        self,
        db,
        token,
        chat_id,
        assistant_rowid,
        operation_id,
    ) -> None:
        try:
            self.delete_outgoing_message_row(
                db,
                token,
                chat_id,
                int(assistant_rowid),
            )
        except Exception:
            self.log_info(
                "Recovery could not clear the current assistant delivery",
                exc_info=True,
            )
        payload = self.get_payload(db, operation_id)
        self.delete_stored_telegram_ids(
            token,
            chat_id,
            payload.get("old_message_ids") or [],
        )

    @staticmethod
    def selected_variant_index(
        db,
        chat_id,
        session_id,
        user_rowid,
    ) -> int:
        row = db.execute(
            "SELECT variant_index FROM response_variants "
            "WHERE chat_id=? AND session_id=? AND user_rowid=? "
            "AND selected=1 ORDER BY id DESC LIMIT 1",
            (chat_id, session_id, int(user_rowid)),
        ).fetchone()
        return int(row[0]) if row else 1

    @staticmethod
    def latest_user_row(db, chat_id, session_id):
        return db.execute(
            "SELECT rowid,content FROM messages "
            "WHERE chat_id=? AND session_id=? AND role='user' "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id, session_id),
        ).fetchone()

    @staticmethod
    def latest_assistant_row(db, chat_id, session_id):
        return db.execute(
            "SELECT rowid,content FROM messages "
            "WHERE chat_id=? AND session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id, session_id),
        ).fetchone()
