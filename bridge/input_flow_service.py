"""Pure application service for pending-input dispatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class InputFlowService:
    handle_pending_backend: Callable[..., bool]
    start_session_name_backend: Callable[..., None]
    start_text_action_backend: Callable[..., None]
    handle_session_name_backend: Callable[..., bool]

    def handle_pending(self, *args: object, **kwargs: object) -> bool:
        return bool(
            self.handle_pending_backend(
                *args,
                handle_session_name=self.handle_session_name_backend,
                **kwargs,
            )
        )

    def start_session_name(self, *args: object, **kwargs: object) -> None:
        self.start_session_name_backend(*args, **kwargs)

    def start_text_action(self, *args: object, **kwargs: object) -> None:
        self.start_text_action_backend(*args, **kwargs)
