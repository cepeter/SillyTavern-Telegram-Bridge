"""Ordinary production startup composition for the Telegram bridge."""

from __future__ import annotations

import argparse
import os
from functools import partial as _partial
from pathlib import Path

import bridge.sillytavern_api as _st_api
from bridge import database as _database
from bridge.application_composition import initialize_extensions as _initialize_extensions
from bridge.card_content import card_fields, card_fields_from_file, read_png_chara, safe_character_path
from bridge.command_routes import handle_command_route
from bridge.common import (
    begin_background_shutdown,
    configure_logging,
    enforce_runtime_permissions,
    register_durable_backlog_dispatcher,
    submit_chat_background,
)
from bridge.composition import BackgroundRuntime as _BackgroundRuntime
from bridge.composition import BridgeServices as _BridgeServices
from bridge.composition import TelegramRuntime as _TelegramRuntime
from bridge.config_values import ConfigurationError
from bridge.conversation_service import ConversationService as _ConversationService
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
from bridge.delivery_port import DeliveryPort as _DeliveryPort
from bridge.director_goals import director_goal_policy
from bridge.environment import bootstrap_environment
from bridge.group_core import (
    advance_group_turn,
    claim_group_user_turn,
    group_character_option_label,
    group_current_speaker,
    group_member_labels,
    group_setup_state,
    group_state,
    group_user_turn_allowed,
    pass_group_user_turn,
    resolve_character_file,
    save_group_state,
)
from bridge.group_director_service import GroupDirectorService as _GroupDirectorService
from bridge.group_service import GroupService as _GroupService
from bridge.help import set_bot_commands
from bridge.input_flow_service import InputFlowService as _InputFlowService
from bridge.input_flows import handle_pending_input, start_text_action_input
from bridge.job_service import JobService as _JobService
from bridge.media import delete_outgoing_message_row, send_reply, send_typing
from bridge.memory import (
    get_session_summary,
    purge_hindsight_session,
    retain_session_memory,
    session_summary_for_prompt,
)
from bridge.memory_backend import recall_memory_context
from bridge.memory_service import MemoryService as _MemoryService
from bridge.message_commands import generate_and_store_reply, prepare_message
from bridge.model_router import ModelRouter as _ModelRouter
from bridge.persona_service import PersonaService as _PersonaService
from bridge.persona_sync import (
    PERSONA_EDIT_LOCK,
    default_persona_id,
    delete_native_persona,
    load_personas,
    upsert_native_persona,
)
from bridge.provider_catalog import load_provider_catalog
from bridge.provider_port import ProviderPort as _ProviderPort
from bridge.provider_transport import generate_provider_text
from bridge.repositories import count_persona_references as _count_persona_references
from bridge.repositories import count_session_messages as _count_session_messages
from bridge.runtime_lifecycle import run_bridge_runtime
from bridge.scheduler_safety import DurableWorkerGuard as _DurableWorkerGuard
from bridge.session_naming import handle_session_name_input, start_session_name_input
from bridge.settings import AppSettings, load_app_settings, validate_app_settings
from bridge.sync_api import _live_sync_disable, live_sync_now, live_sync_poll, live_sync_toggle_realtime
from bridge.sync_core import sync_binding
from bridge.sync_service import SyncService as _SyncService
from bridge.telegram import download_telegram_file, send_panel_request, send_text, telegram_request, update_session


def validate_startup_credential(model: str, model_router: _ModelRouter, *, app_settings: AppSettings) -> None:
    route = model_router.route(model)
    spec = dict(route.spec)
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
    if not any(app_settings.environ.get(key_env) for key_env in key_envs):
        raise RuntimeError(f"required provider credential is missing; set one of: {', '.join(key_envs)}")


def _load_startup_config(environ) -> AppSettings:
    settings = load_app_settings(environ, home=Path.home())
    validate_app_settings(settings)
    return settings


def _build_startup_services(
    config: AppSettings,
    *,
    model_router: _ModelRouter,
) -> _BridgeServices:
    durable_worker_guard = _DurableWorkerGuard(_database._lightweight_db_connect)
    provider = _ProviderPort(generate_backend=_partial(generate_provider_text, model_router, app_settings=config))
    delivery = _DeliveryPort(
        request=telegram_request,
        send_text=send_text,
        send_reply=_partial(send_reply, app_settings=config),
        send_typing=send_typing,
        send_panel_request=send_panel_request,
        delete_outgoing_message_row=delete_outgoing_message_row,
    )
    group = _GroupService(
        load_state=group_state,
        save_state=save_group_state,
        user_turn_allowed_backend=group_user_turn_allowed,
        claim_user_turn_backend=claim_group_user_turn,
        pass_user_turn_backend=pass_group_user_turn,
        setup_state_backend=group_setup_state,
        character_option_label_backend=_partial(group_character_option_label, app_settings=config),
        resolve_character_backend=_partial(resolve_character_file, app_settings=config),
        member_labels_backend=_partial(group_member_labels, app_settings=config),
        current_speaker_backend=_partial(group_current_speaker, app_settings=config),
        advance_turn_backend=advance_group_turn,
    )
    input_flow = _InputFlowService(
        handle_pending_backend=handle_pending_input,
        start_session_name_backend=_partial(start_session_name_input, app_settings=config),
        start_text_action_backend=start_text_action_input,
        handle_session_name_backend=handle_session_name_input,
    )
    group_director = _GroupDirectorService(
        load_group_state=group.state,
        safe_character=_partial(safe_character_path, app_settings=config),
        member_labels=group.member_labels,
        card_fields=_partial(card_fields_from_file, app_settings=config),
        generation_settings=get_generation_settings,
        generate_text=provider.generate,
        director_policy=_partial(director_goal_policy, app_settings=config),
        default_model=config.default_model,
    )
    memory = _MemoryService(
        recall_context=_partial(recall_memory_context, app_settings=config),
        summary_for_prompt=(
            lambda db, chat_id, session: session_summary_for_prompt(
                db, chat_id, session, provider_port=provider, app_settings=config
            )
        ),
        summary_state=get_session_summary,
        retain_session=(
            lambda db, chat_id, session, fields: retain_session_memory(
                db, chat_id, session, fields, provider_port=provider, app_settings=config
            )
        ),
        purge_session_memory=_partial(purge_hindsight_session, app_settings=config),
    )
    persona = _PersonaService(
        load_personas=_partial(load_personas, app_settings=config),
        load_default_persona=_partial(default_persona_id, app_settings=config),
        upsert_persona=_partial(upsert_native_persona, app_settings=config),
        delete_persona=_partial(delete_native_persona, app_settings=config),
        update_session_persona=update_session,
        persona_reference_count=_count_persona_references,
        persona_edit_lock=lambda: PERSONA_EDIT_LOCK,
    )
    sync = _SyncService(
        load_binding=sync_binding,
        count_messages=_count_session_messages,
        sync_now_backend=_partial(live_sync_now, app_settings=config, retain_memory=memory.retain),
        toggle_realtime_backend=_partial(live_sync_toggle_realtime, app_settings=config, retain_memory=memory.retain),
        poll_backend=_partial(live_sync_poll, app_settings=config, retain_memory=memory.retain),
        disable_realtime=_live_sync_disable,
        api_configured=_partial(_st_api.live_sync_api_configured, app_settings=config),
        expected_errors=(_st_api.SillyTavernApiError, ValueError),
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
        prepare_worker=durable_worker_guard.prepare,
    )

    def dispatch(
        db,
        token,
        api_key,
        model,
        fields,
        chat_id,
        stripped,
        command,
        session,
        session_id,
        current_model,
        current_persona,
        user_name,
        operation_id=None,
        *,
        request_context,
    ):
        return handle_command_route(
            db,
            token,
            api_key,
            model,
            fields,
            chat_id,
            stripped,
            command,
            session,
            session_id,
            current_model,
            current_persona,
            user_name,
            operation_id,
            request_context=request_context,
            delivery_port=delivery,
            provider_port=provider,
            memory_service=memory,
            persona_service=persona,
            group_service=group,
            sync_service=sync,
            conversation_service=conversation,
        )

    conversation = _ConversationService(
        prepare_message=_partial(
            prepare_message,
            app_settings=config,
            delivery_port=delivery,
            provider_port=provider,
            memory_service=memory,
            persona_service=persona,
            group_service=group,
            input_flow_service=input_flow,
            group_director_service=group_director,
        ),
        dispatch_command=dispatch,
        generate_reply=_partial(
            generate_and_store_reply,
            app_settings=config,
            group_service=group,
            provider_port=provider,
            memory_service=memory,
            persona_service=persona,
        ),
    )
    return _BridgeServices(
        config,
        db_factory=_partial(db_connect, config.db_file, app_settings=config),
        telegram=_TelegramRuntime(
            request=telegram_request,
            send_text=send_text,
            download_file=download_telegram_file,
        ),
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
        delivery=delivery,
        conversation=conversation,
    )


def run_check(services: _BridgeServices) -> int:
    config = services.config
    fields = card_fields(read_png_chara(config.card_file), app_settings=services.config)
    if _st_api.live_sync_api_configured(app_settings=services.config):
        try:
            _st_api.live_sync_client(app_settings=services.config).authenticate()
        except (_st_api.SillyTavernApiError, ValueError) as exc:
            raise SystemExit(f"Live Sync check failed: {exc}") from exc
    me = services.telegram.request(
        config.bot_token,
        "getMe",
    )
    print(f"card={fields['name']}; telegram=@{me.get('username')}; model={config.default_model}; db={config.db_file}")
    print("check=ok")
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    environment = dict(os.environ)
    bootstrap_environment(environment)
    config = _load_startup_config(environment)
    model_router = _ModelRouter(load_catalog=_partial(load_provider_catalog, app_settings=config))
    try:
        validate_startup_credential(config.default_model, model_router, app_settings=config)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    enforce_runtime_permissions(app_settings=config)
    configure_logging(app_settings=config)

    services = _build_startup_services(
        config,
        model_router=model_router,
    )
    _initialize_extensions()
    token = config.bot_token
    set_bot_commands(token)

    if args.check:
        return run_check(services)

    fields = card_fields(read_png_chara(config.card_file), app_settings=config)
    return run_bridge_runtime(services, fields)


def main() -> int:
    try:
        return _main()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
