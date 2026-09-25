"""One-time explicit application extension setup for tests.

This helper exists only because both unittest discovery and pytest import test
modules independently. It performs no patch propagation, module mutation, or
dependency binding.
"""

from __future__ import annotations

from functools import partial as _partial
from types import SimpleNamespace

from settings_test_support import make_test_settings

from bridge.application_composition import initialize_extensions
from bridge.delivery_port import DeliveryPort
from bridge.input_flow_service import InputFlowService
from bridge.memory_service import MemoryService
from bridge.model_router import ModelRouter
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.sync_service import SyncService

_INITIALIZED = False


def ensure_application_extensions() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    initialize_extensions()
    _INITIALIZED = True


def make_test_request_context(
    db=None, session_id: str = "test-session", actor_id: str = "test-user", *, app_settings=None
) -> RequestContext:
    """Return an explicit request context for panel/router tests."""
    if app_settings is None:
        app_settings = make_test_settings()
    return RequestContext(db, session_id, actor_id, app_settings=app_settings)


def make_test_input_flow_service(
    *,
    handle_pending_backend=None,
    start_session_name_backend=None,
    start_text_action_backend=None,
    handle_session_name_backend=None,
    app_settings=None,
) -> InputFlowService:
    if app_settings is None:
        app_settings = make_test_settings()
    if handle_pending_backend is None:
        from bridge.input_flows import handle_pending_input

        handle_pending_backend = handle_pending_input
    if start_text_action_backend is None:
        from bridge.input_flows import start_text_action_input

        start_text_action_backend = start_text_action_input
    if start_session_name_backend is None or handle_session_name_backend is None:
        from bridge.session_naming import handle_session_name_input, start_session_name_input

        if start_session_name_backend is None:
            start_session_name_backend = _partial(start_session_name_input, app_settings=app_settings)
        if handle_session_name_backend is None:
            handle_session_name_backend = handle_session_name_input
    return InputFlowService(
        handle_pending_backend=handle_pending_backend,
        start_session_name_backend=start_session_name_backend,
        start_text_action_backend=start_text_action_backend,
        handle_session_name_backend=handle_session_name_backend,
    )


def make_test_delivery_port(
    *,
    request=None,
    send_text=None,
    send_reply=None,
    send_typing=None,
    send_panel_request=None,
    delete_outgoing_message_row=None,
) -> DeliveryPort:
    return DeliveryPort(
        request=request or (lambda *_args, **_kwargs: {}),
        send_text=send_text or (lambda *_args, **_kwargs: []),
        send_reply=send_reply or (lambda *_args, **_kwargs: None),
        send_typing=send_typing or (lambda *_args, **_kwargs: None),
        send_panel_request=(send_panel_request or (lambda *_args, **_kwargs: {})),
        delete_outgoing_message_row=(delete_outgoing_message_row or (lambda *_args, **_kwargs: None)),
    )


def make_test_model_router(*, catalog=None) -> ModelRouter:
    return ModelRouter(load_catalog=lambda: dict(catalog or {}))


def make_test_provider_port(*, generate_backend=None) -> ProviderPort:
    return ProviderPort(generate_backend=generate_backend or (lambda *_args, **_kwargs: "test provider response"))


def make_test_memory_service(*, purge_session_memory=None) -> MemoryService:
    """Return an explicit MemoryService for tests that do not compose startup."""
    return MemoryService(
        recall_context=lambda *_args, **_kwargs: "",
        summary_for_prompt=lambda *_args, **_kwargs: "",
        summary_state=lambda *_args, **_kwargs: ("", 0),
        retain_session=lambda *_args, **_kwargs: None,
        purge_session_memory=(
            purge_session_memory if purge_session_memory is not None else (lambda *_args, **_kwargs: 0)
        ),
    )


def make_test_persona_service(*, personas=None) -> PersonaService:
    """Return an explicit PersonaService for tests that do not compose startup."""
    catalog = dict(
        personas
        or {
            "test-user.png": {
                "name": "Test User",
                "description": "Test persona",
                "sillytavern_avatar": "test-user.png",
            }
        }
    )

    def upsert(persona_id, name, description):
        avatar = persona_id if str(persona_id).endswith(".png") else f"bridge-{persona_id}.png"
        catalog[avatar] = {
            "name": name,
            "description": description,
            "sillytavern_avatar": avatar,
        }
        return avatar

    def delete(persona_id):
        return catalog.pop(str(persona_id), None) is not None

    return PersonaService(
        load_personas=lambda: dict(catalog),
        load_default_persona=lambda: next(iter(catalog), ""),
        upsert_persona=upsert,
        delete_persona=delete,
        update_session_persona=lambda *_args, **_kwargs: None,
        persona_reference_count=lambda *_args, **_kwargs: 0,
    )


def make_native_test_persona_service(*, app_settings=None) -> PersonaService:
    """Compose PersonaService from the same canonical collaborators as startup."""
    if app_settings is None:
        app_settings = make_test_settings()
    from bridge.persona_sync import (
        PERSONA_EDIT_LOCK,
        default_persona_id,
        delete_native_persona,
        load_personas,
        upsert_native_persona,
    )
    from bridge.repositories import count_persona_references
    from bridge.session_core import update_session

    return PersonaService(
        load_personas=_partial(load_personas, app_settings=app_settings),
        load_default_persona=_partial(default_persona_id, app_settings=app_settings),
        upsert_persona=_partial(upsert_native_persona, app_settings=app_settings),
        delete_persona=_partial(delete_native_persona, app_settings=app_settings),
        update_session_persona=update_session,
        persona_reference_count=count_persona_references,
        persona_edit_lock=lambda: PERSONA_EDIT_LOCK,
    )


def make_test_sync_service() -> SyncService:
    """Return an inert explicit SyncService for routing tests."""
    return SyncService(
        load_binding=lambda *_args, **_kwargs: {},
        count_messages=lambda *_args, **_kwargs: 0,
        sync_now_backend=lambda *_args, **_kwargs: "unchanged",
        toggle_realtime_backend=lambda *_args, **_kwargs: "realtime API sync disabled",
        poll_backend=lambda *_args, **_kwargs: None,
        disable_realtime=lambda *_args, **_kwargs: None,
        api_configured=lambda: False,
        expected_errors=(ValueError,),
    )


def make_test_group_service(*, app_settings=None):
    """Compose GroupService from the canonical group-core implementation."""
    if app_settings is None:
        app_settings = make_test_settings()
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
    from bridge.group_service import GroupService

    return GroupService(
        load_state=group_state,
        save_state=save_group_state,
        user_turn_allowed_backend=group_user_turn_allowed,
        claim_user_turn_backend=claim_group_user_turn,
        pass_user_turn_backend=pass_group_user_turn,
        setup_state_backend=group_setup_state,
        character_option_label_backend=_partial(group_character_option_label, app_settings=app_settings),
        resolve_character_backend=_partial(resolve_character_file, app_settings=app_settings),
        member_labels_backend=_partial(group_member_labels, app_settings=app_settings),
        current_speaker_backend=_partial(group_current_speaker, app_settings=app_settings),
        advance_turn_backend=advance_group_turn,
    )


class _TestGroupDirector:
    def plan(self, *_args, **_kwargs):
        return None

    def prompt_context(self, *_args, **_kwargs):
        return ""


def make_test_conversation_service(
    *,
    app_settings=None,
    prepare=None,
    dispatch=None,
    generate=None,
    delivery=None,
    provider=None,
    memory=None,
    persona=None,
    group=None,
    input_flow=None,
    group_director=None,
    sync=None,
):
    from functools import partial

    from bridge.command_routes import handle_command_route
    from bridge.conversation_service import ConversationService
    from bridge.message_commands import generate_and_store_reply, prepare_message

    if app_settings is None:
        app_settings = make_test_settings()
    delivery = delivery or make_test_delivery_port()
    provider = provider or make_test_provider_port()
    memory = memory or make_test_memory_service()
    persona = persona or make_test_persona_service()
    group = group or make_test_group_service(app_settings=app_settings)
    input_flow = input_flow or make_test_input_flow_service(app_settings=app_settings)
    group_director = group_director or _TestGroupDirector()
    sync = sync or make_test_sync_service()

    def dispatch_default(
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

    conversation = ConversationService(
        prepare_message=prepare
        or partial(
            prepare_message,
            app_settings=app_settings,
            delivery_port=delivery,
            provider_port=provider,
            memory_service=memory,
            persona_service=persona,
            group_service=group,
            input_flow_service=input_flow,
            group_director_service=group_director,
        ),
        dispatch_command=dispatch or dispatch_default,
        generate_reply=generate
        or partial(
            generate_and_store_reply,
            app_settings=app_settings,
            group_service=group,
            provider_port=provider,
            memory_service=memory,
            persona_service=persona,
        ),
    )
    return conversation


def make_test_application_services(
    *,
    memory=None,
    persona=None,
    sync=None,
    group=None,
    group_director=None,
    input_flow=None,
    model_router=None,
    provider=None,
    conversation=None,
    delivery=None,
    app_settings=None,
):
    """Return an explicit test-only application service graph for routers."""
    if app_settings is None:
        app_settings = make_test_settings()
    delivery = delivery or make_test_delivery_port()
    provider = provider or make_test_provider_port()
    memory = memory or make_test_memory_service()
    persona = persona or make_test_persona_service()
    sync = sync or make_test_sync_service()
    group = group or make_test_group_service(app_settings=app_settings)
    group_director = group_director or _TestGroupDirector()
    input_flow = input_flow or make_test_input_flow_service(app_settings=app_settings)
    conversation = conversation or make_test_conversation_service(
        app_settings=app_settings,
        delivery=delivery,
        provider=provider,
        memory=memory,
        persona=persona,
        sync=sync,
        group=group,
        group_director=group_director,
        input_flow=input_flow,
    )
    return SimpleNamespace(
        config=app_settings,
        memory=memory,
        persona=persona,
        sync=sync,
        group=group,
        group_director=group_director,
        input_flow=input_flow,
        model_router=model_router or make_test_model_router(),
        provider=provider,
        conversation=conversation,
        delivery=delivery,
        session=make_test_session_service(app_settings=app_settings),
    )


def make_native_test_sync_service(*, app_settings=None, retain_memory=None) -> SyncService:
    """Compose SyncService from the canonical collaborators used by startup."""
    if app_settings is None:
        app_settings = make_test_settings()
    if retain_memory is None:
        retain_memory = make_test_memory_service().retain
    import bridge.sillytavern_api as _st_api
    from bridge.repositories import count_session_messages
    from bridge.sync_api import _live_sync_disable, live_sync_now, live_sync_poll, live_sync_toggle_realtime
    from bridge.sync_core import sync_binding

    return SyncService(
        load_binding=sync_binding,
        count_messages=count_session_messages,
        sync_now_backend=_partial(live_sync_now, app_settings=app_settings, retain_memory=retain_memory),
        toggle_realtime_backend=_partial(
            live_sync_toggle_realtime, app_settings=app_settings, retain_memory=retain_memory
        ),
        poll_backend=_partial(live_sync_poll, app_settings=app_settings, retain_memory=retain_memory),
        disable_realtime=_live_sync_disable,
        api_configured=_partial(_st_api.live_sync_api_configured, app_settings=app_settings),
        expected_errors=(_st_api.SillyTavernApiError, ValueError),
    )


def make_test_session_service(*, app_settings=None, memory_service=None):
    from bridge.session_core import (
        create_session,
        delete_session_data,
        ensure_session,
        list_sessions,
        load_session,
        update_session,
    )
    from bridge.session_service import SessionService

    if app_settings is None:
        app_settings = make_test_settings()
    if memory_service is None:
        memory_service = make_test_memory_service()
    return SessionService(
        load_backend=_partial(load_session, app_settings=app_settings),
        ensure_backend=_partial(ensure_session, app_settings=app_settings),
        create_backend=_partial(create_session, app_settings=app_settings),
        update_backend=update_session,
        list_backend=list_sessions,
        delete_backend=_partial(delete_session_data, memory_service=memory_service),
    )
