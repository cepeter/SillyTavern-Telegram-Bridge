"""Characterization for focused UI owners and atomic generation variant workflows."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest
from application_test_setup import make_test_request_context

import bridge.enum_callbacks as _owner_enum_callbacks
from bridge.metadata import get_meta
from bridge.response_variants import save_response_variant
from bridge.session_core import ensure_session
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect, write_transaction

ROOT = Path(__file__).parents[1]
OWNERS = {
    "settings_panels": "send_settings_menu",
    "voice_panels": "send_voice_input_menu",
    "preset_panels": "send_preset_menu",
    "databank_panels": "send_databank_menu",
    "enum_callbacks": "handle_enum_callback",
    "bot_commands": "set_bot_commands",
    "document_jobs": "process_document_job",
    "group_setup": "start_group_session",
    "group_panels": "send_group_menu",
    "group_callbacks": "handle_group_panel_callback",
    "persona_input": "_handle_persona_input",
    "persona_callbacks": "handle_persona_callback",
    "pending_input": "_pending_state",
    "feature_callbacks": "handle_feature_panel_callback",
    "speech": "transcribe_audio_bytes",
    "response_delivery": "send_reply",
    "voice_jobs": "process_voice_job",
    "regeneration": "regenerate_last",
    "continuation": "continue_last",
    "response_variants": "save_response_variant",
    "swipe_panels": "send_swipe_menu",
    "image_messages": "process_image_message",
    "edit_messages": "regenerate_edited_turn",
    "provider_discovery": "refresh_model_catalog",
    "provider_panels": "send_model_menu",
    "world_panels": "send_world_menu",
}


@pytest.mark.parametrize("module,function", OWNERS.items())
def test_feature_function_has_one_focused_owner(module, function):
    path = ROOT / "bridge" / f"{module}.py"
    assert path.is_file(), module
    owner = importlib.import_module(f"bridge.{module}")
    assert getattr(owner, function).__module__ == f"bridge.{module}"


@pytest.mark.parametrize(
    "data,key,value,panel",
    [
        ("enum:stream:on", "stream_mode:chat", "on", "send_stream_menu"),
        ("enum:stream:off", "stream_mode:chat", "off", "send_stream_menu"),
        ("enum:voice:on", "voice_mode:chat", "tts", "send_voice_menu"),
        ("enum:voice:off", "voice_mode:chat", "off", "send_voice_menu"),
        ("enum:stt:on", "stt_mode:chat", "on", "send_voice_input_menu"),
        ("enum:stt:off", "stt_mode:chat", "off", "send_voice_input_menu"),
        ("enum:sttlanguage:en", "stt_language:chat", "en", "send_voice_input_menu"),
        ("enum:sttmodel:base", "stt_model:chat", "base", "send_voice_input_menu"),
        ("enum:memory:on", "memory_mode:chat", "on", "send_memory_menu"),
        ("enum:memory:off", "memory_mode:chat", "off", "send_memory_menu"),
        ("enum:rag:on", "rag_mode:chat", "on", "send_databank_menu"),
        ("enum:rag:off", "rag_mode:chat", "off", "send_databank_menu"),
    ],
)
def test_enum_feature_toggle_retains_scope_and_refreshes_its_own_panel(tmp_path, monkeypatch, data, key, value, panel):
    settings = load_app_settings({}, home=tmp_path)
    db = db_connect(app_settings=settings)
    handler = _owner_enum_callbacks.handle_enum_callback
    calls = []
    owner = inspect.getmodule(handler)
    monkeypatch.setattr(owner, panel, lambda *args, **kwargs: calls.append((args, kwargs)))
    try:
        session = ensure_session(db, "chat", "provider::model", app_settings=settings)
        context = make_test_request_context(db, session["session_id"], "actor", app_settings=settings)
        handler(
            db,
            "fixture",
            "chat",
            session,
            data,
            {"message_id": 17},
            input_flow_service=object(),
            request_context=context,
        )
        assert get_meta(db, key) == value
        assert get_meta(db, key.replace(":chat", ":other")) == ""
        assert len(calls) == 1
        assert calls[0][0][:2] == ("fixture", "chat")
        assert calls[0][1]["request_context"] is context
    finally:
        db.close()


def test_variant_save_participates_in_outer_rollback(tmp_path):
    settings = load_app_settings({}, home=tmp_path)
    db = db_connect(app_settings=settings)
    try:
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s','user','hello',0)"
        )
        db.commit()
        with pytest.raises(ValueError, match="abort"):
            with write_transaction(db):
                save_response_variant(db, "chat", "s", "hello", "response")
                assert db.in_transaction, "variant helper committed caller transaction"
                raise ValueError("abort")
        assert db.execute("SELECT * FROM response_variants").fetchall() == []
    finally:
        db.close()


def test_old_ui_aggregates_are_not_retained_as_facades():
    for filename in ("help.py", "groups.py", "media.py", "commands.py", "catalog.py"):
        assert not (ROOT / "bridge" / filename).exists(), filename
    tree = ast.parse((ROOT / "bridge/input_flows.py").read_text())
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == {"handle_pending_input"}
