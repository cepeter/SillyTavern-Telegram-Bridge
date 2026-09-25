"""Live Sync retains via the configured memory port, including its provider binding."""

from __future__ import annotations

from dataclasses import replace

from bridge.main import _build_startup_services
from bridge.model_router import ModelRouter
from bridge.settings import load_app_settings


def test_startup_sync_callbacks_bind_the_application_memory_service(tmp_path):
    settings = load_app_settings({}, home=tmp_path)
    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    for callback in (services.sync.sync_now_backend, services.sync.poll_backend, services.sync.toggle_realtime_backend):
        retain = callback.keywords.get("retain_memory")
        assert retain is not None, "Live Sync must capture its configured memory retention port"
        assert retain.__self__ is services.memory


def test_snapshot_adapter_retains_through_the_explicit_port(tmp_path):
    import bridge.sync_core as core

    calls = []
    settings = load_app_settings({}, home=tmp_path)
    session = {"session_id": "session", "character_file": "character.png"}
    fields = {"name": "Character"}

    def retain(db, chat_id, current, card):
        calls.append((db, chat_id, current, card))

    adapter = core._make_sync_snapshot_integrity(app_settings=settings, retain_memory=retain)
    adapter = replace(
        adapter,
        apply_backend=lambda *_args: "hash",
        load_session=lambda *_args: session,
        card_fields=lambda _file: fields,
    )
    db = object()
    assert adapter.apply(db, "chat", session, {}, [("user", "remote")], {}) == "hash"
    assert calls == [(db, "chat", session, fields)]
