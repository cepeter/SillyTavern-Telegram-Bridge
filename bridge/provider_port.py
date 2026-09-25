"""Pure application port for model-provider generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from bridge.port_contracts import CancellationEvent, ProviderGenerate


@dataclass(frozen=True)
class ProviderPort:
    generate_backend: ProviderGenerate

    def generate(
        self,
        api_key: str,
        model: str,
        messages: list[dict],
        *,
        session_id: str = "telegram",
        settings: Mapping[str, object] | None = None,
        stream_callback: Callable[[str], object] | None = None,
        cancel_event: CancellationEvent | None = None,
        force_non_stream: bool = False,
        request_timeout: float | None = None,
    ) -> str:
        return str(
            self.generate_backend(
                api_key,
                model,
                messages,
                session_id=session_id,
                settings=settings,
                stream_callback=stream_callback,
                cancel_event=cancel_event,
                force_non_stream=force_non_stream,
                request_timeout=request_timeout,
            )
        )
