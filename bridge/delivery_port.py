"""Pure application port for generated-response delivery operations."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryPort:
    request: Callable[..., object]
    send_text: Callable[..., list[int]]
    send_reply: Callable[..., None]
    send_typing: Callable[..., None]
    send_panel_request: Callable[..., dict]
    delete_outgoing_message_row: Callable[..., None]
