"""Ordinary production startup composition for the Telegram bridge."""
from __future__ import annotations

from functools import partial as _partial
import argparse
import os

from bridge import database as _database
from bridge.application_composition import initialize_extensions as _initialize_extensions
from bridge.card_content import (
    card_fields,
    card_fields_from_file,
    read_png_chara,
    safe_character_path,
)
from bridge.cards import default_persona_id
from bridge.common import (
    begin_background_shutdown,
    configure_logging,
    enforce_runtime_permissions,
    register_durable_backlog_dispatcher,
    submit_chat_background,
)
from bridge.composition import (
    BackgroundRuntime as _BackgroundRuntime,
    BridgeConfig as _BridgeConfig,
    BridgeServices as _BridgeServices,
    TelegramRuntime as _TelegramRuntime,
    build_bridge_services as _build_bridge_services_value,
    load_bridge_config as _load_bridge_config_value,
    validate_bridge_config as _validate_bridge_config_value,
)
from bridge.config import (
    CARD_FILE,
    CHARACTER_DIR,
    DB_FILE,
    DEFAULT_CHARACTER_FILE,
)
from bridge.database import (
    db_connect,
    enqueue_job,
    finish_job,
    get_generation_settings,
    job_actor_id,
    mark_job_running,
    mark_job_scheduled,
    recover_jobs,
)
from bridge.extension_registry import get_director_customization as _get_director_customization_value
from bridge.generation import generate_text, resolve_provider_model
from bridge.group_core import group_member_labels, group_state
from bridge.group_director_service import GroupDirectorService as _GroupDirectorService
from bridge.help import set_bot_commands
from bridge.input_flows import PERSONA_EDIT_LOCK
from bridge.job_service import JobService as _JobService
from bridge.media import get_provider_spec
from bridge.memory import (
    get_session_summary,
    purge_hindsight_session,
    retain_session_memory,
    session_summary_for_prompt,
)
from bridge.memory_backend import recall_memory_context
from bridge.memory_service import MemoryService as _MemoryService
from bridge.persona_service import PersonaService as _PersonaService
from bridge.persona_sync import delete_native_persona, load_personas, upsert_native_persona
from bridge.repositories import (
    count_persona_references as _count_persona_references,
    count_session_messages as _count_session_messages,
)
from bridge.runtime_lifecycle import run_bridge_runtime
from bridge.scheduler_safety import DurableWorkerGuard as _DurableWorkerGuard
from bridge.sync_api import (
    SillyTavernApiError,
    _phase3_disable,
    phase3_api_configured,
    phase3_client,
    phase3_sync_now,
    phase3_sync_poll,
    phase3_toggle_realtime,
    refresh_phase3_config,
)
from bridge.sync_core import sync_binding
from bridge.sync_service import SyncService as _SyncService
from bridge.telegram import send_text, telegram_request, update_session

_DURABLE_WORKER_GUARD = _DurableWorkerGuard(
    _database._lightweight_db_connect
)

def validate_startup_credential(model: str) -> None:
    provider_id, _ = resolve_provider_model(model)
    spec = get_provider_spec(provider_id)
    transport = str(spec.get("transport") or "")
    if transport == "opencode_muse":
        return
    configured_key_env = spec.get("api_key_env")
    if configured_key_env:
        key_envs = [str(configured_key_env)]
    elif transport == "anthropic_messages":
        key_envs = ["ANTHROPIC_API_KEY", "LLM_API_KEY"]
    else:
        key_envs = ["LLM_API_KEY"]
    if not any(os.environ.get(key_env) for key_env in key_envs):
        raise RuntimeError(f"required provider credential is missing; set one of: {', '.join(key_envs)}")












def _require_runtime_configuration(model: str) -> None:
    if not DEFAULT_CHARACTER_FILE:
        raise SystemExit("required SILLYTAVERN_DEFAULT_CHARACTER is missing from .env")
    if not model:
        raise SystemExit("required SILLYTAVERN_MODEL is missing from .env")
    if not CARD_FILE.is_file():
        raise SystemExit(f"configured default character card does not exist: {CARD_FILE}")


def _load_startup_config(environ) -> _BridgeConfig:
    config = _load_bridge_config_value(
        environ,
        character_dir=CHARACTER_DIR,
        db_file=DB_FILE,
    )
    try:
        _validate_bridge_config_value(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return config


def _build_startup_services(
    config: _BridgeConfig,
) -> _BridgeServices:
    group_director = _GroupDirectorService(
        load_group_state=group_state,
        safe_character=safe_character_path,
        member_labels=group_member_labels,
        card_fields=card_fields_from_file,
        generation_settings=get_generation_settings,
        generate_text=generate_text,
        director_customization=_get_director_customization_value,
        default_model=config.default_model,
    )
    memory = _MemoryService(
        recall_context=recall_memory_context,
        summary_for_prompt=session_summary_for_prompt,
        summary_state=get_session_summary,
        retain_session=retain_session_memory,
        purge_session_memory=purge_hindsight_session,
    )
    persona = _PersonaService(
        load_personas=load_personas,
        load_default_persona=default_persona_id,
        upsert_persona=upsert_native_persona,
        delete_persona=delete_native_persona,
        update_session_persona=update_session,
        persona_reference_count=_count_persona_references,
        persona_edit_lock=lambda: PERSONA_EDIT_LOCK,
    )
    sync = _SyncService(
        load_binding=sync_binding,
        count_messages=_count_session_messages,
        sync_now_backend=phase3_sync_now,
        toggle_realtime_backend=phase3_toggle_realtime,
        poll_backend=phase3_sync_poll,
        disable_realtime=_phase3_disable,
        api_configured=phase3_api_configured,
        expected_errors=(SillyTavernApiError, ValueError),
    )
    background = _BackgroundRuntime(
        submit_chat=submit_chat_background,
        register_backlog_dispatcher=register_durable_backlog_dispatcher,
        begin_shutdown=begin_background_shutdown,
    )
    jobs = _JobService(
        enqueue_backend=enqueue_job,
        actor_backend=job_actor_id,
        schedule_backend=mark_job_scheduled,
        start_backend=mark_job_running,
        finish_backend=finish_job,
        recover_backend=recover_jobs,
        submit_chat=background.submit_chat,
        prepare_worker=_DURABLE_WORKER_GUARD.prepare,
    )
    return _build_bridge_services_value(
        config,
        db_factory=_partial(db_connect, config.db_file),
        telegram=_TelegramRuntime(
            request=telegram_request,
            send_text=send_text,
        ),
        background=background,
        group_director=group_director,
        memory=memory,
        persona=persona,
        sync=sync,
        jobs=jobs,
    )


def run_check(services: _BridgeServices) -> int:
    config = services.config
    fields = card_fields(read_png_chara(config.card_file))
    if phase3_api_configured():
        try:
            phase3_client().authenticate()
        except (SillyTavernApiError, ValueError) as exc:
            raise SystemExit(f"Live Sync check failed: {exc}") from exc
    me = services.telegram.request(
        config.bot_token,
        "getMe",
    )
    print(
        f"card={fields['name']}; "
        f"telegram=@{me.get('username')}; "
        f"model={config.default_model}; "
        f"db={config.db_file}"
    )
    print("check=ok")
    return 0


def main() -> int:
    _initialize_extensions()

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    refresh_phase3_config()

    config = _load_startup_config(os.environ)
    try:
        validate_startup_credential(config.default_model)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    enforce_runtime_permissions()
    configure_logging()

    services = _build_startup_services(config)
    token = config.bot_token
    model = config.default_model
    set_bot_commands(token)

    if args.check:
        return run_check(services)

    fields = card_fields(read_png_chara(config.card_file))
    return run_bridge_runtime(services, fields)


if __name__ == "__main__":
    raise SystemExit(main())