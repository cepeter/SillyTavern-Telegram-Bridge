from application_test_setup import make_test_conversation_service
from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_provider_port, make_test_request_context

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
import bridge.commands as _m_commands
import bridge.command_routes as _m_command_routes
import bridge.input_flows as _m_input_flows
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.telegram as _m_telegram
class NotePanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _fields(self):
        return {
            "name": "Test",
            "first_mes": "",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }

    def test_note_panel_has_off_and_user_input(self):
        calls = []
        original_request = _m_commands.send_panel_request
        _m_commands.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_panel_callback_routes.send_note_menu("token", "chat", "existing note", request_context=make_test_request_context(self.db))
        finally:
            _m_commands.send_panel_request = original_request
        self.assertIn("note:off", {b["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for b in row})
        self.assertIn("note:input", {b["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for b in row})
        self.assertIn("Author's Note — on", calls[0][1]["text"])

    def test_note_text_command_opens_panel_without_mutating(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        fields = self._fields()
        opened = []
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_command_routes.send_note_menu
        _m_message_commands.card_fields_from_file = lambda _filename: fields
        _m_command_routes.send_note_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "/note new text", services=make_test_application_services())
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_command_routes.send_note_menu = original_menu
        self.assertEqual(opened, [True])
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)["author_note"], "")

    def test_note_user_input_updates_session_and_expires_state(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        fields = self._fields()
        _m_session_naming.set_meta(self.db, "note_input:chat", json.dumps({"session_id": session["session_id"], "expires_at": time.time() + 600, "prompt_message_ids": [90]}))
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_input_flows.send_note_menu
        original_send = _m_input_flows.send_text
        _m_message_commands.card_fields_from_file = lambda _filename: fields
        _m_input_flows.send_note_menu = lambda *_args, **_kwargs: None
        _m_input_flows.send_text = lambda *_args, **_kwargs: []
        original_request = _m_telegram.telegram_request
        deleted = []
        _m_telegram.telegram_request = lambda _token, method, payload: deleted.append((method, payload)) or {}
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "remember this", operation_id=701, services=make_test_application_services())
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_input_flows.send_note_menu = original_menu
            _m_input_flows.send_text = original_send
            _m_telegram.telegram_request = original_request
        loaded = _m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)
        self.assertEqual(loaded["author_note"], "remember this")
        self.assertEqual(_m_session_naming.get_meta(self.db, "note_input:chat", ""), "")
        self.assertEqual(_m_message_commands.operation_phase(self.db, 701), "applied")
        self.assertEqual(deleted, [("deleteMessage", {"chat_id": "chat", "message_id": 90})])

    def test_note_cancel_deletes_text_prompt(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        fields = self._fields()
        _m_session_naming.set_meta(self.db, "note_input:chat", json.dumps({"session_id": session["session_id"], "expires_at": time.time() + 600, "prompt_message_ids": [90]}))
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_input_flows.send_note_menu
        original_send = _m_input_flows.send_text
        original_request = _m_telegram.telegram_request
        deleted = []
        _m_message_commands.card_fields_from_file = lambda _filename: fields
        _m_input_flows.send_note_menu = lambda *_args, **_kwargs: None
        _m_input_flows.send_text = lambda *_args, **_kwargs: []
        _m_telegram.telegram_request = lambda _token, method, payload: deleted.append((method, payload)) or {}
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "/cancel", services=make_test_application_services())
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_input_flows.send_note_menu = original_menu
            _m_input_flows.send_text = original_send
            _m_telegram.telegram_request = original_request
        self.assertEqual(deleted, [("deleteMessage", {"chat_id": "chat", "message_id": 90})])
        self.assertEqual(_m_session_naming.get_meta(self.db, "note_input:chat", ""), "")

    def test_note_user_input_closes_original_panel(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 78, session["session_id"])
        calls = []
        original_answer = _m_callback_dispatch.answer_callback
        original_request = _m_callbacks.telegram_request
        original_send = _m_panel_callback_routes.send_text
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        _m_callbacks.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        _m_panel_callback_routes.send_text = lambda _token, _chat_id, text: calls.append(("sendText", {"text": text})) or [90]
        callback = {
            "id": "callback-2",
            "from": {"id": "user-1"},
            "data": "note:input",
            "message": {"message_id": 78, "chat": {"id": "chat"}},
        }
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.answer_callback = original_answer
            _m_callbacks.telegram_request = original_request
            _m_panel_callback_routes.send_text = original_send
        self.assertEqual(calls[0][0], "deleteMessage")
        self.assertEqual(calls[0][1]["message_id"], 78)
        self.assertEqual(calls[1][0], "sendText")
        pending = json.loads(_m_session_naming.get_meta(self.db, "note_input:chat", "{}"))
        self.assertEqual(pending["prompt_message_ids"], [90])
        self.assertIsNone(_m_database.panel_session_for_message(self.db, "chat", 78))

    def test_note_cancel_closes_previous_panel(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 77, session["session_id"])
        calls = []
        original_answer = _m_callback_dispatch.answer_callback
        original_request = _m_callbacks.telegram_request
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        _m_callbacks.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-1",
            "from": {"id": "user-1"},
            "data": "note:cancel",
            "message": {"message_id": 77, "chat": {"id": "chat"}},
        }
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.answer_callback = original_answer
            _m_callbacks.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage"])
        self.assertEqual(calls[0][1]["message_id"], 77)
        self.assertIsNone(_m_database.panel_session_for_message(self.db, "chat", 77))

    def test_note_close_uses_valid_marker_when_delete_is_rejected(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 79, session["session_id"])
        calls = []
        original_request = _m_callbacks.telegram_request
        def request(_token, method, payload):
            calls.append((method, payload))
            if method == "deleteMessage":
                raise RuntimeError("delete rejected")
            return {}
        _m_callbacks.telegram_request = request
        try:
            _m_session_naming.close_panel_message(self.db, "token", "chat", {"message_id": 79, "chat": {"id": "chat"}})
        finally:
            _m_callbacks.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage", "editMessageText"])
        self.assertEqual(calls[0][1]["message_id"], 79)
        self.assertEqual(calls[1][1]["message_id"], 79)
        self.assertEqual(calls[1][1]["text"], "Panel closed.")
        self.assertIsNone(_m_database.panel_session_for_message(self.db, "chat", 79))

    def test_removed_authornote_alias_does_not_generate(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        fields = self._fields()
        sent = []
        original_card = _m_message_commands.card_fields_from_file
        original_send = _m_command_routes.send_text
        _m_message_commands.card_fields_from_file = lambda _filename: fields
        _m_command_routes.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, fields, "chat", "/authornote old text", services=make_test_application_services(provider=make_test_provider_port(generate_backend=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("removed alias must not generate")))))
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_command_routes.send_text = original_send
        self.assertEqual(sent, ["Unknown or removed command. Use /help to see available commands."])
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)["author_note"], "")

    def test_note_is_session_scoped(self):
        self.assertTrue(_m_callbacks.is_session_scoped_panel_callback("note:off"))
        self.assertTrue(_m_callbacks.is_session_scoped_panel_callback("note:input"))


if __name__ == "__main__":
    unittest.main()
