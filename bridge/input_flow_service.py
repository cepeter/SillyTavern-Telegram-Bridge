"""Pure application service for pending-input dispatch."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class InputFlowService:
    handle_pending_backend: Callable[..., bool]
    start_session_name_backend: Callable[..., None]

    def handle_pending(self, *args, **kwargs) -> bool:
        return bool(self.handle_pending_backend(*args, **kwargs))

    def start_session_name(self, *args, **kwargs) -> None:
        self.start_session_name_backend(*args, **kwargs)
