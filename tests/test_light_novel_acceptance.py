"""Acceptance regressions from the final feature review."""

import json
from dataclasses import replace

import pytest
from application_test_setup import make_test_application_services
from test_light_novel_flow import attached_choice, bridge_services
from test_light_novel_storage import novel_db as novel_db

from bridge import conversation_callbacks, greetings, update_message_routing
from bridge.conversation_lifecycle import conversation_state
from bridge.light_novel_repository import load_choice_set
from bridge.request_types import RequestContext


def test_failed_opening_can_be_delivered_by_a_new_callback_without_reinserting(novel_db, monkeypatch):
    db, session, settings = novel_db
    fields = {"name": "Alice", "first_mes": "Original opening"}
    monkeypatch.setattr(greetings, "send_text", lambda *a: (_ for _ in ()).throw(RuntimeError("network")))
    with pytest.raises(RuntimeError, match="network"):
        greetings.send_character_greeting(
            db, "token", "chat", fields, "story", "User", operation_id=41, app_settings=settings
        )
    assert conversation_state(db, "chat", "story").started
    services = make_test_application_services(app_settings=settings)
    sent = []
    monkeypatch.setattr(greetings, "send_text", lambda *a: sent.append(a[2]) or [71])
    monkeypatch.setattr(conversation_callbacks, "send_text", lambda *a: sent.append(a[2]) or [])
    monkeypatch.setattr(conversation_callbacks, "card_fields_from_file", lambda *a, **k: fields)
    monkeypatch.setattr(conversation_callbacks, "close_panel_message", lambda *a, **k: None)
    callback = {"id": "new-callback", "message": {"message_id": 80, "chat": {"id": "chat"}}}
    conversation_callbacks.handle_greeting_callback(
        db,
        "token",
        callback,
        lambda *a: None,
        "greeting:use:0:0",
        "chat",
        callback["message"],
        session,
        "story",
        42,
        persona_service=services.persona,
        request_context=RequestContext(db, "story", "owner", app_settings=settings),
    )
    assert sent == ["Original opening"]
    assert db.execute("SELECT content,telegram_message_ids FROM messages").fetchall() == [("Original opening", "[71]")]


@pytest.mark.parametrize("media", [{"voice": {"file_id": "v"}}, {"photo": [{"file_id": "p", "file_size": 12}]}])
def test_accepted_media_invalidates_the_older_choice_panel_before_worker_execution(novel_db, monkeypatch, media):
    record = attached_choice(novel_db)
    db, _session, settings = novel_db
    services = bridge_services(settings, [])
    monkeypatch.setattr(update_message_routing, "send_help_command", lambda *a, **k: False)
    monkeypatch.setattr(update_message_routing, "hide_choice_panels", lambda *a, **k: None)
    update_message_routing.route_message_update(
        services,
        db,
        {},
        {"chat": {"id": "chat"}, "from": {"id": "owner"}, "message_id": 40, **media},
        240,
        frozenset({"owner"}),
    )
    assert load_choice_set(db, record.nonce).state == "invalidated"
    assert db.execute("SELECT count(*) FROM jobs WHERE update_id=240").fetchone()[0] == 1


def test_choice_only_request_contains_actual_persona_world_and_system_context(novel_db, monkeypatch):
    from bridge import light_novel_jobs as workers
    from bridge.model_selection import set_task_model
    from bridge.persona_service import PersonaService
    from bridge.provider_port import ProviderPort
    from bridge.session_core import update_session
    from bridge.sqlite_store import db_connect

    record = attached_choice(novel_db, "b", ready=False)
    db, _session, settings = novel_db
    settings.native_persona_settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.native_persona_settings_file.write_text(
        json.dumps(
            {
                "power_user": {
                    "personas": {"pilot.png": "Pilot"},
                    "persona_descriptions": {"pilot.png": {"description": "A cautious captain who cannot swim."}},
                }
            }
        )
    )
    settings.world_dir.mkdir(parents=True, exist_ok=True)
    (settings.world_dir / "World.json").write_text(
        json.dumps(
            {
                "entries": {
                    "0": {
                        "constant": True,
                        "content": "Only the captain may enter the sealed tower.",
                        "order": 1,
                    }
                }
            }
        )
    )
    update_session(
        db,
        "chat",
        "story",
        persona_id="pilot.png",
        world_file="World.json",
        system_prompt="Respect the mystery setting.",
    )
    set_task_model(db, "chat", "story", "utility::test")
    calls = []

    def generate(*args, **kwargs):
        calls.append(args[2])
        return '{"choices":["Go inside","Stay outside"]}'

    services = bridge_services(settings, [])
    persona = replace(
        services.persona,
        load_personas=lambda: {"pilot.png": {"name": "Pilot", "description": "A cautious captain who cannot swim."}},
    )
    assert isinstance(persona, PersonaService)
    filename = db.execute("PRAGMA database_list").fetchone()[2]
    services = replace(
        services,
        persona=persona,
        provider=ProviderPort(generate),
        db_factory=lambda: db_connect(filename, app_settings=settings),
    )
    monkeypatch.setattr(workers, "card_fields_from_file", lambda *a, **k: {"name": "Alice"})
    monkeypatch.setattr(workers, "render_choices", lambda *a, **k: True)
    job_id = db.execute("SELECT job_id FROM jobs WHERE update_id=?", (-record.id,)).fetchone()[0]
    workers.process_light_novel_choices_job(services, "chat", record.nonce, False, job_id)
    prompt = "\n".join(message["content"] for message in calls[0])
    assert "A cautious captain who cannot swim." in prompt
    assert "Only the captain may enter the sealed tower." in prompt
    assert "Respect the mystery setting." in prompt


def test_choice_recovery_executes_the_normal_conversation_pipeline_exactly_once(novel_db, monkeypatch):
    from test_character_mutation_safety import _card_png

    from bridge import message_commands
    from bridge.job_service import DurableJob
    from bridge.light_novel_callbacks import route_light_novel_callback
    from bridge.light_novel_repository import bind_choice_panel, latest_choice_set
    from bridge.light_novel_service import prepare_turn
    from bridge.metadata import set_meta
    from bridge.provider_port import ProviderPort
    from bridge.response_delivery import persist_assistant_delivery_ids
    from bridge.session_core import update_session
    from bridge.sqlite_store import db_connect, write_transaction
    from bridge.worker_orchestration import resolve_recovered_job_submission

    record = attached_choice(novel_db, "a")
    db, _session, settings = novel_db
    settings.character_dir.mkdir(parents=True, exist_ok=True)
    (settings.character_dir / "Alice.png").write_bytes(_card_png("Alice", "A rainy afternoon"))
    update_session(db, "chat", "story", character_file="Alice.png")
    set_meta(db, "stream_mode:chat", "off")
    calls = []

    def generate(*args, **kwargs):
        calls.append(args[2])
        return '{"story":"You enter the hallway.","choices":["Call for help","Look upstairs"]}'

    port = ProviderPort(generate)
    app = make_test_application_services(app_settings=settings, provider=port)
    filename = db.execute("PRAGMA database_list").fetchone()[2]
    services = replace(
        bridge_services(settings, []),
        conversation=app.conversation,
        provider=port,
        db_factory=lambda: db_connect(filename, app_settings=settings),
    )
    monkeypatch.setattr(message_commands, "send_typing", lambda *a: None)
    monkeypatch.setattr(message_commands, "queue_user_quote_tts", lambda *a, **k: None)
    monkeypatch.setattr(
        message_commands,
        "send_reply",
        lambda token, chat, text, db, sid, rowid, **kw: persist_assistant_delivery_ids(db, rowid, [92]),
    )
    monkeypatch.setattr(
        message_commands,
        "prepare_turn",
        lambda db, chat, session, key, actor: prepare_turn(db, chat, session, key, actor, rng=lambda _: 2),
    )
    with write_transaction(db):
        bind_choice_panel(db, record.nonce, 81)
    callback = {"id": "pick", "message": {"message_id": 81}, "data": f"lnchoice:{record.nonce}:0"}
    route_light_novel_callback(services, db, callback, 600, "chat", "owner", message_worker=lambda *a: None)
    row = db.execute("SELECT job_id,payload_json FROM jobs WHERE update_id=600").fetchone()
    recovered = DurableJob(row[0], "chat", "story", -record.id, "generation", json.loads(row[1]))
    submission = resolve_recovered_job_submission(services, {}, recovered)
    submission.worker(*submission.args, recovered.job_id)
    submission.worker(*submission.args, recovered.job_id)
    assert len(calls) == 1
    assert db.execute("SELECT content,telegram_message_id FROM messages WHERE role='user'").fetchall() == [
        ("Go inside", str(-record.id))
    ]
    assert db.execute("SELECT content FROM messages WHERE role='assistant' ORDER BY rowid").fetchall() == [
        ("The door opens.",),
        ("You enter the hallway.",),
    ]
    current = latest_choice_set(db, "chat", "story")
    assert current.nonce != record.nonce and current.actor_id == "owner"
    assert current.choices == ("Call for help", "Look upstairs")
    assert db.execute("SELECT state FROM jobs WHERE job_id=?", (row[0],)).fetchone()[0] == "done"


def test_dedicated_lightnovel_help_and_strict_start_documentation_are_discoverable():
    from bridge.help_details import HELP_CATEGORIES, command_detail

    commands = {command for entries in HELP_CATEGORIES.values() for command, _summary in entries}
    assert "/lightnovel" in commands
    assert "2–4" in command_detail("/lightnovel", "")
    assert "/start" in command_detail("/reset", "")
    assert "already started" in command_detail("/start", "").lower()
