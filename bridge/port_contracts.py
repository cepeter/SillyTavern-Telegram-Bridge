"""Named call interfaces; JSON values remain dynamic, call shapes do not.

This contract layer imports only immutable request/settings values and standard
library types. It never constructs adapters or imports the composition root.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from typing import Any, ParamSpec, Protocol, TypeVar

from bridge.request_types import PreparedMessage, RequestContext

P = ParamSpec("P")
T = TypeVar("T")
JsonObject = dict[str, Any]


class CancellationEvent(Protocol):
    def is_set(self) -> bool: ...


class TelegramRequest(Protocol):
    def __call__(self, token: str, method: str, payload: JsonObject | None = None) -> Any: ...


class SendText(Protocol):
    def __call__(self, token: str, chat_id: str, text: str) -> list[int]: ...


class DownloadFile(Protocol):
    def __call__(self, token: str, file_id: str, max_bytes: int = ...) -> bytes: ...


class SendTyping(Protocol):
    def __call__(self, token: str, chat_id: str) -> None: ...


class SendReply(Protocol):
    def __call__(
        self,
        token: str,
        chat_id: str,
        text: str,
        db: sqlite3.Connection | None = None,
        session_id: str | None = None,
        assistant_rowid: int | None = None,
    ) -> None: ...


class SendPanelRequest(Protocol):
    def __call__(
        self, token: str, method: str, payload: JsonObject, *, request_context: RequestContext
    ) -> JsonObject: ...


class DeleteOutgoingMessage(Protocol):
    def __call__(self, db: sqlite3.Connection, token: str, chat_id: str, rowid: int) -> None: ...


class ChatSubmit(Protocol):
    def __call__(
        self, label: str, chat_id: str, function: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
    ) -> bool: ...


class ProviderGenerate(Protocol):
    def __call__(
        self,
        api_key: str,
        model: str,
        messages: list[JsonObject],
        *,
        session_id: str = "telegram",
        settings: Mapping[str, object] | None = None,
        stream_callback: Callable[[str], object] | None = None,
        cancel_event: CancellationEvent | None = None,
        force_non_stream: bool = False,
        request_timeout: float | None = None,
    ) -> str: ...


class PrepareMessage(Protocol):
    def __call__(
        self,
        db: sqlite3.Connection,
        token: str,
        api_key: str,
        model: str,
        fields: JsonObject,
        chat_id: str,
        text: str,
        telegram_message_id: int | None = None,
        queued_session_id: str | None = None,
        operation_id: int | None = None,
        *,
        actor_id: str = "",
    ) -> PreparedMessage | None: ...


class DispatchCommand(Protocol):
    def __call__(
        self,
        db: sqlite3.Connection,
        token: str,
        api_key: str,
        model: str,
        fields: JsonObject,
        chat_id: str,
        stripped: str,
        command: str,
        session: JsonObject,
        session_id: str,
        current_model: str,
        current_persona: str,
        user_name: str,
        operation_id: int | None = None,
        *,
        request_context: RequestContext,
    ) -> bool: ...


class GeneratePreparedReply(Protocol):
    def __call__(
        self,
        db: sqlite3.Connection,
        token: str,
        api_key: str,
        fields: JsonObject,
        chat_id: str,
        text: str,
        session: JsonObject,
        session_id: str,
        current_model: str,
        group_turn: tuple[str, JsonObject] | None,
        group_context: str,
        telegram_message_id: int | None = None,
        operation_id: int | None = None,
    ) -> None: ...


class GroupStateRead(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]: ...


class GroupStateWrite(Protocol):
    def __call__(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        state: dict[str, object],
        operation_id: int | str | None = None,
    ) -> bool: ...


class GroupUserTurn(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool: ...


class GroupSetupRead(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> JsonObject | None: ...


class GroupSpeakerRead(Protocol):
    def __call__(
        self, db: sqlite3.Connection, chat_id: str, session: dict[str, str], user_text: str = ""
    ) -> tuple[str, dict[str, object]] | None: ...


class GroupAdvance(Protocol):
    def __call__(
        self, db: sqlite3.Connection, chat_id: str, session_id: str, operation_id: int | str | None = None
    ) -> None: ...


class RecallMemory(Protocol):
    def __call__(
        self, db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], query: str
    ) -> str: ...


class ReadSummary(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> str: ...


class ReadSummaryState(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple[str, int]: ...


class RetainSessionMemory(Protocol):
    def __call__(
        self, db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str]
    ) -> None: ...


class PurgeSessionMemory(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> int: ...
