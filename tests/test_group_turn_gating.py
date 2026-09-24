from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_group_service, make_test_input_flow_service, make_test_memory_service, make_test_persona_service, make_test_request_context, make_test_provider_port

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest

import bridge.card_content as card_content
import bridge.config as config
import json
import time
import bridge.callbacks as _m_callbacks
import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.character_identity as _m_character_identity
import bridge.groups as _m_groups
import bridge.group_core as _m_group_core
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.input_flows as _m_input_flows
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
import bridge.telegram as _m_telegram
class GroupTurnGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.group = make_test_group_service()
        self.group.save(self.db, "chat", "session", {
            "title": "Group chat",
            "enabled": True,
            "turn_index": 0,
            "mode": "manual",
            "forced_speaker": "",
            "members": ["one.png", "two.png"],
            "turn_user_id": "",
            "turn_users": [],
        })

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _callback(self, data, message_id=10):
        return {"id": "callback", "from": {"id": "user"}, "data": data, "message": {"message_id": message_id, "chat": {"id": "chat"}}}

    def test_manual_mode_allows_owner_and_rejects_other_user(self):
        self.assertTrue(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertFalse(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-b"))
        self.assertTrue(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        state = _m_sync_api.group_state(self.db, "chat", "session")
        self.assertEqual(state["turn_user_id"], "user-a")
        self.assertEqual(state["turn_users"], ["user-a", "user-b"])

    def test_owner_can_pass_turn_to_next_known_user(self):
        self.assertTrue(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertFalse(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-b"))
        self.assertTrue(self.group.claim_user_turn(self.db, "chat", "session", "user-a"))
        self.assertTrue(self.group.pass_user_turn(self.db, "chat", "session", "user-a"))
        self.assertFalse(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertTrue(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-b"))

    def test_non_manual_mode_does_not_gate_user_messages(self):
        state = _m_sync_api.group_state(self.db, "chat", "session")
        state["mode"] = "round_robin"
        self.group.save(self.db, "chat", "session", state)
        self.assertTrue(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertTrue(_m_group_core.group_user_turn_allowed(self.db, "chat", "session", "user-b"))

    def test_turn_controls_are_visible_only_in_manual_mode(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, _method, payload, **_kwargs: calls.append(payload) or {}
        try:
            state = _m_sync_api.group_state(self.db, "chat", "session")
            state["mode"] = "manual"
            self.group.save(self.db, "chat", "session", state)
            _m_panel_callback_routes.send_group_menu(self.db, "token", "chat", {"session_id": "session"}, group_service=self.group, request_context=make_test_request_context(self.db, "session", "user"))
            manual_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertEqual(manual_callbacks & {"group:claim", "group:pass"}, {"group:claim", "group:pass"})
            state["mode"] = "round_robin"
            self.group.save(self.db, "chat", "session", state)
            _m_panel_callback_routes.send_group_menu(self.db, "token", "chat", {"session_id": "session"}, group_service=self.group, request_context=make_test_request_context(self.db, "session", "user"))
            other_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertEqual(other_callbacks & {"group:claim", "group:pass"}, set())
        finally:
            _m_cards.send_panel_request = original_request

    def test_group_panel_ignores_not_modified_response(self):
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified"))
        try:
            _m_panel_callback_routes.send_group_menu(self.db, "token", "chat", {"session_id": "session"}, message_id=10, group_service=self.group, request_context=make_test_request_context(self.db, "session", "user"))
        finally:
            _m_cards.send_panel_request = original_request

    def test_new_group_session_button_only_appears_in_topic(self):
        original_request = _m_cards.send_panel_request
        calls = []
        _m_cards.send_panel_request = lambda _token, _method, payload, **_kwargs: calls.append(payload) or {}
        try:
            _m_panel_callback_routes.send_group_menu(self.db, "token", "chat|topic:7", {"session_id": "session"}, group_service=self.group, request_context=make_test_request_context(self.db, "session", "user"))
            topic_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertIn("group:new_session", topic_callbacks)
            _m_panel_callback_routes.send_group_menu(self.db, "token", "chat", {"session_id": "session"}, group_service=self.group, request_context=make_test_request_context(self.db, "session", "user"))
            dm_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertNotIn("group:new_session", dm_callbacks)
        finally:
            _m_cards.send_panel_request = original_request
    def test_group_command_is_rejected_in_direct_chat(self):
        sent = []
        original_send = _m_command_routes.send_text
        _m_command_routes.send_text = lambda _token, _chat, text: sent.append(text) or []
        session = {"session_id": "session", "persona_id": "", "model_id": _m_memory_curator.DEFAULT_MODEL, "author_note": "", "world_file": "", "system_prompt": "", "response_language": "auto"}
        try:
            handled = _m_command_routes.handle_command_route(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, {}, "chat", "/group", "/group", session, "session", _m_memory_curator.DEFAULT_MODEL, "", "Test User", request_context=make_test_request_context(self.db, "session", "user"), services=make_test_application_services())
        finally:
            _m_command_routes.send_text = original_send
        self.assertTrue(handled)
        self.assertEqual(sent, ["Group sessions are available only inside a Telegram Forum Topic."])

    def test_new_group_session_starts_character_wizard_in_topic(self):
        chat_id = "chat|topic:7"
        session = _m_telegram.ensure_session(self.db, chat_id, _m_memory_curator.DEFAULT_MODEL)
        opened = []
        original_close = _m_session_naming.close_panel_message
        original_menu = _m_session_naming.send_character_menu
        original_send = _m_session_naming.send_text
        _m_session_naming.close_panel_message = lambda *_args, **_kwargs: None
        _m_session_naming.send_character_menu = lambda _token, _chat, _character, **kwargs: opened.append(kwargs["request_context"].session_id)
        _m_session_naming.send_text = lambda *_args, **_kwargs: []
        callback = {"id": "callback", "from": {"id": "user"}, "data": "group:new_session", "message": {"message_id": 10, "chat": {"id": chat_id}}}
        try:
            _m_groups.handle_group_panel_callback(self.db, "token", chat_id, session, "group:new_session", callback["message"], sender_id="user", group_service=self.group, input_flow_service=make_test_input_flow_service(), request_context=make_test_request_context(self.db, session["session_id"], "user"))
            pending = _m_session_naming.get_meta(self.db, f"session_name_input:{chat_id}", "")
            self.assertTrue(pending)
            _m_input_flows.handle_pending_input(self.db, "token", chat_id, session, "Named Group", operation_id=77, group_service=self.group, provider_port=make_test_provider_port(), memory_service=make_test_memory_service(), persona_service=make_test_persona_service(), request_context=make_test_request_context(self.db, session["session_id"], "user"))
        finally:
            _m_session_naming.close_panel_message = original_close
            _m_session_naming.send_character_menu = original_menu
            _m_session_naming.send_text = original_send
        active_id = _m_session_naming.get_meta(self.db, f"active_session:{chat_id}", "")
        setup = self.group.setup_state(self.db, chat_id, active_id)
        self.assertEqual(active_id, "group-77")
        self.assertIsNotNone(setup)
        self.assertEqual(setup["stage"], "character")
        self.assertEqual(opened[-1], "group-77")

    def test_group_wizard_chains_character_to_world_then_group(self):
        chat_id = "chat|topic:8"
        session = _m_telegram.ensure_session(self.db, chat_id, _m_memory_curator.DEFAULT_MODEL)
        _m_session_naming.set_meta(self.db, f"group_setup:{chat_id}", json.dumps({"session_id": session["session_id"], "stage": "character", "expires_at": time.time() + 600}))
        original_resolve = _m_panel_callback_routes.resolve_dynamic_callback_token
        original_char_path = _m_panel_callback_routes.safe_character_path
        original_world_path = _m_panel_callback_routes.safe_world_path
        original_canonical_world_path = card_content.safe_world_path
        original_telegram_world_path = _m_telegram.safe_world_path
        original_fields = _m_panel_callback_routes.card_fields_from_file
        original_close = _m_panel_callback_routes.close_panel_message
        original_world_menu = _m_panel_callback_routes.send_world_menu
        original_groups_world_menu = _m_groups.send_world_menu
        original_remove = _m_panel_callback_routes.remove_inline_keyboard
        original_group_menu = _m_panel_callback_routes.send_group_menu
        opened_world = []
        opened_group = []
        _m_panel_callback_routes.resolve_dynamic_callback_token = lambda _value, kind, _chat, **_kwargs: "chosen.png" if kind == "character" else "lore.json"
        _m_panel_callback_routes.safe_character_path = lambda _name: Path("/tmp/chosen.png")
        _m_panel_callback_routes.safe_world_path = lambda _name: Path("/tmp/lore.json")
        card_content.safe_world_path = lambda _name: Path("/tmp/lore.json")
        _m_telegram.safe_world_path = lambda _name: Path("/tmp/lore.json")
        _m_panel_callback_routes.card_fields_from_file = lambda _name: {"name": "Chosen"}
        _m_panel_callback_routes.close_panel_message = lambda *_args, **_kwargs: None
        _m_panel_callback_routes.send_world_menu = lambda *_args, **_kwargs: opened_world.append(True)
        _m_groups.send_world_menu = lambda *_args, **_kwargs: opened_world.append(True)
        _m_panel_callback_routes.remove_inline_keyboard = lambda *_args, **_kwargs: None
        _m_panel_callback_routes.send_group_menu = lambda *_args, **_kwargs: opened_group.append(True)
        try:
            character_callback = self._callback("character:character-token")
            _m_panel_callback_routes.handle_character_callback(self.db, "token", character_callback, lambda *_args: None, character_callback["data"], chat_id, character_callback["message"], session, session["session_id"], None, group_service=self.group, request_context=make_test_request_context(self.db, session["session_id"], "user"))
            setup = self.group.setup_state(self.db, chat_id, session["session_id"])
            self.assertEqual(setup["stage"], "world")
            self.assertEqual(_m_memory_curator.load_session(self.db, chat_id, session["session_id"], _m_memory_curator.DEFAULT_MODEL)["character_file"], "chosen.png")
            world_callback = self._callback("world:world-token")
            _m_panel_callback_routes.handle_world_callback(self.db, "token", world_callback, lambda *_args: None, world_callback["data"], chat_id, world_callback["message"], session, session["session_id"], None, group_service=self.group, request_context=make_test_request_context(self.db, session["session_id"], "user"))
            self.assertEqual(_m_sync_core.active_world_files(_m_memory_curator.load_session(self.db, chat_id, session["session_id"], _m_memory_curator.DEFAULT_MODEL)["world_file"]), ["lore.json"])
            done_callback = self._callback("world:done")
            _m_panel_callback_routes.handle_world_callback(self.db, "token", done_callback, lambda *_args: None, done_callback["data"], chat_id, done_callback["message"], session, session["session_id"], None, group_service=self.group, request_context=make_test_request_context(self.db, session["session_id"], "user"))
        finally:
            _m_panel_callback_routes.resolve_dynamic_callback_token = original_resolve
            _m_panel_callback_routes.safe_character_path = original_char_path
            _m_panel_callback_routes.safe_world_path = original_world_path
            card_content.safe_world_path = original_canonical_world_path
            _m_telegram.safe_world_path = original_telegram_world_path
            _m_panel_callback_routes.card_fields_from_file = original_fields
            _m_panel_callback_routes.close_panel_message = original_close
            _m_panel_callback_routes.send_world_menu = original_world_menu
            _m_groups.send_world_menu = original_groups_world_menu
            _m_panel_callback_routes.remove_inline_keyboard = original_remove
            _m_panel_callback_routes.send_group_menu = original_group_menu
        self.assertTrue(opened_world)
        self.assertEqual(opened_group, [True])
        self.assertEqual(_m_session_naming.get_meta(self.db, f"group_setup:{chat_id}", ""), "")

    def test_character_cancel_clears_new_group_wizard_state(self):
        chat_id = "chat|topic:9"
        session = _m_telegram.ensure_session(self.db, chat_id, _m_memory_curator.DEFAULT_MODEL)
        _m_session_naming.set_meta(self.db, f"group_setup:{chat_id}", json.dumps({"session_id": session["session_id"], "stage": "character", "expires_at": time.time() + 600}))
        original_close = _m_session_naming.close_panel_message
        _m_session_naming.close_panel_message = lambda *_args, **_kwargs: None
        try:
            callback = self._callback("character:cancel")
            handled = _m_panel_callback_routes.handle_character_callback(self.db, "token", callback, lambda *_args: None, callback["data"], chat_id, callback["message"], session, session["session_id"], None, group_service=self.group, request_context=make_test_request_context(self.db, session["session_id"], "user"))
        finally:
            _m_session_naming.close_panel_message = original_close
        self.assertTrue(handled)
        self.assertEqual(_m_session_naming.get_meta(self.db, f"group_setup:{chat_id}", ""), "")


if __name__ == "__main__":
    unittest.main()
