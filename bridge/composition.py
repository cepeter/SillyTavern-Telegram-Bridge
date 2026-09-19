"""Explicit startup configuration and root infrastructure composition."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3


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


@dataclass(frozen=True)
class BackgroundRuntime:
    submit_chat: Callable[..., bool]
    register_backlog_dispatcher: Callable[[Callable[[], None]], None]
    begin_shutdown: Callable[[], None]


@dataclass(frozen=True)
class BridgeServices:
    config: BridgeConfig
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime


def load_bridge_config(
    environ: Mapping[str, str],
    *,
    character_dir: Path,
    db_file: Path,
) -> BridgeConfig:
    default_character_file = str(
        environ.get("SILLYTAVERN_DEFAULT_CHARACTER", "")
    ).strip()
    allowed = frozenset(
        value.strip()
        for value in str(
            environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", "")
        ).split(",")
        if value.strip()
    )
    character_dir = Path(character_dir)
    return BridgeConfig(
        bot_token=str(
            environ.get("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "")
        ).strip(),
        api_key=str(environ.get("LLM_API_KEY", "")).strip(),
        default_model=str(environ.get("SILLYTAVERN_MODEL", "")).strip(),
        default_character_file=default_character_file,
        card_file=character_dir / default_character_file,
        db_file=Path(db_file),
        allowed_users=allowed,
    )


def validate_bridge_config(config: BridgeConfig) -> None:
    if not config.bot_token:
        raise ValueError("required Telegram bot token is missing from .env")
    if not config.default_model:
        raise ValueError("required SILLYTAVERN_MODEL is missing from .env")
    if not config.default_character_file:
        raise ValueError(
            "required SILLYTAVERN_DEFAULT_CHARACTER is missing from .env"
        )
    if not config.card_file.is_file():
        raise ValueError(
            "configured default character card does not exist: "
            f"{config.card_file}"
        )


def build_bridge_services(
    config: BridgeConfig,
    *,
    db_factory: Callable[[], sqlite3.Connection],
    telegram: TelegramRuntime,
    background: BackgroundRuntime,
) -> BridgeServices:
    return BridgeServices(
        config=config,
        db_factory=db_factory,
        telegram=telegram,
        background=background,
    )
