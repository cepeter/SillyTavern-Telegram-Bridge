import application_test_setup as application_setup
from application_test_setup import ensure_application_extensions, make_test_group_service, make_test_request_context
from settings_test_support import make_test_settings

import bridge.character_callbacks as _owner_character_callbacks

ensure_application_extensions()

import json
import sqlite3

import pytest

import bridge.telegram as telegram


def _callback(message_id=41, data="characterinfo:token"):
    return {"id": "cb", "data": data, "message": {"message_id": message_id}}


def _info():
    return {
        "name": "Alisha",
        "description": "description",
        "personality": "personality",
        "scenario": "scenario",
        "first_mes": "hello",
    }


def test_character_info_selection_sends_selected_png_as_bound_photo(tmp_path, monkeypatch):
    card = tmp_path / "Alisha.png"
    card.write_bytes(b"png-card-bytes")
    db = sqlite3.connect(":memory:")
    context = make_test_request_context(db, "session", "owner", app_settings=make_test_settings(home=tmp_path))
    sent = []
    closed = []

    monkeypatch.setattr(_owner_character_callbacks, "resolve_dynamic_callback_token", lambda *_a, **_k: card.name)
    monkeypatch.setattr(_owner_character_callbacks, "safe_character_path", lambda *_a, **_k: card)
    monkeypatch.setattr(_owner_character_callbacks, "card_fields_from_file", lambda *_a, **_k: _info())
    monkeypatch.setattr(
        _owner_character_callbacks, "send_panel_photo", lambda *a, **k: sent.append((a, k)), raising=False
    )
    monkeypatch.setattr(_owner_character_callbacks, "close_panel_message", lambda *a, **k: closed.append((a, k)))
    monkeypatch.setattr(
        _owner_character_callbacks, "send_panel_request", lambda *_a, **_k: pytest.fail("text fallback must not run")
    )

    try:
        handled = _owner_character_callbacks.handle_character_callback(
            db,
            "bot-token",
            _callback(),
            lambda *_a: None,
            "characterinfo:token",
            "chat",
            {"message_id": 41},
            {"character_file": "Active.png"},
            "session",
            None,
            group_service=make_test_group_service(app_settings=context.app_settings),
            request_context=context,
            provider_port=application_setup.make_test_provider_port(),
        )
    finally:
        db.close()

    assert handled is True
    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[:3] == ("bot-token", "chat", card)
    assert "Character: Alisha" in args[3]
    assert "File: Alisha.png" in args[3]
    assert "Description: 11 chars" in args[3]
    buttons = args[4]["inline_keyboard"][0]
    assert buttons == [
        {"text": "⬅️ Back", "callback_data": "characterinfo:back"},
        {"text": "❌ Close", "callback_data": "character:cancel"},
    ]
    assert kwargs["request_context"] is context
    assert len(closed) == 1


def test_character_info_photo_failure_falls_back_to_existing_text_panel(tmp_path, monkeypatch):
    card = tmp_path / "Alisha.png"
    card.write_bytes(b"png-card-bytes")
    db = sqlite3.connect(":memory:")
    context = make_test_request_context(db, "session", "owner", app_settings=make_test_settings(home=tmp_path))
    fallback = []
    closed = []

    monkeypatch.setattr(_owner_character_callbacks, "resolve_dynamic_callback_token", lambda *_a, **_k: card.name)
    monkeypatch.setattr(_owner_character_callbacks, "safe_character_path", lambda *_a, **_k: card)
    monkeypatch.setattr(_owner_character_callbacks, "card_fields_from_file", lambda *_a, **_k: _info())
    monkeypatch.setattr(
        _owner_character_callbacks,
        "send_panel_photo",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("Telegram rejected photo")),
        raising=False,
    )
    monkeypatch.setattr(_owner_character_callbacks, "close_panel_message", lambda *a, **k: closed.append((a, k)))
    monkeypatch.setattr(_owner_character_callbacks, "send_panel_request", lambda *a, **k: fallback.append((a, k)) or {})

    try:
        _owner_character_callbacks.handle_character_callback(
            db,
            "bot-token",
            _callback(),
            lambda *_a: None,
            "characterinfo:token",
            "chat",
            {"message_id": 41},
            {"character_file": "Active.png"},
            "session",
            None,
            group_service=make_test_group_service(app_settings=context.app_settings),
            request_context=context,
            provider_port=application_setup.make_test_provider_port(),
        )
    finally:
        db.close()

    assert closed == []
    assert len(fallback) == 1
    args, kwargs = fallback[0]
    assert args[1] == "editMessageText"
    assert args[2]["message_id"] == 41
    assert "Character: Alisha" in args[2]["text"]
    assert kwargs["request_context"] is context


def test_character_info_photo_back_closes_photo_and_opens_fresh_info_list(monkeypatch):
    db = sqlite3.connect(":memory:")
    context = make_test_request_context(db, "session", "owner")
    closed = []
    opened = []
    answered = []
    monkeypatch.setattr(_owner_character_callbacks, "close_panel_message", lambda *a, **k: closed.append((a, k)))
    monkeypatch.setattr(_owner_character_callbacks, "send_character_info_menu", lambda *a, **k: opened.append((a, k)))

    try:
        handled = _owner_character_callbacks.handle_character_callback(
            db,
            "bot-token",
            _callback(data="characterinfo:back"),
            lambda *a: answered.append(a),
            "characterinfo:back",
            "chat",
            {"message_id": 41},
            {"character_file": "Active.png"},
            "session",
            None,
            group_service=make_test_group_service(app_settings=context.app_settings),
            request_context=context,
            provider_port=application_setup.make_test_provider_port(),
        )
    finally:
        db.close()

    assert handled is True
    assert answered[-1][-1] == "Back"
    assert len(closed) == 1
    args, kwargs = opened[0]
    assert args[:2] == ("bot-token", "chat")
    assert len(args) == 2
    assert kwargs["request_context"] is context


def test_send_panel_photo_uploads_reply_markup_and_binds_returned_message(tmp_path, monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE panel_sessions("
        "chat_id TEXT,message_id TEXT,session_id TEXT,owner_user_id TEXT,expires_at REAL,"
        "PRIMARY KEY(chat_id,message_id))"
    )
    context = make_test_request_context(db, "session-1", "owner-1", app_settings=make_test_settings(home=tmp_path))
    card = tmp_path / "Card.png"
    card.write_bytes(b"character-png")
    captured = {}

    class Response:
        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 777}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["content_type"] = request.headers["Content-type"]
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    markup = {"inline_keyboard": [[{"text": "Back", "callback_data": "characterinfo:back"}]]}

    try:
        result = telegram.send_panel_photo(
            "secret-token",
            "chat",
            card,
            "Character: Card",
            markup,
            request_context=context,
        )
        binding = db.execute(
            "SELECT session_id,owner_user_id FROM panel_sessions WHERE chat_id=? AND message_id=?",
            ("chat", "777"),
        ).fetchone()
    finally:
        db.close()

    assert result["message_id"] == 777
    assert captured["url"].endswith("/botsecret-token/sendPhoto")
    assert b"character-png" in captured["body"]
    assert b"Character: Card" in captured["body"]
    assert b"characterinfo:back" in captured["body"]
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert binding == ("session-1", "owner-1")
