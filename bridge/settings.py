"""Immutable application settings, loaded explicitly from an environment snapshot.

Importing this module performs no environment or filesystem reads. Each call to
load_app_settings owns a detached environment mapping and derived path values.
Secrets are exposed only to adapters that explicitly receive this instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from bridge.config_values import ConfigurationError, read_float, read_int


@dataclass(frozen=True, kw_only=True)
class AppSettings:
    bot_token: str = field(repr=False)
    api_key: str = field(repr=False)
    allowed_users: frozenset[str]
    home: Path
    environment_file: Path
    bridge_home: Path
    db_file: Path
    log_file: Path
    provider_config_file: Path
    model_cache_file: Path
    character_backup_dir: Path
    sillytavern_dir: Path
    character_dir: Path
    world_dir: Path
    system_prompts_dir: Path
    default_model: str
    default_character_file: str
    card_file: Path
    default_user_name: str
    native_persona_settings_file: Path
    native_persona_avatar_dir: Path
    native_persona_backup_dir: Path
    model_refresh_seconds: int
    default_allowed_user: str
    rag_embedding_url: str
    rag_embedding_model: str
    rag_embedding_dimensions: int
    rag_embedding_revision: str
    rag_max_extracted_chars: int
    rag_max_pdf_pages: int
    rag_pdf_parse_timeout_seconds: int
    rag_semantic_candidates: int
    context_window_tokens: int
    context_output_reserve_tokens: int
    context_history_candidates: int
    live_sync_api_url: str
    live_sync_api_handle: str
    live_sync_api_password: str | None = field(repr=False)
    live_sync_timeout_seconds: int
    live_sync_interval_seconds: float
    tts_bin: str
    tts_voice: str
    stt_model: str
    performance_log: bool
    enforce_prompt_permissions: bool
    opencode_client_version: str
    update_repo_dir: Path
    update_live_dir: Path
    update_allowed_signers: Path | None
    update_service: str
    environ: Mapping[str, str] = field(repr=False, compare=False)


def _path(environ: Mapping[str, str], name: str, default: Path, home: Path) -> Path:
    value = str(environ.get(name, str(default))).strip()
    if not value:
        raise ConfigurationError(f"{name} must name a path")
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    path = Path(value)
    return path if path.is_absolute() else home / path


def _boolean(environ: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = environ.get(name, str(default)).strip().casefold()
    if value in {"true", "yes", "on", "1"}:
        return True
    if value in {"false", "no", "off", "0", ""}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def load_app_settings(environ: Mapping[str, str], *, home: Path) -> AppSettings:
    """Build an independent, validated settings value without changing process state."""
    values = MappingProxyType(dict(environ))
    home = Path(home)
    if not home.is_absolute():
        raise ConfigurationError("application home must be an absolute path")
    bridge_home = _path(values, "SILLYTAVERN_BRIDGE_HOME", home / ".local/share/sillytavern-telegram", home)
    native = _path(values, "SILLYTAVERN_DIR", bridge_home.parent / "SillyTavern", home)
    character_dir = _path(values, "SILLYTAVERN_CHARACTER_DIR", native / "data/default-user/characters", home)
    character = values.get("SILLYTAVERN_DEFAULT_CHARACTER", "").strip()
    signer_raw = values.get("SILLYTAVERN_UPDATE_ALLOWED_SIGNERS", "").strip()
    return AppSettings(
        bot_token=values.get("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "").strip(),
        api_key=values.get("LLM_API_KEY", "").strip(),
        allowed_users=frozenset(
            value.strip() for value in values.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", "").split(",") if value.strip()
        ),
        home=home,
        environment_file=_path(values, "SILLYTAVERN_ENV_FILE", home / ".local/share/sillytavern-telegram/.env", home),
        bridge_home=bridge_home,
        db_file=bridge_home / "scripts/sillytavern_telegram.sqlite3",
        log_file=bridge_home / "logs/sillytavern_telegram_bridge.log",
        provider_config_file=_path(
            values, "SILLYTAVERN_PROVIDER_CONFIG", bridge_home / "sillytavern_telegram_providers.yaml", home
        ),
        model_cache_file=_path(values, "SILLYTAVERN_MODEL_CACHE", bridge_home / "model_catalog_cache.json", home),
        character_backup_dir=_path(
            values, "SILLYTAVERN_CHARACTER_BACKUP_DIR", bridge_home / "backups/sillytavern/characters", home
        ),
        sillytavern_dir=native,
        character_dir=character_dir,
        world_dir=_path(values, "SILLYTAVERN_WORLD_DIR", native / "data/default-user/worlds", home),
        system_prompts_dir=_path(
            values, "SILLYTAVERN_SYSTEM_PROMPTS_DIR", native / "data/default-user/sysprompt", home
        ),
        default_model=values.get("SILLYTAVERN_MODEL", "").strip(),
        default_character_file=character,
        card_file=character_dir / character,
        default_user_name=values.get("SILLYTAVERN_DEFAULT_USER_NAME", "").strip(),
        native_persona_settings_file=_path(
            values, "SILLYTAVERN_NATIVE_SETTINGS_FILE", native / "data/default-user/settings.json", home
        ),
        native_persona_avatar_dir=_path(
            values, "SILLYTAVERN_NATIVE_AVATAR_DIR", native / "data/default-user/User Avatars", home
        ),
        native_persona_backup_dir=bridge_home / "backups/sillytavern/personas",
        model_refresh_seconds=read_int(values, "SILLYTAVERN_MODEL_REFRESH_SECONDS", 3600, minimum=1, maximum=86400),
        default_allowed_user=values.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", ""),
        rag_embedding_url=values.get("SILLYTAVERN_RAG_EMBEDDING_URL", "http://127.0.0.1:8891/v1/embeddings"),
        rag_embedding_model=values.get("SILLYTAVERN_RAG_EMBEDDING_MODEL", "text-embedding-3-small"),
        rag_embedding_dimensions=read_int(
            values, "SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS", 1536, minimum=1, maximum=65536
        ),
        rag_embedding_revision=values.get("SILLYTAVERN_RAG_EMBEDDING_REVISION", "1"),
        rag_max_extracted_chars=read_int(
            values, "SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS", 1000000, minimum=1, maximum=10000000
        ),
        rag_max_pdf_pages=read_int(values, "SILLYTAVERN_RAG_MAX_PDF_PAGES", 200, minimum=1, maximum=10000),
        rag_pdf_parse_timeout_seconds=read_int(
            values, "SILLYTAVERN_RAG_PDF_PARSE_TIMEOUT_SECONDS", 45, minimum=1, maximum=300
        ),
        rag_semantic_candidates=read_int(values, "SILLYTAVERN_RAG_SEMANTIC_CANDIDATES", 384, minimum=64, maximum=2048),
        context_window_tokens=read_int(
            values, "SILLYTAVERN_CONTEXT_WINDOW_TOKENS", 32768, minimum=4096, maximum=1000000
        ),
        context_output_reserve_tokens=read_int(
            values, "SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS", 4096, minimum=512, maximum=131072
        ),
        context_history_candidates=read_int(
            values, "SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES", 96, minimum=8, maximum=512
        ),
        live_sync_api_url=values.get("SILLYTAVERN_SYNC_API_URL", "").strip().rstrip("/"),
        live_sync_api_handle=values.get("SILLYTAVERN_SYNC_API_HANDLE", "").strip(),
        live_sync_api_password=values.get("SILLYTAVERN_SYNC_API_PASSWORD") or None,
        live_sync_timeout_seconds=read_int(values, "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS", 10, minimum=2, maximum=30),
        live_sync_interval_seconds=read_float(
            values, "SILLYTAVERN_SYNC_API_INTERVAL_SECONDS", 2.0, minimum=1.0, maximum=30.0
        ),
        tts_bin=values.get("SILLYTAVERN_TTS_BIN", str(bridge_home / "venv/bin/edge-tts")),
        tts_voice=values.get("SILLYTAVERN_TTS_VOICE", "").strip(),
        stt_model=values.get("SILLYTAVERN_STT_MODEL", "base"),
        performance_log=_boolean(values, "SILLYTAVERN_PERF_LOG"),
        enforce_prompt_permissions=_boolean(values, "SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS"),
        opencode_client_version=values.get("OPENCODE_CLIENT_VERSION", "1.18.31"),
        update_repo_dir=_path(values, "SILLYTAVERN_BRIDGE_SOURCE_DIR", Path(__file__).resolve().parents[1], home),
        update_live_dir=_path(values, "SILLYTAVERN_LIVE_BRIDGE_DIR", bridge_home / "live", home),
        update_allowed_signers=_path(values, "SILLYTAVERN_UPDATE_ALLOWED_SIGNERS", home, home) if signer_raw else None,
        update_service=values.get("SILLYTAVERN_UPDATE_SERVICE", "sillytavern-telegram.service"),
        environ=values,
    )


def validate_app_settings(settings: AppSettings) -> None:
    """Validate required runtime inputs only when an application is started."""
    if not settings.bot_token:
        raise ConfigurationError("required Telegram bot token is missing from environment")
    if not settings.default_model:
        raise ConfigurationError("required SILLYTAVERN_MODEL is missing from environment")
    if not settings.default_character_file:
        raise ConfigurationError("required SILLYTAVERN_DEFAULT_CHARACTER is missing from environment")
    if not settings.card_file.is_file():
        raise ConfigurationError(f"configured default character card does not exist: {settings.card_file}")
    if not settings.allowed_users:
        raise ConfigurationError("required SILLYTAVERN_TELEGRAM_ALLOWED_USERS is missing from environment")
    if any(not user.isdecimal() for user in settings.allowed_users):
        raise ConfigurationError("SILLYTAVERN_TELEGRAM_ALLOWED_USERS must contain only numeric Telegram user IDs")
