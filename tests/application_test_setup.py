"""One-time explicit application extension setup for tests.

This helper exists only because both unittest discovery and pytest import test
modules independently. It performs no patch propagation, module mutation, or
dependency binding.
"""
from __future__ import annotations

from types import SimpleNamespace

from bridge.application_composition import initialize_extensions
from bridge.composition import RequestContext
from bridge.delivery_port import DeliveryPort
from bridge.input_flow_service import InputFlowService
from bridge.memory_service import MemoryService
from bridge.model_router import ModelRouter
from bridge.provider_port import ProviderPort
from bridge.persona_service import PersonaService
from bridge.sync_service import SyncService


_INITIALIZED = False


def ensure_application_extensions() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    initialize_extensions()
    _INITIALIZED = True


def make_test_request_context(
    db=None,
    session_id: str = "test-session",
    actor_id: str = "test-user",
) -> RequestContext:
    """Return an explicit request context for panel/router tests."""
    return RequestContext(db, session_id, actor_id)


def make_test_input_flow_service(*, handle_pending_backend=None) -> InputFlowService:
    if handle_pending_backend is None:
        from bridge.input_flows import handle_pending_input
        handle_pending_backend = handle_pending_input
    return InputFlowService(handle_pending_backend=handle_pending_backend)


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
        send_panel_request=(
            send_panel_request
            or (lambda *_args, **_kwargs: {})
        ),
        delete_outgoing_message_row=(
            delete_outgoing_message_row
            or (lambda *_args, **_kwargs: None)
        ),
    )


def make_test_model_router(*, catalog=None) -> ModelRouter:
    return ModelRouter(load_catalog=lambda: dict(catalog or {}))


def make_test_provider_port(*, generate_backend=None) -> ProviderPort:
    return ProviderPort(
        generate_backend=generate_backend
        or (lambda *_args, **_kwargs: "test provider response")
    )


def make_test_memory_service(*, purge_session_memory=None) -> MemoryService:
    """Return an explicit MemoryService for tests that do not compose startup."""
    return MemoryService(
        recall_context=lambda *_args, **_kwargs: "",
        summary_for_prompt=lambda *_args, **_kwargs: "",
        summary_state=lambda *_args, **_kwargs: ("", 0),
        retain_session=lambda *_args, **_kwargs: None,
        purge_session_memory=(
            purge_session_memory
            if purge_session_memory is not None
            else (lambda *_args, **_kwargs: 0)
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


def make_native_test_persona_service() -> PersonaService:
    """Compose PersonaService from the same canonical collaborators as startup."""
    from bridge.cards import default_persona_id
    from bridge.persona_sync import (
        PERSONA_EDIT_LOCK,
        delete_native_persona,
        load_personas,
        upsert_native_persona,
    )
    from bridge.repositories import count_persona_references
    from bridge.telegram import update_session

    return PersonaService(
        load_personas=load_personas,
        load_default_persona=default_persona_id,
        upsert_persona=upsert_native_persona,
        delete_persona=delete_native_persona,
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


def make_test_group_service():
    """Compose GroupService from the canonical group-core implementation."""
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
        character_option_label_backend=group_character_option_label,
        resolve_character_backend=resolve_character_file,
        member_labels_backend=group_member_labels,
        current_speaker_backend=group_current_speaker,
        advance_turn_backend=advance_group_turn,
    )


class _TestGroupDirector:
    def plan(self, *_args, **_kwargs):
        return None

    def prompt_context(self, *_args, **_kwargs):
        return ""


def make_test_conversation_service():
    """Compose the canonical conversation collaborators explicitly for tests."""
    from bridge.command_routes import handle_command_route
    from bridge.conversation_service import ConversationService
    from bridge.message_commands import prepare_message, generate_and_store_reply

    return ConversationService(
        prepare_message=prepare_message,
        dispatch_command=handle_command_route,
        generate_reply=generate_and_store_reply,
    )


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
):
    """Return an explicit test-only application service graph for routers."""
    return SimpleNamespace(
        memory=memory or make_test_memory_service(),
        persona=persona or make_test_persona_service(),
        sync=sync or make_test_sync_service(),
        group=group or make_test_group_service(),
        group_director=group_director or _TestGroupDirector(),
        input_flow=input_flow or make_test_input_flow_service(),
        model_router=model_router or make_test_model_router(),
        provider=provider or make_test_provider_port(),
        conversation=conversation or make_test_conversation_service(),
        delivery=delivery or make_test_delivery_port(),
    )


def make_native_test_sync_service() -> SyncService:
    """Compose SyncService from the canonical collaborators used by startup."""
    from bridge.persona_sync import SillyTavernApiError
    from bridge.repositories import count_session_messages
    from bridge.sync_api import (
        _phase3_disable,
        phase3_api_configured,
        phase3_sync_now,
        phase3_sync_poll,
        phase3_toggle_realtime,
    )
    from bridge.sync_core import sync_binding

    return SyncService(
        load_binding=sync_binding,
        count_messages=count_session_messages,
        sync_now_backend=phase3_sync_now,
        toggle_realtime_backend=phase3_toggle_realtime,
        poll_backend=phase3_sync_poll,
        disable_realtime=_phase3_disable,
        api_configured=phase3_api_configured,
        expected_errors=(SillyTavernApiError, ValueError),
    )
