import application_test_setup as application_setup
from application_test_setup import (
    ensure_application_extensions,
    make_test_group_service,
    make_test_memory_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.character_callbacks as _owner_character_callbacks
import bridge.session_callbacks as _owner_session_callbacks
import bridge.session_core as _owner_session_core
from bridge import pending_input

ensure_application_extensions()

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming


class CharacterSessionChainTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.group = make_test_group_service(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _callback(self, data, message_id=10):
        return {
            "id": "callback",
            "from": {"id": "user"},
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": "chat"}},
        }

    def test_character_selection_opens_session_panel(self):
        session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        opened = []
        original_resolve = _owner_character_callbacks.resolve_dynamic_callback_token
        original_safe = _owner_character_callbacks.safe_character_path
        original_fields = _owner_character_callbacks.card_fields_from_file
        original_close = _owner_character_callbacks.close_panel_message
        original_menu = _owner_character_callbacks.send_session_menu
        _owner_character_callbacks.resolve_dynamic_callback_token = lambda *_args, **_kwargs: "chosen.png"
        _owner_character_callbacks.safe_character_path = lambda _name, *, app_settings=None: Path("/tmp/chosen.png")
        _owner_character_callbacks.card_fields_from_file = lambda _name, *, app_settings=None: {"name": "Chosen"}
        _owner_character_callbacks.close_panel_message = lambda *_args, **_kwargs: None
        _owner_character_callbacks.send_session_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            callback = self._callback("character:token")
            handled = _owner_character_callbacks.handle_character_callback(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                callback["data"],
                "chat",
                callback["message"],
                session,
                session["session_id"],
                None,
                group_service=self.group,
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
                provider_port=application_setup.make_test_provider_port(),
            )
        finally:
            _owner_character_callbacks.resolve_dynamic_callback_token = original_resolve
            _owner_character_callbacks.safe_character_path = original_safe
            _owner_character_callbacks.card_fields_from_file = original_fields
            _owner_character_callbacks.close_panel_message = original_close
            _owner_character_callbacks.send_session_menu = original_menu
        self.assertTrue(handled)
        self.assertEqual(opened, [True])
        self.assertEqual(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                session["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )["character_file"],
            self.app_settings_builder.default_character_file,
        )
        pending = json.loads(_m_session_naming.get_meta(self.db, "character_session_input:chat", "{}"))
        self.assertEqual(pending["character_file"], "chosen.png")
        self.assertEqual(pending["character_name"], "Chosen")

    def test_session_selection_applies_pending_character(self):
        current = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        target = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="target",
            app_settings=self.app_settings_builder.build(),
        )
        _m_session_naming.set_meta(self.db, "active_session:chat", current["session_id"])
        _m_session_naming.set_meta(
            self.db,
            "character_session_input:chat",
            json.dumps({"character_file": "chosen.png", "character_name": "Chosen", "expires_at": time.time() + 600}),
        )
        original_safe = pending_input.safe_character_path
        original_remove = _owner_session_callbacks.remove_inline_keyboard
        original_send = _owner_session_callbacks.send_text
        pending_input.safe_character_path = lambda _name, *, app_settings=None: Path("/tmp/chosen.png")
        _owner_session_callbacks.remove_inline_keyboard = lambda *_args, **_kwargs: None
        sent = []
        _owner_session_callbacks.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            callback = self._callback("session:target")
            handled = _owner_session_callbacks.handle_session_callback(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                callback["data"],
                "chat",
                callback["message"],
                current,
                current["session_id"],
                None,
                group_service=self.group,
                memory_service=make_test_memory_service(),
                request_context=make_test_request_context(
                    self.db, current["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            pending_input.safe_character_path = original_safe
            _owner_session_callbacks.remove_inline_keyboard = original_remove
            _owner_session_callbacks.send_text = original_send
        self.assertTrue(handled)
        self.assertEqual(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                target["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )["character_file"],
            "chosen.png",
        )
        self.assertEqual(_m_session_naming.get_meta(self.db, "active_session:chat", ""), "target")
        self.assertEqual(_m_session_naming.get_meta(self.db, "character_session_input:chat", ""), "")
        self.assertIn("Chosen", sent[0])


if __name__ == "__main__":
    unittest.main()
