"""Application-owned command-versus-generation orchestration.

The composition root supplies concrete collaborators; this module imports no
Telegram adapter, command router, provider transport, or application container.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bridge.port_contracts import DispatchCommand, GeneratePreparedReply, PrepareMessage


@dataclass
class ConversationService:
    """Select exactly one command or generation workflow for a message."""

    prepare_message: PrepareMessage
    dispatch_command: DispatchCommand
    generate_reply: GeneratePreparedReply

    def process_message(
        self,
        db: sqlite3.Connection,
        token: str,
        api_key: str,
        model: str,
        fields: dict,
        chat_id: str,
        text: str,
        telegram_message_id: int | None = None,
        queued_session_id: str | None = None,
        operation_id: int | None = None,
        *,
        actor_id: str = "",
    ) -> None:
        prepared = self.prepare_message(
            db,
            token,
            api_key,
            model,
            fields,
            chat_id,
            text,
            telegram_message_id,
            queued_session_id=queued_session_id,
            operation_id=operation_id,
            actor_id=actor_id,
        )
        if prepared is None:
            return

        # Exceptions propagate to the durable worker. A failed command must
        # never fall through into ordinary character generation.
        if self.dispatch_command(
            db,
            token,
            api_key,
            model,
            prepared.fields,
            chat_id,
            prepared.stripped,
            prepared.command,
            prepared.session,
            prepared.session_id,
            prepared.current_model,
            prepared.current_persona,
            prepared.user_name,
            operation_id=operation_id,
            request_context=prepared.request_context,
        ):
            return

        self.generate_reply(
            db,
            token,
            api_key,
            prepared.fields,
            chat_id,
            text,
            prepared.session,
            prepared.session_id,
            prepared.current_model,
            prepared.group_turn,
            prepared.group_context,
            telegram_message_id,
            operation_id,
        )
