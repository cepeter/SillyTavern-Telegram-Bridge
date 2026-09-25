"""Explicit startup configuration and root infrastructure composition."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from bridge.conversation_service import ConversationService
from bridge.delivery_port import DeliveryPort
from bridge.group_director_service import GroupDirectorService
from bridge.group_service import GroupService
from bridge.input_flow_service import InputFlowService
from bridge.job_service import JobService
from bridge.memory_service import MemoryService
from bridge.model_router import ModelRouter
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.settings import AppSettings
from bridge.sync_service import SyncService


@dataclass(frozen=True)
class TelegramRuntime:
    request: Callable[..., object]
    send_text: Callable[..., object]
    download_file: Callable[..., bytes]


@dataclass(frozen=True)
class BackgroundRuntime:
    submit_chat: Callable[..., bool]
    register_backlog_dispatcher: Callable[[Callable[[], None]], None]
    begin_shutdown: Callable[[], None]


@dataclass(frozen=True)
class RequestContext:
    db: sqlite3.Connection = field(repr=False)
    session_id: str
    actor_id: str = ""
    app_settings: AppSettings = field(kw_only=True)


@dataclass(frozen=True)
class BridgeServices:
    config: AppSettings
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime
    jobs: JobService
    conversation: ConversationService
    delivery: DeliveryPort
    group: GroupService
    group_director: GroupDirectorService
    input_flow: InputFlowService
    model_router: ModelRouter
    provider: ProviderPort
    memory: MemoryService
    persona: PersonaService
    sync: SyncService


def build_bridge_services(
    config: AppSettings,
    *,
    db_factory: Callable[[], sqlite3.Connection],
    telegram: TelegramRuntime,
    background: BackgroundRuntime,
    jobs: JobService,
    conversation: ConversationService,
    delivery: DeliveryPort,
    group: GroupService,
    group_director: GroupDirectorService,
    input_flow: InputFlowService,
    model_router: ModelRouter,
    provider: ProviderPort,
    memory: MemoryService,
    persona: PersonaService,
    sync: SyncService,
) -> BridgeServices:
    return BridgeServices(
        config=config,
        db_factory=db_factory,
        telegram=telegram,
        background=background,
        group=group,
        group_director=group_director,
        input_flow=input_flow,
        model_router=model_router,
        provider=provider,
        memory=memory,
        persona=persona,
        sync=sync,
        jobs=jobs,
        conversation=conversation,
        delivery=delivery,
    )
