import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from application_test_setup import make_test_application_services
from test_light_novel_storage import novel_db as novel_db

from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started, reset_conversation
from bridge.light_novel_repository import load_choice_set
from bridge.light_novel_service import attach_turn, prepare_turn
from bridge.main import _build_startup_services
from bridge.model_router import ModelRouter
from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


def make_started(novel_db, strategy="a"):
    db, session, settings = novel_db
    configure_conversation(db, "chat", "story", "lightnovel", strategy)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    return db, session, settings


def attached_choice(novel_db, strategy="b", ready=True, choices=None):
    db, session, _settings = make_started(novel_db, strategy)
    record = prepare_turn(db, "chat", session, "turn:1", "owner", rng=lambda _: 2)
    with write_transaction(db):
        rowid = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,crea"
            "ted_at) VALUES('chat','story','assistant','The door opens.','[71]',1)"
        ).lastrowid
        attach_turn(db, record, rowid, "The door opens.", (choices or ["Go inside", "Stay outside"]) if ready else None)
    return load_choice_set(db, record.nonce)


def bridge_services(settings, sent):
    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    return replace(
        services,
        background=replace(services.background, submit_chat=lambda *a, **k: False),
        jobs=replace(services.jobs, submit_chat=lambda *a, **k: False, prepare_worker=None),
        telegram=replace(
            services.telegram,
            request=lambda token, method, payload=None: sent.append((method, payload)) or {"message_id": 81},
            send_text=lambda *a: sent.append(("text", a[2])) or [82],
        ),
    )


def test_story_commit_atomically_schedules_choice_recovery(novel_db):
    record = attached_choice(novel_db)
    db, _, _ = novel_db
    job = db.execute("SELECT update_id,kind,payload_json FROM jobs WHERE update_id=?", (-record.id,)).fetchone()
    assert job[0:2] == (-record.id, "novel_choices")
    assert json.loads(job[2])["nonce"] == record.nonce


def test_story_and_choice_job_roll_back_together(novel_db):
    db, session, _settings = make_started(novel_db)
    record = prepare_turn(db, "chat", session, "new-turn", "owner", rng=lambda _: 2)
    with pytest.raises(RuntimeError), write_transaction(db):
        rowid = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat'"
            ",'story','assistant','Hello',1)"
        ).lastrowid
        attach_turn(db, record, rowid, "Hello", ["Go", "Stay"])
        raise RuntimeError("crash")
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    assert load_choice_set(db, record.nonce).assistant_rowid is None


def test_choice_panels_show_full_text_in_body_with_numbered_selector_buttons(novel_db, monkeypatch):
    from bridge import light_novel_panels as panels

    choices = [
        "Walk toward the abandoned station & investigate the sound coming from inside.",
        "Stay hidden behind the wall & watch the strangers before deciding.",
    ]
    record = attached_choice(novel_db, choices=choices)
    db, _, settings = novel_db
    sent = []
    monkeypatch.setattr(
        panels, "send_panel_request", lambda token, method, payload, **kw: sent.append(payload) or {"message_id": 81}
    )
    panels.render_choices(db, "token", record, app_settings=settings)
    payload = sent[-1]
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == (
        "What will you do?\n\n"
        "<b>1.</b> Walk toward the abandoned station &amp; investigate the sound coming from inside.\n\n"
        "<b>2.</b> Stay hidden behind the wall &amp; watch the strangers before deciding.\n\n"
        "You may also type your own reply."
    )
    rows = payload["reply_markup"]["inline_keyboard"]
    assert rows == [
        [
            {"text": "1", "callback_data": f"lnchoice:{record.nonce}:0"},
            {"text": "2", "callback_data": f"lnchoice:{record.nonce}:1"},
        ],
        [{"text": "⏭ Next Scene", "callback_data": f"lnnext:{record.nonce}"}],
    ]
    assert all(choice not in button["text"] for row in rows for button in row for choice in choices)
    assert load_choice_set(db, record.nonce).panel_message_id == 81


def test_next_scene_consumes_panel_and_queues_fixed_narrative_instruction_once(novel_db):
    from bridge.light_novel_callbacks import route_light_novel_callback
    from bridge.light_novel_repository import bind_choice_panel

    record = attached_choice(novel_db)
    db, _session, settings = novel_db
    sent = []
    services = bridge_services(settings, sent)
    with write_transaction(db):
        bind_choice_panel(db, record.nonce, 81)
    callback = {
        "id": "cb-next",
        "from": {"id": "owner"},
        "message": {"chat": {"id": "chat"}, "message_id": 81},
        "data": f"lnnext:{record.nonce}",
    }
    assert route_light_novel_callback(services, db, callback, 102, "chat", "owner", message_worker=lambda *a: None)
    consumed = load_choice_set(db, record.nonce)
    assert consumed.state == "consumed"
    assert consumed.selected_index is None
    job = db.execute("SELECT kind,session_id,payload_json FROM jobs WHERE update_id=102").fetchone()
    assert job[:2] == ("generation", "story")
    payload = json.loads(job[2])
    assert payload["text"] == (
        "Advance to the next scene without speaking, deciding, or acting for the user character. "
        "Continue the narrative until the user character can meaningfully participate again."
    )
    assert payload["actor_id"] == "owner"
    assert payload["choice_nonce"] == record.nonce
    assert route_light_novel_callback(services, db, callback, 103, "chat", "owner", message_worker=lambda *a: None)
    assert db.execute("SELECT count(*) FROM jobs WHERE kind='generation'").fetchone()[0] == 1
    edits = [payload for method, payload in sent if method == "editMessageText"]
    assert edits[-1]["text"] == "<blockquote>⏭ Next Scene</blockquote>"
    assert edits[-1]["parse_mode"] == "HTML"
    assert "Selected:" not in edits[-1]["text"]


def test_choice_click_consumes_and_queues_stored_user_text_once(novel_db):
    from bridge.light_novel_callbacks import route_light_novel_callback
    from bridge.light_novel_repository import bind_choice_panel

    record = attached_choice(novel_db, choices=["Go inside", "Stay & wait"])
    db, _session, settings = novel_db
    sent = []
    services = bridge_services(settings, sent)
    with write_transaction(db):
        bind_choice_panel(db, record.nonce, 81)
    callback = {
        "id": "cb",
        "from": {"id": "owner"},
        "message": {"chat": {"id": "chat"}, "message_id": 81},
        "data": f"lnchoice:{record.nonce}:1",
    }
    assert route_light_novel_callback(services, db, callback, 100, "chat", "owner", message_worker=lambda *a: None)
    picked = load_choice_set(db, record.nonce)
    assert picked.state == "consumed"
    job = db.execute("SELECT kind,telegram_message_id,session_id,payload_json FROM jobs WHERE update_id=100").fetchone()
    assert job[:3] == ("generation", str(-record.id), "story")
    payload = json.loads(job[3])
    assert payload["text"] == "Stay & wait"
    assert payload["actor_id"] == "owner"
    assert payload["epoch"] == record.epoch
    assert payload["resolve_active"] is False
    edits = [payload for method, payload in sent if method == "editMessageText"]
    assert edits[-1]["text"] == "<blockquote>Stay &amp; wait</blockquote>"
    assert edits[-1]["parse_mode"] == "HTML"
    assert "Selected:" not in edits[-1]["text"]
    assert route_light_novel_callback(services, db, callback, 101, "chat", "owner", message_worker=lambda *a: None)
    assert db.execute("SELECT count(*) FROM jobs WHERE kind='generation'").fetchone()[0] == 1


@pytest.mark.parametrize("bad", ["actor", "session", "epoch", "panel", "index"])
def test_choice_scope_rejected_without_generation_job(novel_db, bad):
    from bridge.light_novel_callbacks import route_light_novel_callback
    from bridge.light_novel_repository import bind_choice_panel
    from bridge.metadata import set_meta

    record = attached_choice(novel_db)
    db, _session, settings = novel_db
    services = bridge_services(settings, [])
    with write_transaction(db):
        bind_choice_panel(db, record.nonce, 81)
    if bad == "session":
        set_meta(db, "active_session:chat", "other")
    if bad == "epoch":
        reset_conversation(db, "chat", "story")
    callback = {
        "id": "cb",
        "data": f"lnchoice:{record.nonce}:{9 if bad == 'index' else 0}",
        "message": {"message_id": 82 if bad == "panel" else 81},
    }
    route_light_novel_callback(
        services, db, callback, 101, "chat", "thief" if bad == "actor" else "owner", message_worker=lambda *a: None
    )
    assert db.execute("SELECT count(*) FROM jobs WHERE kind='generation'").fetchone()[0] == 0


def test_retry_choices_enqueues_only_choice_job_with_unchanged_count(novel_db):
    from bridge.light_novel_callbacks import route_light_novel_callback
    from bridge.light_novel_repository import bind_choice_panel, fail_choice_generation

    record = attached_choice(novel_db, ready=False)
    db, _session, settings = novel_db
    services = bridge_services(settings, [])
    with write_transaction(db):
        bind_choice_panel(db, record.nonce, 81)
        fail_choice_generation(db, record.nonce)
        db.execute("UPDATE jobs SET state='done'")
    callback = {"id": "cb", "data": f"lnretry:{record.nonce}", "message": {"message_id": 81}}
    route_light_novel_callback(services, db, callback, 105, "chat", "owner", message_worker=lambda *a: None)
    route_light_novel_callback(services, db, callback, 106, "chat", "owner", message_worker=lambda *a: None)
    rows = db.execute("SELECT kind,payload_json FROM jobs WHERE state='queued'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "novel_choices"
    assert json.loads(rows[0][1])["retry"] is True
    assert load_choice_set(db, record.nonce).requested_count == 2
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_opening_light_novel_commit_creates_first_choice_job(novel_db, monkeypatch):
    from bridge import greetings

    db, _session, settings = novel_db
    configure_conversation(db, "chat", "story", "lightnovel", "a")
    monkeypatch.setattr(greetings, "send_text", lambda *a: [71])
    assert greetings.send_character_greeting(
        db,
        "token",
        "chat",
        {"name": "Alice", "first_mes": "The rain falls."},
        "story",
        "User",
        operation_id=41,
        actor_id="owner",
        app_settings=settings,
    )
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    row = db.execute("SELECT requested_count,actor_id,strategy FROM light_novel_choice_sets").fetchone()
    assert row[0] in (2, 3, 4) and row[1:] == ("owner", "a")
    assert db.execute("SELECT count(*) FROM jobs WHERE kind='novel_choices'").fetchone()[0] == 1


@pytest.mark.parametrize("strategy", ["a", "b", "c"])
def test_generation_attaches_choices_without_leaking_protocol(novel_db, monkeypatch, strategy):
    from bridge import message_commands

    db, session, settings = make_started(novel_db, strategy)
    session["_actor_id"] = "owner"
    session["response_language"] = "auto"
    session["humanizer"] = "off"
    raw = '{"story":"The scene moves.","choices":["Go", "Stay"]}' if strategy == "a" else "The scene moves."
    provider_calls = []

    def generate(*a, **k):
        provider_calls.append((a, k))
        return raw

    port = ProviderPort(generate)
    services = make_test_application_services(app_settings=settings, provider=port)
    monkeypatch.setattr(
        message_commands,
        "prepare_turn",
        lambda db, chat, sess, key, actor: prepare_turn(db, chat, sess, key, actor, rng=lambda _: 2),
    )
    monkeypatch.setattr(message_commands, "build_chat_messages", lambda *a, **k: [{"role": "user", "content": "Go"}])
    monkeypatch.setattr(message_commands, "send_typing", lambda *a: None)
    monkeypatch.setattr(message_commands, "telegram_request", lambda *a, **k: {"message_id": 70})
    monkeypatch.setattr(message_commands, "queue_user_quote_tts", lambda *a, **k: None)
    monkeypatch.setattr(message_commands, "send_reply", lambda *a, **k: None)
    message_commands.generate_and_store_reply(
        db,
        "token",
        "key",
        {"name": "Alice"},
        "chat",
        "Go",
        session,
        "story",
        "story::test",
        None,
        "",
        20,
        None,
        group_service=services.group,
        provider_port=port,
        memory_service=services.memory,
        persona_service=services.persona,
        app_settings=settings,
        rag_service=services.rag,
    )
    stored = db.execute("SELECT content FROM messages WHERE role='assistant'").fetchone()[0]
    assert stored == "The scene moves."
    assert len(provider_calls) == 1
    if strategy == "a":
        assert provider_calls[0][1]["stream_callback"] is None
    record = db.execute("SELECT generation_status,choices_json FROM light_novel_choice_sets").fetchone()
    assert record[0] == ("ready" if strategy == "a" else "pending")
    assert db.execute("SELECT count(*) FROM jobs WHERE kind='novel_choices'").fetchone()[0] == 1


def test_turn_wrapper_attaches_combined_continuation_and_fences_old_panel(novel_db):
    from bridge.light_novel_turn import begin_novel_turn

    db, session, _settings = make_started(novel_db, "a")
    session["_actor_id"] = "owner"
    turn = begin_novel_turn(db, "chat", session, "continue", 55)
    count = turn.record.requested_count
    actions = ["Look around", "Stay still", "Call out", "Leave"][:count]
    assert turn.extract(json.dumps({"story": "More narrative.", "choices": actions})) == "More narrative."
    with write_transaction(db):
        rowid = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat'"
            ",'story','assistant','First. More narrative.',1)"
        ).lastrowid
        turn.commit(db, rowid, "First. More narrative.")
    from bridge.light_novel_service import current_choice_story

    assert current_choice_story(db, load_choice_set(db, turn.record.nonce)) == "First. More narrative."
    assert load_choice_set(db, turn.record.nonce).choices == tuple(actions)


def test_missing_inline_choices_marks_retry_without_automatic_extra_call(novel_db):
    from bridge.light_novel_turn import begin_novel_turn

    db, session, _settings = make_started(novel_db, "a")
    turn = begin_novel_turn(db, "chat", session, "message", 22)
    assert turn.extract('{"story":"The rain falls.","choices":[]}') == "The rain falls."
    with write_transaction(db):
        rowid = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat'"
            ",'story','assistant','The rain falls.',1)"
        ).lastrowid
        turn.commit(db, rowid, "The rain falls.")
    assert load_choice_set(db, turn.record.nonce).generation_status == "failed"


def test_manual_reply_invalidates_choices_and_pins_generation_job(novel_db, monkeypatch):
    from bridge import update_message_routing

    record = attached_choice(novel_db)
    db, _session, settings = novel_db
    services = bridge_services(settings, [])
    monkeypatch.setattr(update_message_routing, "send_help_command", lambda *a, **k: False)
    monkeypatch.setattr(update_message_routing, "hide_choice_panels", lambda *a, **k: None)
    update_message_routing.route_message_update(
        services,
        db,
        {},
        {"chat": {"id": "chat"}, "from": {"id": "owner"}, "message_id": 20, "text": "I choose something else"},
        20,
        frozenset({"owner"}),
    )
    assert load_choice_set(db, record.nonce).state == "invalidated"
    payload = json.loads(db.execute("SELECT payload_json FROM jobs WHERE update_id=20").fetchone()[0])
    assert payload["epoch"] == record.epoch and payload["resolve_active"] is False


def test_durable_recovery_resolves_choices_to_choice_only_worker(novel_db):
    from bridge.job_service import DurableJob
    from bridge.light_novel_jobs import process_light_novel_choices_job
    from bridge.worker_orchestration import resolve_recovered_job_submission

    record = attached_choice(novel_db)
    _db, _session, settings = novel_db
    services = bridge_services(settings, [])
    job = DurableJob(-record.id, "chat", "story", 0, "novel_choices", {"nonce": record.nonce, "retry": True})
    submission = resolve_recovered_job_submission(services, {}, job)
    assert submission.worker is process_light_novel_choices_job
    assert submission.args[1:] == ("chat", record.nonce, True)


def test_choice_worker_does_not_regenerate_ready_inline_story(novel_db, monkeypatch):
    from bridge import light_novel_jobs as workers
    from bridge.sqlite_store import db_connect

    record = attached_choice(novel_db, "a")
    db, _session, settings = novel_db
    filename = db.execute("PRAGMA database_list").fetchone()[2]
    services = bridge_services(settings, [])
    services = replace(
        services,
        db_factory=lambda: db_connect(filename, app_settings=settings),
        provider=ProviderPort(lambda *a, **k: pytest.fail("ready choices must not call model")),
    )
    panels = []
    monkeypatch.setattr(workers, "card_fields_from_file", lambda *a, **k: {"name": "Alice"})
    monkeypatch.setattr(workers, "render_choices", lambda db, token, record, **k: panels.append(record))
    job_id = db.execute("SELECT job_id FROM jobs WHERE update_id=?", (-record.id,)).fetchone()[0]
    workers.process_light_novel_choices_job(services, "chat", record.nonce, False, job_id)
    assert panels[-1].choices == ("Go inside", "Stay outside")
    assert db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0] == "done"
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_dedicated_mode_panel_is_not_in_settings(novel_db, monkeypatch):
    from bridge import light_novel_panels as panels
    from bridge.request_types import RequestContext

    db, session, settings = novel_db
    sent = []
    monkeypatch.setattr(
        panels, "send_panel_request", lambda token, method, payload, **k: sent.append(payload) or {"message_id": 81}
    )
    panels.send_light_novel_menu(
        "token", "chat", session, request_context=RequestContext(db, "story", "owner", app_settings=settings)
    )
    buttons = [b for row in sent[-1]["reply_markup"]["inline_keyboard"] for b in row]
    assert len([b for b in buttons if b["callback_data"].startswith("novelmode:")]) == 4


def test_queued_choice_from_before_reset_never_enters_new_story(novel_db):
    from bridge.job_store import enqueue_job
    from bridge.sqlite_store import db_connect
    from bridge.worker_orchestration import process_message_job

    db, _session, settings = make_started(novel_db)
    filename = db.execute("PRAGMA database_list").fetchone()[2]
    epoch = conversation_state(db, "chat", "story").epoch
    job_id = enqueue_job(
        db,
        207,
        "chat",
        "story",
        -7,
        "generation",
        {"text": "Go", "actor_id": "owner", "model": "story::test", "epoch": epoch},
    )
    reset_conversation(db, "chat", "story")
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    services = bridge_services(settings, [])
    conversation = Mock()
    services = replace(
        services, db_factory=lambda: db_connect(filename, app_settings=settings), conversation=conversation
    )
    process_message_job(services, {}, "chat", "Go", -7, None, "story", "story::test", job_id)
    conversation.process_message.assert_not_called()
    assert db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0] == "done"
