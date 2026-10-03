"""Review regressions use real transcripts/jobs and intercept external boundaries."""

from types import SimpleNamespace

import pytest
from application_test_setup import (
    make_test_delivery_port,
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
    make_test_session_service,
)
from settings_test_support import make_test_settings
from test_npc_branch_safety import _fields, _session

from bridge import (
    document_jobs,
    image_messages,
    job_store,
    message_commands,
    native_imports,
    response_delivery,
    telegram,
    voice_jobs,
    worker_orchestration,
)
from bridge.conversation_lifecycle import mark_started
from bridge.job_service import JobService
from bridge.metadata import set_meta
from bridge.npc_service import NpcService
from bridge.session_naming import create_session
from bridge.sqlite_store import db_connect


@pytest.fixture
def case(tmp_path, monkeypatch):
    config = make_test_settings(home=tmp_path, db_file=tmp_path / "review.sqlite3")
    db = db_connect(app_settings=config)
    create_session(db, "chat", "model", session_id="s1", app_settings=config)
    mark_started(db, "chat", "s1", 0)
    set_meta(db, "stream_mode:chat", "off")
    effects = []
    notices = []
    offline = [True]
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("external transport forbidden"))

    def request(_token, method, payload):
        assert not db.in_transaction
        if method == "sendMessage":
            if offline[0]:
                raise TimeoutError("synthetic transport timeout")
            effects.append("send")
            return {"message_id": 900 + len(effects)}
        return {}

    monkeypatch.setattr(telegram, "telegram_request", request)
    monkeypatch.setattr(response_delivery, "telegram_request", request)
    monkeypatch.setattr(message_commands, "send_typing", lambda *a: None)
    monkeypatch.setattr(image_messages, "send_typing", lambda *a: None)
    monkeypatch.setattr(message_commands, "queue_user_quote_tts", lambda *a, **k: None)
    provider = make_test_provider_port(generate_backend=lambda *a, **k: effects.append("provider") or "saved answer")
    group = make_test_group_service(app_settings=config)
    memory = make_test_memory_service()
    persona = make_test_persona_service()
    rag = make_test_rag_service()
    npc = NpcService()
    jobs = JobService(
        job_store.enqueue_job,
        job_store.store_job_payload,
        job_store.job_actor_id,
        job_store.mark_job_scheduled,
        job_store.mark_job_running,
        job_store.finish_job,
        job_store.recover_jobs,
        lambda *a: True,
        delivery_retry_backend=job_store.retry_delivery_job,
    )

    def conversation(conn, token, key, model, fields, chat, text, mid, **kw):
        message_commands.generate_and_store_reply(
            conn,
            token,
            key,
            fields,
            chat,
            text,
            _session(),
            "s1",
            model,
            None,
            "",
            mid,
            kw.get("operation_id"),
            group_service=group,
            provider_port=provider,
            memory_service=memory,
            npc_service=npc,
            persona_service=persona,
            app_settings=config,
            rag_service=rag,
        )

    def download(*a):
        effects.append("download")
        return b"synthetic image or voice"

    services = SimpleNamespace(
        config=config,
        jobs=jobs,
        db_factory=lambda: db_connect(app_settings=config),
        provider=provider,
        group=group,
        memory=memory,
        persona=persona,
        rag=rag,
        npc=npc,
        group_director=SimpleNamespace(prompt_context=lambda *a: ""),
        session=make_test_session_service(app_settings=config),
        conversation=SimpleNamespace(process_message=conversation),
        telegram=SimpleNamespace(send_text=lambda *a: notices.append(a[-1]), request=request, download_file=download),
    )
    monkeypatch.setattr(worker_orchestration, "card_fields_from_file", lambda *a, **k: _fields())
    monkeypatch.setattr(native_imports, "card_fields_from_file", lambda *a, **k: _fields())
    monkeypatch.setattr(native_imports, "download_telegram_file", download)
    monkeypatch.setattr(voice_jobs, "download_telegram_file", download)
    monkeypatch.setattr(voice_jobs, "transcribe_audio_bytes", lambda *a, **k: effects.append("transcribe") or "spoken")
    yield SimpleNamespace(db=db, config=config, services=services, effects=effects, notices=notices, offline=offline)
    db.close()


def invoke(c, kind, jid):
    if kind == "generation":
        worker_orchestration.process_message_job(c.services, _fields(), "chat", "prompt", 77, [], "s1", None, jid)
    elif kind == "image":
        worker_orchestration.process_image_job(c.services, "chat", "file", "caption", 10, 77, "s1", None, jid)
    elif kind == "voice":
        voice_jobs.process_voice_job(c.services, _fields(), "chat", {"file_id": "file"}, 77, "s1", None, jid)
    else:
        document_jobs.process_document_job(
            c.services,
            "chat",
            {"file_id": "file", "file_name": "image.png"},
            77,
            "s1",
            None,
            job_id=jid,
        )


@pytest.mark.parametrize("kind", ["generation", "image", "voice", "document"])
@pytest.mark.parametrize(
    "mutation", ["delete", "replace", "changed_source", "changed_user", "changed_payload", "valid"]
)
def test_committed_turn_recovery_never_repeats_generation_or_media_effects(case, kind, mutation):
    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, kind, {"actor_id": "100"})
    invoke(c, kind, jid)
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == ("queued", 1)
    assert c.effects.count("provider") == 1
    old_effects = list(c.effects)
    original = c.db.execute("SELECT rowid,role,content FROM messages ORDER BY rowid").fetchall()
    if mutation in {"delete", "replace"}:
        c.db.execute("DELETE FROM messages")
        if mutation == "replace":
            for _, role, text in original:
                c.db.execute(
                    "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
                    "VALUES('chat','s1',?,?,?,1)",
                    (role, text, "77" if role == "user" else None),
                )
    elif mutation == "changed_source":
        c.db.execute("UPDATE messages SET content='replaced' WHERE role='assistant'")
    elif mutation == "changed_user":
        c.db.execute("UPDATE messages SET content='new prompt' WHERE role='user'")
    elif mutation == "changed_payload":
        c.db.execute("UPDATE assistant_delivery_progress SET payload='other'")
    c.db.commit()
    after_mutation = c.db.execute("SELECT rowid,role,content FROM messages ORDER BY rowid").fetchall()
    c.offline[0] = False
    invoke(c, kind, jid)
    assert c.effects == old_effects + (["send"] if mutation == "valid" else [])
    assert c.db.execute("SELECT rowid,role,content FROM messages ORDER BY rowid").fetchall() == after_mutation
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == (
        "done" if mutation == "valid" else "failed",
        2,
    )
    if mutation != "valid":
        assert "deleted or replaced" in c.notices[-1]


@pytest.mark.parametrize("attempts", [2, 3])
def test_restart_and_claim_enforce_committed_delivery_attempt_budget(case, attempts):
    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    c.db.execute("UPDATE jobs SET state='running',attempts=?", (attempts,))
    c.db.commit()
    recovered = job_store.recover_jobs(c.db)
    if attempts == 3:
        assert recovered == []
        assert not job_store.mark_job_running(c.db, jid)
        assert c.db.execute("SELECT state,attempts,last_error FROM jobs").fetchone()[:2] == ("failed", 3)
        assert "/retry" in c.db.execute("SELECT last_error FROM jobs").fetchone()[0]
    else:
        assert len(recovered) == 1
        c.offline[0] = False
        invoke(c, "generation", jid)
        assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == ("done", 3)
        assert c.effects.count("provider") == 1


@pytest.mark.parametrize("stage", ["lookup", "preparation"])
def test_manual_retry_expiry_after_selection_reports_cancellation(case, monkeypatch, stage):
    from bridge import delivery_progress, delivery_recovery

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    for _ in range(3):
        invoke(c, "generation", jid)

    def erase():
        c.db.execute("DELETE FROM messages")
        c.db.commit()

    if stage == "lookup":
        lookup = delivery_recovery.failed_delivery_target

        def selected(*a):
            target = lookup(*a)
            erase()
            return target

        monkeypatch.setattr(delivery_recovery, "failed_delivery_target", selected)
    else:
        prepare = delivery_progress.prepare_progress

        def preparing(*a, **kw):
            erase()
            return prepare(*a, **kw)

        monkeypatch.setattr(response_delivery, "prepare_progress", preparing)
    port = make_test_delivery_port(send_text=lambda *a: c.notices.append(a[-1]))
    assert delivery_recovery.retry_failed_delivery(c.db, "t", "chat", "s1", "100", port, app_settings=c.config)
    assert "cancelled" in c.notices[-1]
    assert "available" not in c.notices[-1]


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("callback_route", [False, True])
def test_manual_exhausted_greeting_retains_no_expression_or_tts_contract(
    case, monkeypatch, group, partial, callback_route
):
    from bridge import conversation_callbacks, delivery_recovery, greetings

    c = case
    from bridge import group_core
    from bridge.conversation_lifecycle import lifecycle_key

    set_meta(c.db, lifecycle_key("started", "chat", "s1"), "0")
    set_meta(c.db, "voice_mode:chat", "tts")
    if group:
        group_core.save_group_state(c.db, "chat", "s1", {"enabled": True, "members": ["a", "b"]})
    extra_effects = []
    monkeypatch.setattr(response_delivery, "deliver_expression", lambda *a, **k: extra_effects.append("expression"))
    monkeypatch.setattr(response_delivery, "submit_background", lambda *a, **k: extra_effects.append("tts") or True)
    ack = []

    def request(_t, method, payload):
        if method == "sendMessage":
            if c.offline[0] and (not partial or ack):
                raise TimeoutError("synthetic timeout")
            ack.append(800 + len(ack))
            return {"message_id": ack[-1]}
        return {}

    monkeypatch.setattr(telegram, "telegram_request", request)
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "callback", {"actor_id": "100"})
    for _ in range(3):
        assert job_store.mark_job_running(c.db, jid)
        try:
            fields = dict(_fields(), first_mes='"opening"' + "A" * 8100)
            if callback_route:
                from bridge.request_types import RequestContext

                set_meta(c.db, "active_session:chat", "s1")
                monkeypatch.setattr(
                    conversation_callbacks, "card_fields_from_file", lambda *a, fields=fields, **k: fields
                )
                conversation_callbacks.handle_greeting_callback(
                    c.db,
                    "t",
                    {"id": "callback"},
                    lambda *a, **k: None,
                    "greeting:use:0:0",
                    "chat",
                    {"message_id": 88},
                    _session(),
                    "s1",
                    jid,
                    persona_service=c.services.persona,
                    request_context=RequestContext(c.db, "s1", "100", app_settings=c.config),
                )
            else:
                greetings.send_character_greeting(
                    c.db,
                    "t",
                    "chat",
                    fields,
                    "s1",
                    "User",
                    operation_id=jid,
                    app_settings=c.config,
                )
        except response_delivery.DeliveryFailure as exc:
            delivery_recovery.handle_delivery_failure(c.services, c.db, "chat", jid, exc)
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == ("failed", 3)
    assert c.db.execute("SELECT kind FROM operations").fetchone() == (
        "start_greeting" if callback_route else "greeting",
    )
    seen = []
    real_send = delivery_recovery.send_reply

    def send(*a, **kw):
        seen.append(a[4])
        return real_send(*a, **kw)

    monkeypatch.setattr(delivery_recovery, "send_reply", send)
    c.offline[0] = False
    assert delivery_recovery.retry_failed_delivery(
        c.db,
        "t",
        "chat",
        "s1",
        "100",
        make_test_delivery_port(),
        app_settings=c.config,
    )
    assert seen == [None]
    assert extra_effects == []
    assert len(ack) == 3
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("done",)


def test_manual_committed_tombstone_cannot_fall_through_to_failed_turn_generation(case, monkeypatch):
    from bridge import command_routes
    from bridge.failed_turns import record_failed_turn
    from bridge.request_types import RequestContext

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    c.db.execute("DELETE FROM messages")
    c.db.commit()
    invoke(c, "generation", jid)
    record_failed_turn(c.db, "chat", 77, "prompt", "model", "old failure", "s1")
    monkeypatch.setattr(command_routes, "send_text", lambda *a: c.notices.append(a[-1]))
    assert command_routes._handle_basic(
        c.db,
        "t",
        "key",
        "model",
        _fields(),
        "chat",
        "/retry",
        "/retry",
        _session(),
        "s1",
        "model",
        "",
        "User",
        99,
        request_context=RequestContext(c.db, "s1", "100", app_settings=c.config),
        conversation_service=c.services.conversation,
        delivery_port=make_test_delivery_port(send_text=lambda *a: c.notices.append(a[-1])),
        group_service=c.services.group,
        memory_service=c.services.memory,
        provider_port=c.services.provider,
    )
    assert c.effects.count("provider") == 1
    assert c.db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
    assert "cancelled" in c.notices[-1]


@pytest.mark.parametrize("association", ["unique", "multiple_jobs", "multiple_sources", "missing_input"])
@pytest.mark.parametrize("nullable", [False, True])
def test_v9_backfill_preserves_exact_bindings_or_cancels_ambiguous_attempted_jobs(association, nullable):
    import sqlite3

    from bridge import schema
    from bridge.migrations import run_migrations
    from bridge.turn_delivery_repository import turn_delivery_target

    db = sqlite3.connect(":memory:")
    run_migrations(db, schema.SCHEMA_MIGRATIONS[:8])
    first = job_store.enqueue_job(db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    if nullable:
        db.execute("UPDATE jobs SET telegram_message_id=NULL WHERE job_id=?", (first,))
    db.execute("UPDATE jobs SET state='running',attempts=1 WHERE job_id=?", (first,))
    db.commit()

    def turn(text="prompt", answer="answer"):
        user = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
            "VALUES('chat','s1','user',?,?,1)",
            (text, None if nullable else "77"),
        ).lastrowid
        row = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','assistant',?,2)",
            (answer,),
        ).lastrowid
        db.execute(
            "INSERT INTO assistant_delivery_progress(assistant_rowid,source_content,payload,message_ids) "
            "VALUES(?,?,?,'[800]')",
            (row, answer, answer),
        )
        return user, row

    user, row = turn()
    db.commit()
    expected_jobs = [first]
    if association == "multiple_jobs":
        duplicate = job_store.enqueue_job(db, 2, "chat", "s1", 77, "voice", {"actor_id": "100"})
        db.execute(
            "UPDATE jobs SET attempts=1,telegram_message_id=? WHERE job_id=?", (None if nullable else "77", duplicate)
        )
        db.commit()
        expected_jobs.append(duplicate)
    if association == "multiple_sources":
        turn()
    if association == "missing_input":
        db.execute("DELETE FROM messages WHERE rowid=?", (user,))
    db.commit()
    unattempted = job_store.enqueue_job(db, 3, "chat", "s1", 88, "generation", {})
    other_session = job_store.enqueue_job(db, 4, "chat", "s2", 88, "generation", {})
    other_chat = job_store.enqueue_job(db, 5, "elsewhere", "s1", 88, "generation", {})
    db.execute("UPDATE jobs SET state='running',attempts=1 WHERE job_id IN (?,?)", (other_session, other_chat))
    db.commit()
    schema.initialize_database_schema(db)
    schema.initialize_database_schema(db)
    if association == "unique":
        assert turn_delivery_target(db, first) == ("chat", "s1", row, "answer", 1, "")
    else:
        for jid in expected_jobs:
            target = turn_delivery_target(db, jid)
            assert target is not None and not target[4]
            assert target[2] is None and "ambiguous" in target[5]
        from bridge.delivery_recovery import retry_failed_delivery

        notices = []
        job_store.finish_job(db, first, "failed", "ambiguous historical intent")
        assert retry_failed_delivery(
            db,
            "t",
            "chat",
            "s1",
            "100",
            make_test_delivery_port(send_text=lambda *a: notices.append(a[-1])),
            app_settings=make_test_settings(),
        )
        assert "associated safely" in notices[-1] and "cancelled" in notices[-1]
        assert "deleted" not in notices[-1] and "available" not in notices[-1]
    for jid in [unattempted, other_session, other_chat]:
        assert turn_delivery_target(db, jid) is None
    db.close()


def test_v9_migration_failure_rolls_back_binding_schema_and_history():
    import sqlite3

    from bridge import schema
    from bridge.delivery_schema import migrate_job_delivery_intents
    from bridge.migrations import Migration, MigrationError, run_migrations

    db = sqlite3.connect(":memory:")
    run_migrations(db, schema.SCHEMA_MIGRATIONS[:8])
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','assistant','keep',1)"
    )
    db.commit()
    before = db.execute("SELECT * FROM messages").fetchall()

    def interrupted(conn):
        migrate_job_delivery_intents(conn)
        raise RuntimeError("injected after binding backfill")

    with pytest.raises(MigrationError):
        run_migrations(db, (*schema.SCHEMA_MIGRATIONS[:8], Migration(9, "job_delivery_intents", interrupted)))
    assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (8,)
    assert db.execute("SELECT name FROM sqlite_master WHERE name='job_delivery_intents'").fetchone() is None
    assert db.execute("SELECT * FROM messages").fetchall() == before
    db.close()


@pytest.mark.parametrize("kind", ["generation", "image", "voice", "document"])
def test_first_attempt_commits_binding_and_completes_regular_delivery(case, kind):
    c = case
    c.offline[0] = False
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, kind, {"actor_id": "100"})
    invoke(c, kind, jid)
    assert c.effects.count("provider") == c.effects.count("send") == 1
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == ("done", 1)
    assert c.db.execute("SELECT assistant_rowid FROM job_delivery_intents").fetchone() is not None
    assert c.db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (1,)


@pytest.mark.parametrize("kind", ["generation", "image", "voice", "document"])
def test_exhausted_regular_delivery_manual_retry_preserves_job_actor_session_payload(case, kind):
    from bridge.delivery_recovery import retry_failed_delivery

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, kind, {"actor_id": "100"})
    for _ in range(3):
        invoke(c, kind, jid)
    intent = c.db.execute("SELECT * FROM job_delivery_intents").fetchone()
    payload = c.db.execute("SELECT payload_json FROM jobs").fetchone()
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == ("failed", 3)
    c.offline[0] = False
    assert retry_failed_delivery(c.db, "t", "chat", "s1", "100", make_test_delivery_port(), app_settings=c.config)
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == ("done", 3)
    assert c.db.execute("SELECT * FROM job_delivery_intents").fetchone() == intent
    assert c.db.execute("SELECT payload_json FROM jobs").fetchone() == payload
    assert c.effects.count("provider") == 1 and c.effects.count("send") == 1


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("kind", ["edit", "regen", "continue", "greeting", "start_greeting", "legacy_delivery"])
def test_attempt_cap_is_specific_to_committed_delivery_even_without_restart(case, restart, committed, kind):
    from bridge.operations import begin_operation, set_operation_phase

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "edit", {})
    if committed:
        begin_operation(c.db, jid, kind)
        set_operation_phase(c.db, jid, kind, "local_committed")
        if kind == "legacy_delivery":
            c.db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "VALUES('chat','s1','assistant','saved',1)"
            )
            set_meta(
                c.db,
                f"operation_payload:{jid}",
                '{"assistant_rowid":1,"source_content":"saved","delivery_payload":"saved"}',
            )
    c.db.execute("UPDATE jobs SET state=?,attempts=3", ("running" if restart else "queued",))
    c.db.commit()
    if restart:
        job_store.recover_jobs(c.db)
    assert job_store.mark_job_running(c.db, jid) is (not committed)
    assert c.db.execute("SELECT state,attempts FROM jobs").fetchone() == (
        ("failed", 3) if committed else ("running", 4)
    )


def test_reply_and_delivery_intent_commit_or_roll_back_together(case):
    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    c.db.execute(
        "CREATE TRIGGER reject_intent BEFORE INSERT ON job_delivery_intents "
        "BEGIN SELECT RAISE(ABORT,'injected binding failure'); END"
    )
    c.db.commit()
    invoke(c, "generation", jid)
    assert c.db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
    assert c.db.execute("SELECT COUNT(*) FROM job_delivery_intents").fetchone() == (0,)
    assert c.db.execute("SELECT COUNT(*) FROM assistant_delivery_progress").fetchone() == (0,)
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("failed",)


def test_committed_binding_is_immutable_and_deleted_only_with_job(case):
    from bridge.delivery_progress import bind_committed_turn
    from bridge.sqlite_store import write_transaction

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    before = c.db.execute("SELECT * FROM job_delivery_intents").fetchone()
    with pytest.raises(ValueError), write_transaction(c.db):
        bind_committed_turn(c.db, jid, before[1], before[3], "replacement payload")
    assert c.db.execute("SELECT * FROM job_delivery_intents").fetchone() == before
    c.db.execute("DELETE FROM messages")
    c.db.commit()
    assert c.db.execute("SELECT * FROM job_delivery_intents").fetchone() == before
    c.db.execute("DELETE FROM jobs")
    c.db.commit()
    assert c.db.execute("SELECT * FROM job_delivery_intents").fetchone() is None


@pytest.mark.parametrize("queued_input", [None, "0", "77"])
def test_nullable_generation_input_binds_exact_committed_user_identity(case, queued_input):
    from bridge.delivery_recovery import resume_committed_turn

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    c.db.execute("UPDATE jobs SET telegram_message_id=?", (queued_input,))
    c.db.commit()
    assert job_store.mark_job_running(c.db, jid)
    with pytest.raises(response_delivery.DeliveryFailure):
        c.services.conversation.process_message(
            c.db,
            "t",
            "key",
            "model",
            _fields(),
            "chat",
            "synthetic prompt",
            None,
            operation_id=jid,
        )
    rows = c.db.execute("SELECT rowid,role,telegram_message_id FROM messages ORDER BY rowid").fetchall()
    assert len(rows) == 2 and rows[0][2] is None
    c.offline[0] = False
    assert resume_committed_turn(c.services, c.db, jid)
    assert c.effects.count("provider") == 1 and c.effects.count("send") == 1
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("done",)


def test_source_removed_before_first_checkpoint_is_expired_not_retryable(case, monkeypatch):
    from bridge.delivery_progress import DeliveryTargetExpired

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    rowid = c.db.execute("SELECT assistant_rowid FROM job_delivery_intents").fetchone()[0]

    def request(_t, method, payload):
        c.db.execute("DELETE FROM messages WHERE rowid=?", (rowid,))
        c.db.commit()
        return {"message_id": 800}

    monkeypatch.setattr(telegram, "telegram_request", request)
    with pytest.raises(DeliveryTargetExpired):
        response_delivery.send_reply("t", "chat", "saved answer", c.db, "s1", rowid, app_settings=c.config)


def test_unbound_legacy_retry_requires_an_explicit_delivery_checkpoint(case):
    from bridge.delivery_retry_repository import failed_delivery_target

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    c.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
        "VALUES('chat','s1','user','legacy','77',1)"
    )
    c.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
        "VALUES('chat','s1','assistant','legacy answer',2)"
    )
    c.db.commit()
    job_store.finish_job(c.db, jid, "failed", "delivery incomplete: legacy")
    assert failed_delivery_target(c.db, "chat", "s1") is None


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("mutation", ["assistant", "user", "payload"])
def test_validated_original_intent_cannot_rebind_at_preparation(case, monkeypatch, manual, mutation):
    from bridge import delivery_progress, delivery_recovery

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    for _ in range(3 if manual else 1):
        invoke(c, "generation", jid)
    before_intent = c.db.execute("SELECT * FROM job_delivery_intents").fetchone()
    before_effects = list(c.effects)
    prepared = response_delivery.prepare_progress
    replacement = []

    def mutate_then_prepare(*a, **kw):
        if mutation == "payload":
            c.db.execute("UPDATE assistant_delivery_progress SET payload='replacement payload'")
        else:
            c.db.execute("UPDATE messages SET content=? WHERE role=?", ("replacement " + mutation, mutation))
        c.db.commit()
        replacement.append(c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall())
        return prepared(*a, **kw)

    monkeypatch.setattr(response_delivery, "prepare_progress", mutate_then_prepare)
    c.offline[0] = False
    if manual:
        assert delivery_recovery.retry_failed_delivery(
            c.db,
            "t",
            "chat",
            "s1",
            "100",
            make_test_delivery_port(send_text=lambda *a: c.notices.append(a[-1])),
            app_settings=c.config,
        )
    else:
        invoke(c, "generation", jid)
    assert c.effects == before_effects
    assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == replacement[0]
    assert c.db.execute("SELECT * FROM job_delivery_intents").fetchone() == before_intent
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("failed",)
    assert "cancelled" in c.notices[-1]
    assert not delivery_progress.delivery_complete(c.db, before_intent[3])


def test_direct_preparation_rejects_changed_source_without_overwriting_checkpoint(case):
    from bridge.delivery_progress import DeliveryTargetExpired, prepare_progress

    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    rowid = c.db.execute("SELECT assistant_rowid FROM job_delivery_intents").fetchone()[0]
    c.db.execute("UPDATE messages SET content='replacement answer' WHERE rowid=?", (rowid,))
    c.db.commit()
    progress = c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall()
    with pytest.raises(DeliveryTargetExpired):
        prepare_progress(c.db, rowid, "saved answer")
    assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == progress


@pytest.mark.parametrize("mutation", ["user", "payload"])
def test_manual_operation_target_is_validated_atomically_at_preparation(case, monkeypatch, mutation):
    from bridge import delivery_recovery, edit_messages

    c = case
    c.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
        "VALUES('chat','s1','user','original','77',1)"
    )
    c.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
        "VALUES('chat','s1','assistant','original answer',2)"
    )
    c.db.commit()
    monkeypatch.setattr(edit_messages, "card_fields_from_file", lambda *a, **k: _fields())
    monkeypatch.setattr(edit_messages, "send_typing", lambda *a: None)
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "edit", {"actor_id": "100"})
    for _ in range(3):
        worker_orchestration.process_edit_job(c.services, "chat", 77, "edited", job_id=jid)
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("failed",)
    prior = list(c.effects)
    prepared = response_delivery.prepare_progress
    replaced = []

    def replace(*a, **kw):
        if mutation == "user":
            c.db.execute("UPDATE messages SET content='another prompt' WHERE role='user'")
        else:
            c.db.execute("UPDATE assistant_delivery_progress SET payload='another payload'")
        c.db.commit()
        replaced.append(c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall())
        return prepared(*a, **kw)

    monkeypatch.setattr(response_delivery, "prepare_progress", replace)
    c.offline[0] = False
    assert delivery_recovery.retry_failed_delivery(
        c.db,
        "t",
        "chat",
        "s1",
        "100",
        make_test_delivery_port(send_text=lambda *a: c.notices.append(a[-1])),
        app_settings=c.config,
    )
    assert c.effects == prior
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("failed",)
    assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == replaced[0]
    assert "cancelled" in c.notices[-1]


@pytest.mark.parametrize("mutation", ["user", "payload", "rebound_assistant"])
def test_acknowledgment_cas_cannot_complete_a_rebound_target(case, monkeypatch, mutation):
    c = case
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    effects = list(c.effects)
    preserved = []

    def request(_t, method, payload):
        if mutation == "user":
            c.db.execute("UPDATE messages SET content='new prompt' WHERE role='user'")
        elif mutation == "payload":
            c.db.execute("UPDATE assistant_delivery_progress SET payload='replacement payload'")
        else:
            c.db.execute("UPDATE messages SET content='replacement answer' WHERE role='assistant'")
            c.db.execute("UPDATE assistant_delivery_progress SET source_content='replacement answer'")
        c.db.commit()
        preserved.append(c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall())
        return {"message_id": 800}

    monkeypatch.setattr(telegram, "telegram_request", request)
    c.offline[0] = False
    invoke(c, "generation", jid)
    assert c.effects == effects
    assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == preserved[0]
    assert c.db.execute("SELECT state FROM jobs").fetchone() == ("failed",)
    assert "cancelled" in c.notices[-1]


def _actual_callback_opening(c, monkeypatch, *, partial=False, lightnovel=False, actor="100"):
    from functools import partial as bind

    from application_test_setup import make_test_input_flow_service, make_test_sync_service

    from bridge import callback_dispatch, callbacks, conversation_callbacks
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, lifecycle_key
    from bridge.panel_bindings import bind_panel_session

    set_meta(c.db, lifecycle_key("started", "chat", "s1"), "0")
    set_meta(c.db, "active_session:chat", "s1")
    set_meta(c.db, "voice_mode:chat", "tts")
    if lightnovel:
        configure_conversation(c.db, "chat", "s1", "lightnovel", "b")
    fields = dict(_fields(), first_mes='"opening"' + "A" * 8100)
    monkeypatch.setattr(conversation_callbacks, "card_fields_from_file", lambda *a, **k: fields)
    monkeypatch.setattr(callback_dispatch, "send_text", lambda *a: c.notices.append(a[-1]))
    c.services.delivery = make_test_delivery_port(
        send_text=lambda *a: c.notices.append(a[-1]),
        send_reply=bind(response_delivery.send_reply, app_settings=c.config),
    )
    c.services.sync = make_test_sync_service()
    c.services.input_flow = make_test_input_flow_service(app_settings=c.config)
    bind_panel_session(c.db, "chat", 88, "s1", "100")
    extras, sends = [], []
    monkeypatch.setattr(response_delivery, "deliver_expression", lambda *a, **k: extras.append("expression"))
    monkeypatch.setattr(response_delivery, "submit_background", lambda *a, **k: extras.append("tts") or True)

    def request(_t, method, payload):
        assert not c.db.in_transaction
        if method == "sendMessage":
            if c.offline[0] and (not partial or sends):
                raise TimeoutError("synthetic opening timeout")
            sends.append(dict(payload))
            return {"message_id": 800 + len(sends)}
        return {}

    monkeypatch.setattr(telegram, "telegram_request", request)
    monkeypatch.setattr(callbacks, "telegram_request", request)
    epoch = conversation_state(c.db, "chat", "s1").epoch
    callback = {
        "id": "opening-callback",
        "from": {"id": actor},
        "message": {"chat": {"id": "chat"}, "message_id": 88},
        "data": f"greeting:use:0:{epoch}",
        "_queued": True,
    }
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 88, "callback", {"actor_id": actor})
    worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    return jid, callback, sends, extras


@pytest.mark.parametrize("view_change", ["expired_panel", "active_session"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("target", ["valid", "deleted", "source_changed", "payload_changed"])
def test_callback_worker_recovers_original_delivery_before_view_checks(case, monkeypatch, view_change, partial, target):
    import json

    c = case
    jid, callback, sends, extras = _actual_callback_opening(c, monkeypatch, partial=partial)
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("queued", 1)
    assert c.db.execute("SELECT state,kind FROM operations").fetchone() == ("local_committed", "start_greeting")
    rowid, original_payload, ids, complete = c.db.execute(
        "SELECT assistant_rowid,payload,message_ids,complete FROM assistant_delivery_progress"
    ).fetchone()
    assert complete == 0 and len(json.loads(ids)) == int(partial)
    snapshot = c.db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (jid,)).fetchone()
    if view_change == "expired_panel":
        c.db.execute("UPDATE panel_sessions SET expires_at=0")
        c.db.commit()
    else:
        create_session(c.db, "chat", "model", session_id="other", app_settings=c.config)
        set_meta(c.db, "active_session:chat", "other")
    if target == "deleted":
        c.db.execute("DELETE FROM messages WHERE rowid=?", (rowid,))
    elif target == "source_changed":
        c.db.execute("UPDATE messages SET content='replacement' WHERE rowid=?", (rowid,))
    elif target == "payload_changed":
        c.db.execute("UPDATE assistant_delivery_progress SET payload='replacement'")
    c.db.commit()
    before_sends = list(sends)
    before_progress = c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall()
    c.offline[0] = False
    worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    assert c.db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (jid,)).fetchone() == snapshot
    assert extras == [] and c.effects == []
    if target == "valid":
        assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("done", 2)
        assert c.db.execute("SELECT state,kind FROM operations").fetchone() == ("applied", "start_greeting")
        assert c.db.execute("SELECT payload,complete FROM assistant_delivery_progress").fetchone() == (
            original_payload,
            1,
        )
        assert len(sends) == 3
        expected_visible = "".join(chunk[0] for chunk in response_delivery._roleplay_reply_chunks(original_payload))
        assert "".join(item["text"] for item in sends) == expected_visible
        assert c.db.execute("SELECT session_id FROM messages WHERE rowid=?", (rowid,)).fetchone() == ("s1",)
    else:
        assert sends == before_sends
        assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == before_progress
        assert c.db.execute("SELECT state FROM operations").fetchone() == ("local_committed",)
        assert c.db.execute("SELECT state FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("failed",)
        assert "cancelled" in c.notices[-1]


def test_callback_worker_first_attempt_keeps_panel_actor_authorization(case, monkeypatch):
    c = case
    c.offline[0] = False
    jid, _, sends, extras = _actual_callback_opening(c, monkeypatch, actor="intruder")
    assert sends == extras == []
    assert c.db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
    assert "another user" in c.notices[-1]
    assert c.db.execute("SELECT state FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("done",)


@pytest.mark.parametrize("owner", ["queued", "exhausted", "legacy_unowned"])
@pytest.mark.parametrize("target", ["valid", "deleted", "source_changed", "payload_changed"])
@pytest.mark.parametrize("partial", [False, True])
def test_choice_worker_leaves_pending_delivery_to_original_owner(case, monkeypatch, owner, target, partial):
    from bridge import light_novel_jobs

    c = case
    jid, callback, sends, extras = _actual_callback_opening(c, monkeypatch, partial=partial, lightnovel=True)
    if owner == "exhausted":
        worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
        worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    rowid = c.db.execute("SELECT assistant_rowid FROM assistant_delivery_progress").fetchone()[0]
    nonce = c.db.execute("SELECT nonce FROM light_novel_choice_sets").fetchone()[0]
    choice_jid = c.db.execute("SELECT job_id FROM jobs WHERE kind='novel_choices'").fetchone()[0]
    if owner == "legacy_unowned":
        c.db.execute("DELETE FROM meta WHERE key=?", (f"operation_payload:{jid}",))
        c.db.execute("DELETE FROM operations WHERE operation_id=?", (str(jid),))
    if target == "deleted":
        c.db.execute("DELETE FROM messages WHERE rowid=?", (rowid,))
    elif target == "source_changed":
        c.db.execute("UPDATE messages SET content='replacement' WHERE rowid=?", (rowid,))
    elif target == "payload_changed":
        c.db.execute("UPDATE assistant_delivery_progress SET payload='replacement'")
    c.db.commit()
    before_job = c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone()
    before_sends = list(sends)
    before_progress = c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall()
    monkeypatch.setattr(light_novel_jobs, "card_fields_from_file", lambda *a, **k: _fields())
    monkeypatch.setattr(
        light_novel_jobs, "ensure_choices", lambda db, nonce, *a, **k: light_novel_jobs.load_choice_set(db, nonce)
    )
    monkeypatch.setattr(light_novel_jobs, "render_choices", lambda *a, **k: None)
    c.offline[0] = False
    light_novel_jobs.process_light_novel_choices_job(c.services, "chat", nonce, job_id=choice_jid)
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == before_job
    if owner == "legacy_unowned" and target == "valid":
        assert len(sends) == 3
        assert c.db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (1,)
    elif owner == "legacy_unowned" and target == "payload_changed":
        # Legacy unowned delivery uses its existing explicit checkpoint; no guessed owner identity.
        assert c.db.execute("SELECT payload,complete FROM assistant_delivery_progress").fetchone() == ("replacement", 1)
    else:
        assert sends == before_sends and extras == []
        assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == before_progress
    assert c.db.execute("SELECT state FROM jobs WHERE job_id=?", (choice_jid,)).fetchone() == ("done",)


@pytest.mark.parametrize("partial", [False, True])
def test_callback_view_expiry_preserves_attempt_cap_and_manual_recovery(case, monkeypatch, partial):
    from bridge.delivery_recovery import retry_failed_delivery

    c = case
    jid, callback, sends, extras = _actual_callback_opening(c, monkeypatch, partial=partial)
    c.db.execute("UPDATE panel_sessions SET expires_at=0")
    c.db.commit()
    worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("failed", 3)
    assert c.db.execute("SELECT state FROM operations").fetchone() == ("local_committed",)
    assert c.db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (0,)
    c.offline[0] = False
    assert retry_failed_delivery(c.db, "t", "chat", "s1", "100", c.services.delivery, app_settings=c.config)
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("done", 3)
    assert c.db.execute("SELECT state,kind FROM operations").fetchone() == ("applied", "start_greeting")
    assert c.db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (1,)
    assert len(sends) == 3 and extras == []


@pytest.mark.parametrize("mutation", ["valid", "user", "payload"])
def test_choice_worker_does_not_bypass_original_narrative_intent(case, monkeypatch, mutation):
    from bridge import light_novel_jobs
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, lifecycle_key

    c = case
    set_meta(c.db, lifecycle_key("started", "chat", "s1"), "0")
    configure_conversation(c.db, "chat", "s1", "lightnovel", "b")
    mark_started(c.db, "chat", "s1", conversation_state(c.db, "chat", "s1").epoch)
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
    invoke(c, "generation", jid)
    assert c.db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (0,)
    nonce = c.db.execute("SELECT nonce FROM light_novel_choice_sets").fetchone()[0]
    choice_jid = c.db.execute("SELECT job_id FROM jobs WHERE kind='novel_choices'").fetchone()[0]
    if mutation == "user":
        c.db.execute("UPDATE messages SET content='replacement input' WHERE role='user'")
    elif mutation == "payload":
        c.db.execute("UPDATE assistant_delivery_progress SET payload='replacement payload'")
    c.db.commit()
    before = c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall()
    effects = list(c.effects)
    from functools import partial as bind

    c.services.delivery = make_test_delivery_port(send_reply=bind(response_delivery.send_reply, app_settings=c.config))
    monkeypatch.setattr(light_novel_jobs, "card_fields_from_file", lambda *a, **k: _fields())
    monkeypatch.setattr(
        light_novel_jobs, "ensure_choices", lambda db, nonce, *a, **k: light_novel_jobs.load_choice_set(db, nonce)
    )
    monkeypatch.setattr(light_novel_jobs, "render_choices", lambda *a, **k: None)
    c.offline[0] = False
    light_novel_jobs.process_light_novel_choices_job(c.services, "chat", nonce, job_id=choice_jid)
    assert c.effects == effects
    assert c.db.execute("SELECT * FROM assistant_delivery_progress").fetchall() == before
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("queued", 1)
    assert c.db.execute("SELECT state FROM jobs WHERE job_id=?", (choice_jid,)).fetchone() == ("done",)


@pytest.mark.parametrize("attempts", [0, 3])
@pytest.mark.parametrize("restart", [False, True])
def test_callback_worker_committed_reset_stays_with_reset_owner(case, monkeypatch, attempts, restart):
    from application_test_setup import make_test_input_flow_service, make_test_sync_service

    from bridge import callbacks, conversation_callbacks
    from bridge.conversation_lifecycle import lifecycle_key
    from bridge.operations import begin_operation, set_operation_phase
    from bridge.panel_bindings import bind_panel_session

    c = case
    c.services.delivery = make_test_delivery_port()
    c.services.sync = make_test_sync_service()
    c.services.input_flow = make_test_input_flow_service(app_settings=c.config)
    c.services.memory = make_test_memory_service(purge_session_memory=lambda *a: pytest.fail("reset replayed purge"))
    set_meta(c.db, "active_session:chat", "s1")
    set_meta(c.db, lifecycle_key("started", "chat", "s1"), "0")
    bind_panel_session(c.db, "chat", 88, "s1", "100")
    monkeypatch.setattr(callbacks, "telegram_request", lambda *a: {})
    monkeypatch.setattr(conversation_callbacks, "send_text", lambda *a: c.notices.append(a[-1]))
    callback = {
        "id": "reset-callback",
        "from": {"id": "100"},
        "message": {"chat": {"id": "chat"}, "message_id": 88},
        "data": "reset:confirm",
        "_queued": True,
    }
    jid = job_store.enqueue_job(c.db, 1, "chat", "s1", 88, "callback", {"actor_id": "100"})
    # This is reset's valid durable crash boundary after clearing its transcript.
    begin_operation(c.db, jid, "reset")
    set_operation_phase(c.db, jid, "reset", "local_committed")
    c.db.execute("UPDATE jobs SET state=?,attempts=?", ("running" if restart else "queued", attempts))
    c.db.commit()
    if restart:
        assert len(job_store.recover_jobs(c.db)) == 1
    assert c.db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
    worker_orchestration.process_callback_job(c.services, "chat", callback, jid)
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("done", attempts + 1)
    assert c.db.execute("SELECT state FROM operations").fetchone() == ("applied",)
    assert c.db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
    assert c.db.execute("SELECT COUNT(*) FROM assistant_delivery_progress").fetchone() == (0,)
    assert c.effects == []
    assert c.notices == ["Reset complete. The active session was cleared."]


def test_callback_worker_fresh_greeting_still_commits_and_delivers(case, monkeypatch):
    c = case
    c.offline[0] = False
    jid, _, sends, extras = _actual_callback_opening(c, monkeypatch)
    assert c.db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (jid,)).fetchone() == ("done", 1)
    assert c.db.execute("SELECT state FROM operations").fetchone() == ("applied",)
    assert c.db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (1,)
    assert len(sends) == 3 and extras == [] and c.effects == []
