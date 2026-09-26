from application_test_setup import (
    ensure_application_extensions,
    make_test_conversation_service,
    make_test_memory_service,
)
from settings_test_support import SettingsTestCase

import bridge.callbacks as _owner_callbacks
import bridge.conversation_callbacks as _owner_conversation_callbacks
import bridge.session_core as _owner_session_core

ensure_application_extensions()

# pyright: reportAttributeAccessIssue=false

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_naming as _m_session_naming


class ResetBehaviorTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.old_reset = _m_message_commands.reset_session
        self.old_send = _owner_conversation_callbacks.send_text
        self.old_remove = _owner_callbacks.remove_inline_keyboard
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )

    def tearDown(self):
        _owner_conversation_callbacks.reset_session = self.old_reset
        _owner_conversation_callbacks.send_text = self.old_send
        _owner_conversation_callbacks.remove_inline_keyboard = self.old_remove
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_reset_command_preempts_pending_input(self):
        _m_session_naming.set_meta(
            self.db,
            "text_action_input:chat",
            json.dumps(
                {
                    "session_id": self.session["session_id"],
                    "action": "edit",
                    "expires_at": time.time() + 600,
                }
            ),
        )
        opened = []
        original_request = _m_message_commands.send_panel_request
        _m_message_commands.send_panel_request = lambda _token, method, payload, **_kwargs: (
            opened.append((method, payload)) or {}
        )
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                {},
                "chat",
                "/reset",
            )
        finally:
            _m_message_commands.send_panel_request = original_request
        self.assertEqual(opened[0][0], "sendMessage")
        self.assertEqual(opened[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "reset:confirm")
        self.assertIn('"action": "edit"', _m_session_naming.get_meta(self.db, "text_action_input:chat", ""))

    def test_confirmed_reset_sends_visible_completion_message(self):
        sent = []
        removed = []

        def original_answer(*_args, **_kwargs):
            return None

        _owner_conversation_callbacks.reset_session = lambda *_args, **_kwargs: None
        _owner_conversation_callbacks.send_text = lambda _token, _chat, text: sent.append(text) or []
        _owner_conversation_callbacks.remove_inline_keyboard = lambda _db, _token, callback: removed.append(callback)
        callback = {"id": "callback-1", "message": {"message_id": 10, "chat": {"id": "chat"}}}
        handled = _owner_conversation_callbacks.handle_reset_callback(
            self.db,
            "token",
            callback,
            original_answer,
            "reset:confirm",
            "chat",
            callback["message"],
            self.session,
            self.session["session_id"],
            None,
            memory_service=make_test_memory_service(),
        )
        self.assertTrue(handled)
        self.assertEqual(sent, ["Reset complete. The active session was cleared."])
        self.assertEqual(removed, [callback])

    def test_reset_clears_curated_memory_and_deletes_outgoing_telegram_messages(self):
        curator_key = _m_memory_curator.memory_curator_key("chat", self.session["session_id"])
        _m_session_naming.set_meta(
            self.db,
            curator_key,
            json.dumps({"items": [{"key": "stable", "text": "durable fact"}], "through_rowid": 2}),
        )
        deleted = []
        original_delete = _m_message_commands.delete_outgoing_messages
        _m_message_commands.delete_outgoing_messages = lambda *args, **kwargs: deleted.append((args, kwargs))
        try:
            _m_message_commands.reset_session(
                self.db,
                "token",
                "chat",
                self.session,
                memory_service=make_test_memory_service(),
            )
        finally:
            _m_message_commands.delete_outgoing_messages = original_delete

        self.assertEqual(_m_session_naming.get_meta(self.db, curator_key, ""), "")
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0][0][2:4], ("chat", self.session["session_id"]))


if __name__ == "__main__":
    unittest.main()
