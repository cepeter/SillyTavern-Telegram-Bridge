"""Pure application service for pending-input dispatch."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class InputFlowService:
    handle_pending_backend: Callable[..., bool]

    def handle_pending(self, *args, **kwargs) -> bool:
        return bool(self.handle_pending_backend(*args, **kwargs))
