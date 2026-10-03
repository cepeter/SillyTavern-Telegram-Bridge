import sqlite3
import time

from application_test_setup import (
    make_test_delivery_port,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
    make_test_request_context,
)
from persisted_state_test_support import find_test_npc
from settings_test_support import SettingsBuilder

import bridge.conversation_callbacks as conversation_callbacks
import bridge.edit_messages as edit_messages
import bridge.message_commands as message_commands
import bridge.regeneration as regeneration
from bridge.metadata import set_meta
from bridge.npc_repository import load_npc_fields, set_npc_extraction_coverage
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.response_variants import keep_swipe_variant, save_response_variant, swipe_state_key
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    initialize_database_schema(db)
    now = time.time()
    db.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("chat", "s1", "Session", "mira.png", "story::main", "", "", "", "", "auto", now, now),
    )
    db.commit()
    return db


def _session():
    return {
        "chat_id": "chat",
        "session_id": "s1",
        "character_file": "mira.png",
        "model_id": "story::main",
        "persona_id": "",
        "world_file": "",
        "author_note": "",
        "system_prompt": "",
        "response_language": "auto",
    }


def _fields():
    return {
        "name": "Mira",
        "system_prompt": "",
        "description": "",
        "personality": "",
        "scenario": "",
        "mes_example": "",
        "first_mes": "",
        "post_history_instructions": "",
        "alternate_greetings": "[]",
    }


def _set_relationship(service, db, rowid, value):
    group = NpcExtractionGroup(
        "Maya Torres",
        (),
        (
            NpcOperation(
                "relationship",
                "set",
                value,
                "mutable",
                "shared",
                (),
            ),
        ),
    )
    result = service.apply_group(
        db,
        "chat",
        "s1",
        group,
        source_rowid=rowid,
        primary_name="Mira",
        user_name="User",
    )
    assert result.applied == 1


def _relationship(db):
    entity = find_test_npc(db, "chat", "s1", "maya torres")
    if entity is None:
        return None
    state = load_npc_fields(db, entity.npc_id).get("relationship")
    return None if state is None else state.value


def _turn(db, role, content, at):
    return int(
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "s1", role, content, at),
        ).lastrowid
    )


def test_edit_uses_pre_edit_npc_state_and_rolls_back_future_changes(monkeypatch):
    db = _db()
    service = NpcService()
    captured = {}
    now = time.time()
    try:
        _turn(db, "user", "Maya Torres arrives.", now)
        early_assistant = _turn(db, "assistant", "Maya stays cautious.", now + 0.001)
        target_user = _turn(db, "user", "I confront Maya Torres.", now + 0.002)
        future_assistant = _turn(db, "assistant", "Maya turns hostile.", now + 0.003)
        db.commit()
        _set_relationship(service, db, early_assistant, "cautious")
        _set_relationship(service, db, future_assistant, "hostile")
        with write_transaction(db):
            set_npc_extraction_coverage(db, "chat", "s1", future_assistant, time.time())

        monkeypatch.setattr(
            edit_messages,
            "build_chat_messages",
            lambda *_args, **kwargs: captured.update(kwargs) or [{"role": "user", "content": "replacement"}],
        )
        monkeypatch.setattr(edit_messages, "send_typing", lambda *_a, **_k: None)
        monkeypatch.setattr(edit_messages, "send_reply", lambda *_a, **_k: None)
        monkeypatch.setattr(edit_messages, "telegram_request", lambda *_a, **_k: {})

        edit_messages.regenerate_edited_turn(
            db,
            "token",
            "key",
            _session(),
            _fields(),
            "chat",
            target_user,
            "replacement",
            provider_port=make_test_provider_port(generate_backend=lambda *_a, **_k: "new reply"),
            memory_service=make_test_memory_service(),
            npc_service=service,
            persona_service=make_test_persona_service(),
            app_settings=SettingsBuilder().build(),
            rag_service=make_test_rag_service(),
        )

        assert "Relationship: cautious" in captured["npc_context"]
        assert "hostile" not in captured["npc_context"]
        assert _relationship(db) == "cautious"
    finally:
        db.close()


def test_regen_uses_state_through_last_user_and_rolls_back_old_assistant(monkeypatch):
    db = _db()
    service = NpcService()
    captured = {}
    now = time.time()
    try:
        _turn(db, "user", "Maya Torres arrives.", now)
        early_assistant = _turn(db, "assistant", "Maya remains cautious.", now + 0.001)
        last_user = _turn(db, "user", "Continue with Maya Torres.", now + 0.002)
        old_assistant = _turn(db, "assistant", "Maya turns hostile.", now + 0.003)
        db.commit()
        _set_relationship(service, db, early_assistant, "cautious")
        _set_relationship(service, db, old_assistant, "hostile")
        with write_transaction(db):
            set_npc_extraction_coverage(db, "chat", "s1", old_assistant, time.time())

        monkeypatch.setattr(
            regeneration,
            "build_chat_messages",
            lambda *_args, **kwargs: captured.update(kwargs) or [{"role": "user", "content": "regen"}],
        )

        regeneration.regenerate_last(
            db,
            "token",
            "key",
            _session(),
            _fields(),
            "chat",
            provider_port=make_test_provider_port(generate_backend=lambda *_a, **_k: "replacement reply"),
            delivery_port=make_test_delivery_port(),
            memory_service=make_test_memory_service(),
            npc_service=service,
            persona_service=make_test_persona_service(),
            app_settings=SettingsBuilder().build(),
            rag_service=make_test_rag_service(),
        )

        assert "Relationship: cautious" in captured["npc_context"]
        assert "hostile" not in captured["npc_context"]
        assert _relationship(db) == "cautious"
        latest = db.execute(
            "SELECT content FROM messages WHERE chat_id='chat' AND session_id='s1' "
            "AND role='assistant' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert latest == ("*replacement reply*",)
        assert last_user < old_assistant
    finally:
        db.close()


def test_reset_purges_npc_bank(monkeypatch):
    db = _db()
    service = NpcService()
    try:
        _set_relationship(service, db, 1, "cautious")
        monkeypatch.setattr(message_commands, "delete_outgoing_messages", lambda *_a, **_k: None)
        monkeypatch.setattr(message_commands, "delete_incoming_messages", lambda *_a, **_k: None)
        monkeypatch.setattr(message_commands, "telegram_request", lambda *_a, **_k: {})

        message_commands.reset_session(
            db,
            "token",
            "chat",
            _session(),
            memory_service=make_test_memory_service(),
            npc_service=service,
        )

        assert find_test_npc(db, "chat", "s1", "maya torres") is None
        assert db.execute("SELECT COUNT(*) FROM npc_field_history").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM npc_extraction_state").fetchone()[0] == 0
    finally:
        db.close()


def test_keep_swipe_variant_rolls_back_old_assistant_npc_state():
    db = _db()
    service = NpcService()
    now = time.time()
    try:
        user_row = _turn(db, "user", "Maya Torres?", now)
        old_assistant = _turn(db, "assistant", "Old branch.", now + 0.001)
        db.commit()
        save_response_variant(db, "chat", "s1", "Maya Torres?", "Old branch.", user_rowid=user_row)
        save_response_variant(db, "chat", "s1", "Maya Torres?", "New branch.", user_rowid=user_row)
        _set_relationship(service, db, old_assistant, "hostile")

        selected = keep_swipe_variant(
            db,
            "chat",
            "s1",
            2,
            npc_service=service,
        )

        assert selected == "New branch."
        assert _relationship(db) is None
        latest = db.execute(
            "SELECT content FROM messages WHERE chat_id='chat' AND session_id='s1' "
            "AND role='assistant' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert latest == ("New branch.",)
    finally:
        db.close()


def test_swipe_keep_retain_queues_processing_for_selected_assistant(monkeypatch):
    db = _db()
    service = NpcService()
    retained = []
    now = time.time()

    class Memory:
        def retain(self, db, chat_id, session, fields):
            retained.append((chat_id, session["session_id"], fields["name"]))

    try:
        user_row = _turn(db, "user", "Choose Maya Torres.", now)
        old_assistant = _turn(db, "assistant", "Old branch.", now + 0.001)
        db.commit()
        save_response_variant(db, "chat", "s1", "Choose Maya Torres.", "Old branch.", user_rowid=user_row)
        save_response_variant(db, "chat", "s1", "Choose Maya Torres.", "New branch.", user_rowid=user_row)
        _set_relationship(service, db, old_assistant, "hostile")
        set_meta(db, swipe_state_key("chat", "s1"), "2")

        monkeypatch.setattr(conversation_callbacks, "delete_outgoing_messages", lambda *_a, **_k: None)
        monkeypatch.setattr(conversation_callbacks, "telegram_request", lambda *_a, **_k: {})
        monkeypatch.setattr(conversation_callbacks, "card_fields_from_file", lambda *_a, **_k: _fields())

        handled = conversation_callbacks.handle_swipe_callback(
            db,
            "token",
            {"id": "cb"},
            lambda *_a, **_k: None,
            "swipe:keep",
            "chat",
            {"message_id": 77},
            _session(),
            "s1",
            None,
            delivery_port=make_test_delivery_port(),
            memory_service=Memory(),
            npc_service=service,
            request_context=make_test_request_context(
                db,
                "s1",
                app_settings=SettingsBuilder().build(),
            ),
        )

        assert handled is True
        assert retained == [("chat", "s1", "Mira")]
        assert _relationship(db) is None
    finally:
        db.close()
