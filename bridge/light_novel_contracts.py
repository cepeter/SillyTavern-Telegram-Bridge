"""Narrow structural ports for Light Novel adapters; no composition-root imports."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Protocol

from bridge.delivery_port import DeliveryPort
from bridge.job_service import JobService
from bridge.persona_service import PersonaService
from bridge.port_contracts import SendText, TelegramRequest
from bridge.provider_port import ProviderPort
from bridge.session_service import SessionService
from bridge.settings import AppSettings


class ChoiceTelegram(Protocol):
    @property
    def request(self) -> TelegramRequest: ...

    @property
    def send_text(self) -> SendText: ...


class LightNovelRuntime(Protocol):
    @property
    def persona(self) -> PersonaService: ...

    @property
    def config(self) -> AppSettings: ...

    @property
    def db_factory(self) -> Callable[[], sqlite3.Connection]: ...

    @property
    def jobs(self) -> JobService: ...

    @property
    def session(self) -> SessionService: ...

    @property
    def delivery(self) -> DeliveryPort: ...

    @property
    def provider(self) -> ProviderPort: ...

    @property
    def telegram(self) -> ChoiceTelegram: ...
