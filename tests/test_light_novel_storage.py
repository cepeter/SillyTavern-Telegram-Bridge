import pytest
from settings_test_support import make_test_settings

from bridge.session_core import create_session
from bridge.sqlite_store import db_connect, write_transaction


@pytest.fixture
def novel_db(tmp_path):
    settings = make_test_settings(home=tmp_path)
    db = db_connect(tmp_path / "novel.sqlite3", app_settings=settings)
    session = create_session(db, "chat", "story::test", session_id="story", app_settings=settings)
    yield db, session, settings
    db.close()


def test_new_session_lifecycle_is_explicit_unstarted(novel_db):
    from bridge.conversation_lifecycle import conversation_state

    db, session, _ = novel_db
    state = conversation_state(db, "chat", session["session_id"])
    assert (state.mode, state.strategy, state.started, state.epoch) == ("normal", "", False, 0)
    assert db.execute("SELECT value FROM meta WHERE key='conversation_started:chat:story'").fetchone() == ("0",)


def test_reset_keeps_configuration_and_invalidates_epoch(novel_db):
    from bridge.conversation_lifecycle import (
        configure_conversation,
        conversation_state,
        mark_started,
        reset_conversation,
    )

    db, session, _ = novel_db
    configure_conversation(db, "chat", session["session_id"], "lightnovel", "b")
    before = conversation_state(db, "chat", "story")
    mark_started(db, "chat", "story", before.epoch)
    reset_conversation(db, "chat", "story")
    after = conversation_state(db, "chat", "story")
    assert (after.mode, after.strategy, after.started) == ("lightnovel", "b", False)
    assert after.epoch == before.epoch + 1


def test_lifecycle_joins_outer_transaction(novel_db):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state

    db, _, _ = novel_db
    with pytest.raises(RuntimeError), write_transaction(db):
        configure_conversation(db, "chat", "story", "lightnovel", "c")
        raise RuntimeError("rollback")
    assert conversation_state(db, "chat", "story").mode == "normal"


def test_initial_backfill_does_not_recompute_lifecycle(novel_db):
    from bridge.conversation_lifecycle import conversation_state
    from bridge.conversation_schema import migrate_conversation_modes

    db, _, _ = novel_db
    with write_transaction(db):
        db.execute("DELETE FROM meta WHERE key='conversation_started:chat:story'")
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat'"
            ",'story','assistant','hello',1)"
        )
        migrate_conversation_modes(db)
    assert conversation_state(db, "chat", "story").started
    with write_transaction(db):
        db.execute("DELETE FROM messages")
        migrate_conversation_modes(db)
    assert conversation_state(db, "chat", "story").started


def reserve(db, key="turn1", count=3):
    from bridge.light_novel_repository import reserve_choice_set

    with write_transaction(db):
        return reserve_choice_set(db, "chat", "story", 0, key, "b", count, "owner", "story::test", 1.0)


def test_choice_reservation_preserves_count_and_nonce(novel_db):
    db, _, _ = novel_db
    first = reserve(db)
    duplicate = reserve(db, count=4)
    assert first.id == duplicate.id
    assert first.nonce == duplicate.nonce
    assert duplicate.requested_count == 3
    assert not duplicate.choices
    assert not db.in_transaction


def test_choice_consumption_is_single_use_and_scoped(novel_db):
    from bridge.light_novel_repository import attach_choice_set, bind_choice_panel, consume_choice_set

    db, _, _ = novel_db
    record = reserve(db)
    with write_transaction(db):
        attach_choice_set(db, record.nonce, 1, "digest", ["Go", "Stay", "Ask"])
        bind_choice_panel(db, record.nonce, 81)
    for chat, actor, epoch, panel in [
        ("other", "owner", 0, 81),
        ("chat", "thief", 0, 81),
        ("chat", "owner", 1, 81),
        ("chat", "owner", 0, 82),
    ]:
        with pytest.raises(ValueError), write_transaction(db):
            consume_choice_set(db, record.nonce, 0, chat, "story", actor, epoch, panel)
    with write_transaction(db):
        picked = consume_choice_set(db, record.nonce, 1, "chat", "story", "owner", 0, 81)
    assert picked.choices[picked.selected_index] == "Stay"
    with pytest.raises(ValueError, match="already used"), write_transaction(db):
        consume_choice_set(db, record.nonce, 2, "chat", "story", "owner", 0, 81)


def test_reset_invalidates_pending_and_ready_choices(novel_db):
    from bridge.conversation_lifecycle import reset_conversation
    from bridge.light_novel_repository import load_choice_set

    db, _, _ = novel_db
    record = reserve(db)
    reset_conversation(db, "chat", "story")
    assert load_choice_set(db, record.nonce).state == "invalidated"


def test_session_deletion_removes_choices_and_lifecycle(novel_db):
    db, _, _ = novel_db
    record = reserve(db)
    with write_transaction(db):
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='story'")
    assert db.execute("SELECT id FROM light_novel_choice_sets WHERE id=?", (record.id,)).fetchone() is None
    assert db.execute("SELECT value FROM meta WHERE key='conversation_started:chat:story'").fetchone() is None


def test_choice_repo_requires_caller_transaction(novel_db):
    from bridge.light_novel_repository import reserve_choice_set

    db, _, _ = novel_db
    with pytest.raises(RuntimeError):
        reserve_choice_set(db, "chat", "story", 0, "x", "a", 2, "owner", "model", 1)
