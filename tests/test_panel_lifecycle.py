from application_test_setup import make_test_conversation_service
from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_request_context

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.database as _m_database
import json
import time
import bridge.callbacks as _m_callbacks
import bridge.callback_dispatch as _m_callback_dispatch
import bridge.cards as _m_cards
import bridge.input_flows as _m_input_flows
import bridge.help as _m_help
import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
import bridge.telegram as _m_telegram
class PanelLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_enum_close_deletes_panel_message(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 104, session["session_id"])
        calls = []
        original_answer = _m_callback_dispatch.answer_callback
        original_request = _m_callbacks.telegram_request
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        _m_callbacks.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-104",
            "from": {"id": "user-1"},
            "data": "enum:close",
            "message": {"message_id": 104, "chat": {"id": "chat"}},
        }
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.answer_callback = original_answer
            _m_callbacks.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage"])
        self.assertIsNone(_m_database.panel_session_for_message(self.db, "chat", 104))

    def test_settings_text_route_opens_panel_without_mutating(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        fields = {"name": "Test", "first_mes": "", "system_prompt": "", "description": "", "personality": "", "scenario": "", "mes_example": "", "post_history_instructions": ""}
        opened = []
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_input_flows.send_settings_menu
        _m_message_commands.card_fields_from_file = lambda _filename: fields
        _m_command_routes.send_settings_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "/settings temperature 0.7", services=make_test_application_services())
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_input_flows.send_settings_menu = original_menu
        self.assertEqual(opened, [True])
        settings = _m_memory_curator.get_generation_settings(self.db, "chat", session["session_id"])
        self.assertEqual(settings["temperature"], 0.85)

        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        original_answer = _m_callback_dispatch.answer_callback
        original_request = _m_callbacks.telegram_request
        original_send = _m_input_flows.send_text
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        try:
            for message_id, data in ((101, "enum:settings:input:temperature"), (102, "enum:preset:save"), (103, "enum:stt:language_input")):
                self.db.execute("DELETE FROM panel_sessions")
                _m_telegram.bind_panel_session(self.db, "chat", message_id, session["session_id"])
                calls = []
                _m_callbacks.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
                _m_help.send_text = lambda _token, _chat_id, text: calls.append(("sendText", {"text": text})) or []
                callback = {
                    "id": f"callback-{message_id}",
                    "from": {"id": "user-1"},
                    "data": data,
                    "message": {"message_id": message_id, "chat": {"id": "chat"}},
                }
                _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
                self.assertEqual(calls[0][0], "deleteMessage", data)
                self.assertEqual(calls[1][0], "sendText", data)
                self.assertIsNone(_m_database.panel_session_for_message(self.db, "chat", message_id), data)
        finally:
            _m_callback_dispatch.answer_callback = original_answer
            _m_callbacks.telegram_request = original_request
            _m_help.send_text = original_send

    def test_settings_panel_displays_current_field_values(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_sync_core.update_generation_settings(self.db, "chat", session["session_id"], temperature=0.7, max_tokens=900, top_p=0.8, frequency_penalty=0.2, presence_penalty=-0.1, stop_sequences="END")
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_settings_menu("token", "chat", self.db, session["session_id"], request_context=make_test_request_context(self.db, session["session_id"]))
        finally:
            _m_cards.send_panel_request = original_request
        text = calls[0][1]["text"]
        for value in ("temperature=0.7", "max_tokens=900", "top_p=0.8", "frequency_penalty=0.2", "presence_penalty=-0.1", "stop_sequences=END"):
            self.assertIn(value, text)

    def test_settings_invalid_feedback_is_deleted_by_cancel(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_session_naming.set_meta(self.db, "settings_input:chat", json.dumps({"key": "temperature", "session_id": session["session_id"], "expires_at": time.time() + 600, "prompt_message_ids": [90]}))
        fields = {"name": "Test", "first_mes": "", "system_prompt": "", "description": "", "personality": "", "scenario": "", "mes_example": "", "post_history_instructions": ""}
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_command_routes.send_settings_menu
        original_send = _m_message_commands.send_text
        original_request = _m_telegram.telegram_request
        deleted = []
        _m_message_commands.card_fields_from_file = lambda _filename: fields
        _m_input_flows.send_settings_menu = lambda *_args, **_kwargs: None
        _m_message_commands.send_text = lambda *_args, **_kwargs: [91]
        _m_telegram.telegram_request = lambda _token, method, payload: deleted.append((method, payload)) or {}
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "99", services=make_test_application_services())
            pending = json.loads(_m_session_naming.get_meta(self.db, "settings_input:chat", "{}"))
            self.assertEqual(pending["prompt_message_ids"], [90, 91])
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "/cancel", services=make_test_application_services())
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_command_routes.send_settings_menu = original_menu
            _m_message_commands.send_text = original_send
            _m_telegram.telegram_request = original_request
        self.assertEqual(deleted, [("deleteMessage", {"chat_id": "chat", "message_id": 90}), ("deleteMessage", {"chat_id": "chat", "message_id": 91})])

    def test_character_upload_uses_closable_guidance_panel(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 105, session["session_id"])
        calls = []
        original_answer = _m_callback_dispatch.answer_callback
        original_request = _m_panel_callback_routes.send_panel_request
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        _m_panel_callback_routes.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-105",
            "from": {"id": "user-1"},
            "data": "character:upload",
            "message": {"message_id": 105, "chat": {"id": "chat"}},
        }
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.answer_callback = original_answer
            _m_panel_callback_routes.send_panel_request = original_request
        self.assertEqual([method for method, _payload in calls], ["editMessageText"])
        self.assertEqual(calls[0][1]["message_id"], 105)
        buttons = calls[0][1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[-1][1]["callback_data"], "character:cancel")

    def test_character_cancel_deletes_panel_message(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 106, session["session_id"])
        calls = []
        original_answer = _m_callback_dispatch.answer_callback
        original_request = _m_callbacks.telegram_request
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        _m_callbacks.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-106",
            "from": {"id": "user-1"},
            "data": "character:cancel",
            "message": {"message_id": 106, "chat": {"id": "chat"}},
        }
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.answer_callback = original_answer
            _m_callbacks.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage"])
        self.assertIsNone(_m_database.panel_session_for_message(self.db, "chat", 106))


    def test_owned_panel_rejects_different_user_without_closing_it(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_session_naming.update_session(self.db, "chat", session["session_id"], author_note="keep me")
        _m_telegram.bind_panel_session(self.db, "chat", 107, session["session_id"], "user-1")
        answers = []
        original_answer = _m_callback_dispatch.answer_callback
        _m_callback_dispatch.answer_callback = lambda _token, _callback_id, text: answers.append(text)
        callback = {
            "id": "callback-107",
            "from": {"id": "user-2"},
            "data": "note:off",
            "message": {"message_id": 107, "chat": {"id": "chat"}},
        }
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.answer_callback = original_answer
        self.assertEqual(answers, ["This panel belongs to another user"])
        self.assertEqual(
            _m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)["author_note"],
            "keep me",
        )
        self.assertEqual(_m_database.panel_owner_for_message(self.db, "chat", 107), "user-1")
        self.assertEqual(_m_database.panel_session_for_message(self.db, "chat", 107), session["session_id"])


if __name__ == "__main__":
    unittest.main()
