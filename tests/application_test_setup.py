"""One-time explicit application extension setup for tests.

This helper exists only because both unittest discovery and pytest import test
modules independently. It performs no patch propagation, module mutation, or
dependency binding.
"""
from __future__ import annotations

from types import SimpleNamespace

from bridge.application_composition import initialize_extensions
from bridge.composition import RequestContext
from bridge.memory_service import MemoryService
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
    from bridge.input_flows import PERSONA_EDIT_LOCK
    from bridge.persona_sync import (
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


class _TestGroupDirector:
    def plan(self, *_args, **_kwargs):
        return None

    def prompt_context(self, *_args, **_kwargs):
        return ""


def make_test_application_services(
    *,
    memory=None,
    persona=None,
    sync=None,
    group_director=None,
):
    """Return an explicit test-only application service graph for routers."""
    return SimpleNamespace(
        memory=memory or make_test_memory_service(),
        persona=persona or make_test_persona_service(),
        sync=sync or make_test_sync_service(),
        group_director=group_director or _TestGroupDirector(),
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
