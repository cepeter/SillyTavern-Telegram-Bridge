"""Pure application port for generated-response delivery operations."""

from __future__ import annotations

from dataclasses import dataclass

from bridge.port_contracts import (
    DeleteOutgoingMessage,
    SendPanelRequest,
    SendReply,
    SendText,
    SendTyping,
    TelegramRequest,
)


@dataclass(frozen=True)
class DeliveryPort:
    request: TelegramRequest
    send_text: SendText
    send_reply: SendReply
    send_typing: SendTyping
    send_panel_request: SendPanelRequest
    delete_outgoing_message_row: DeleteOutgoingMessage
