"""Explicit startup configuration and root infrastructure composition."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
from bridge.sync_service import SyncService


@dataclass(frozen=True)
class BridgeConfig:
    bot_token: str = field(repr=False)
    api_key: str = field(repr=False)
    default_model: str
    default_character_file: str
    card_file: Path
    db_file: Path
    allowed_users: frozenset[str]


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


@dataclass(frozen=True)
class BridgeServices:
    config: BridgeConfig
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


def load_bridge_config(
    environ: Mapping[str, str],
    *,
    character_dir: Path,
    db_file: Path,
) -> BridgeConfig:
    default_character_file = str(environ.get("SILLYTAVERN_DEFAULT_CHARACTER", "")).strip()
    allowed = frozenset(
        value.strip()
        for value in str(environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", "")).split(",")
        if value.strip()
    )
    character_dir = Path(character_dir)
    return BridgeConfig(
        bot_token=str(environ.get("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "")).strip(),
        api_key=str(environ.get("LLM_API_KEY", "")).strip(),
        default_model=str(environ.get("SILLYTAVERN_MODEL", "")).strip(),
        default_character_file=default_character_file,
        card_file=character_dir / default_character_file,
        db_file=Path(db_file),
        allowed_users=allowed,
    )


def validate_bridge_config(config: BridgeConfig) -> None:
    if not config.bot_token:
        raise ValueError("required Telegram bot token is missing from environment")
    if not config.default_model:
        raise ValueError("required SILLYTAVERN_MODEL is missing from environment")
    if not config.default_character_file:
        raise ValueError("required SILLYTAVERN_DEFAULT_CHARACTER is missing from environment")
    if not config.card_file.is_file():
        raise ValueError(f"configured default character card does not exist: {config.card_file}")

    if not config.allowed_users:
        raise ValueError("required SILLYTAVERN_TELEGRAM_ALLOWED_USERS is missing from environment")

    invalid_users = sorted(user_id for user_id in config.allowed_users if not user_id.isdecimal())
    if invalid_users:
        raise ValueError(
            "SILLYTAVERN_TELEGRAM_ALLOWED_USERS must contain "
            "only numeric Telegram user IDs: " + ", ".join(invalid_users)
        )


def build_bridge_services(
    config: BridgeConfig,
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
