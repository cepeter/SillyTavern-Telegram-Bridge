import json

import pytest
from test_light_novel_storage import novel_db as novel_db

from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


def started(db):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started

    configure_conversation(db, "chat", "story", "lightnovel", "b")
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)


def story_row(db, text="The door opens."):
    with write_transaction(db):
        return db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','story','assistant',?,1)",
            (text,),
        ).lastrowid


@pytest.mark.parametrize("count", [2, 3, 4])
def test_requested_count_sampled_once_and_preserved(novel_db, count):
    from bridge.light_novel_service import prepare_turn

    db, session, _ = novel_db
    started(db)
    seen = []
    record = prepare_turn(db, "chat", session, "message:4", "owner", rng=lambda choices: seen.append(choices) or count)
    second = prepare_turn(db, "chat", session, "message:4", "owner", rng=lambda _: pytest.fail("resampled"))
    assert second.requested_count == record.requested_count == count
    assert seen == [(2, 3, 4)]


def test_normal_turn_does_not_create_choice_records(novel_db):
    from bridge.light_novel_service import prepare_turn

    db, session, _ = novel_db
    assert prepare_turn(db, "chat", session, "message:4") is None
    assert db.execute("SELECT count(*) FROM light_novel_choice_sets").fetchone()[0] == 0


def test_inline_parser_keeps_story_when_choice_tail_is_invalid():
    from bridge.light_novel_format import parse_story_response

    for source in [
        '{"story":"The door opens.","choices":broken}',
        '{"story":"The door opens."}',
        '{"story":"The door opens.","choices":["Go"]}',
    ]:
        story, choices = parse_story_response(source, 3)
        assert story == "The door opens."
        assert choices is None


def test_inline_parser_never_leaks_envelope():
    from bridge.light_novel_format import parse_story_response

    assert parse_story_response('```json\n{"story":"Hello","choices":["Go", "Stay"]}\n```', 2) == (
        "Hello",
        ["Go", "Stay"],
    )
    with pytest.raises(ValueError):
        parse_story_response('{"choices":["Go","Stay"]}', 2)


@pytest.mark.parametrize(
    "values", [["/reset", "Stay"], ["@bot /reset", "Stay"], ["Go", " go "], ["Go"], ["x" * 161, "Stay"], [True, "Stay"]]
)
def test_unsafe_or_invalid_choices_are_rejected(values):
    from bridge.light_novel_format import validate_choices

    with pytest.raises(ValueError):
        validate_choices(values, 2)


@pytest.mark.parametrize("strategy,expected", [("a", "story::test"), ("b", "utility::test"), ("c", "story::test")])
def test_choice_only_strategy_routes_correct_model_outside_transaction(novel_db, strategy, expected):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
    from bridge.model_selection import set_task_model

    db, session, settings = novel_db
    configure_conversation(db, "chat", "story", "lightnovel", strategy)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    set_task_model(db, "chat", "story", "utility::test")
    record = prepare_turn(db, "chat", session, "opening:1", "owner", rng=lambda _: 2)
    rowid = story_row(db)
    attach_turn(db, record, rowid, "The door opens.")
    calls = []

    def generate(key, model, messages, **kwargs):
        assert not db.in_transaction
        calls.append((model, messages, kwargs))
        return json.dumps({"choices": ["Go inside", "Wait outside"]})

    result = ensure_choices(
        db, record.nonce, session, {"name": "Alice"}, provider_port=ProviderPort(generate), app_settings=settings
    )
    assert result.choices == ("Go inside", "Wait outside")
    assert calls[0][0] == expected
    assert calls[0][2]["request_timeout"] == 30
    assert calls[0][2]["force_non_stream"]
    ensure_choices(
        db,
        record.nonce,
        session,
        {},
        provider_port=ProviderPort(lambda *a, **k: pytest.fail("already ready")),
        app_settings=settings,
    )


def test_failed_choices_retry_does_not_change_story_or_count(novel_db):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn", "owner", rng=lambda _: 3)
    attach_turn(db, record, story_row(db), "The door opens.")
    bad = ProviderPort(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider error")))
    result = ensure_choices(db, record.nonce, session, {}, provider_port=bad, app_settings=settings)
    assert result.generation_status == "failed"
    good = ProviderPort(lambda *a, **k: '{"choices":["Go", "Stay", "Ask"]}')
    result = ensure_choices(db, record.nonce, session, {}, provider_port=good, app_settings=settings, retry=True)
    assert result.requested_count == 3
    assert len(result.choices) == 3
    assert db.execute("SELECT content FROM messages").fetchall() == [("The door opens.",)]


def test_inline_ready_choices_do_not_call_another_model(novel_db):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.", ["Go", "Stay"])
    result = ensure_choices(
        db,
        record.nonce,
        session,
        {},
        provider_port=ProviderPort(lambda *a, **k: pytest.fail("extra call")),
        app_settings=settings,
    )
    assert result.generation_status == "ready"


def test_reset_during_provider_call_cannot_publish_old_choices(novel_db):
    from bridge.conversation_lifecycle import reset_conversation
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")

    def generate(*a, **k):
        reset_conversation(db, "chat", "story")
        return '{"choices":["Go", "Stay"]}'

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.state == "invalidated"
    assert not result.choices
