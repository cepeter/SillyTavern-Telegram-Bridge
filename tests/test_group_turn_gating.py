from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class GroupTurnGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        rt.save_group_state(self.db, "chat", "session", {
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
        self.assertTrue(rt.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertFalse(rt.group_user_turn_allowed(self.db, "chat", "session", "user-b"))
        self.assertTrue(rt.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        state = rt.group_state(self.db, "chat", "session")
        self.assertEqual(state["turn_user_id"], "user-a")
        self.assertEqual(state["turn_users"], ["user-a", "user-b"])

    def test_owner_can_pass_turn_to_next_known_user(self):
        self.assertTrue(rt.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertFalse(rt.group_user_turn_allowed(self.db, "chat", "session", "user-b"))
        self.assertTrue(rt.claim_group_user_turn(self.db, "chat", "session", "user-a"))
        self.assertTrue(rt.pass_group_user_turn(self.db, "chat", "session", "user-a"))
        self.assertFalse(rt.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertTrue(rt.group_user_turn_allowed(self.db, "chat", "session", "user-b"))

    def test_non_manual_mode_does_not_gate_user_messages(self):
        state = rt.group_state(self.db, "chat", "session")
        state["mode"] = "round_robin"
        rt.save_group_state(self.db, "chat", "session", state)
        self.assertTrue(rt.group_user_turn_allowed(self.db, "chat", "session", "user-a"))
        self.assertTrue(rt.group_user_turn_allowed(self.db, "chat", "session", "user-b"))

    def test_turn_controls_are_visible_only_in_manual_mode(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, _method, payload: calls.append(payload) or {}
        try:
            state = rt.group_state(self.db, "chat", "session")
            state["mode"] = "manual"
            rt.save_group_state(self.db, "chat", "session", state)
            rt.send_group_menu(self.db, "token", "chat", {"session_id": "session"})
            manual_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertEqual(manual_callbacks & {"group:claim", "group:pass"}, {"group:claim", "group:pass"})
            state["mode"] = "round_robin"
            rt.save_group_state(self.db, "chat", "session", state)
            rt.send_group_menu(self.db, "token", "chat", {"session_id": "session"})
            other_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertEqual(other_callbacks & {"group:claim", "group:pass"}, set())
        finally:
            rt.telegram_request = original_request

    def test_group_panel_ignores_not_modified_response(self):
        original_request = rt.telegram_request
        rt.telegram_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified"))
        try:
            rt.send_group_menu(self.db, "token", "chat", {"session_id": "session"}, message_id=10)
        finally:
            rt.telegram_request = original_request

    def test_new_group_session_button_only_appears_in_topic(self):
        original_request = rt.telegram_request
        calls = []
        rt.telegram_request = lambda _token, _method, payload: calls.append(payload) or {}
        try:
            rt.send_group_menu(self.db, "token", "chat|topic:7", {"session_id": "session"})
            topic_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertIn("group:new_session", topic_callbacks)
            rt.send_group_menu(self.db, "token", "chat", {"session_id": "session"})
            dm_callbacks = {button["callback_data"] for row in calls[-1]["reply_markup"]["inline_keyboard"] for button in row}
            self.assertNotIn("group:new_session", dm_callbacks)
        finally:
            rt.telegram_request = original_request
    def test_group_command_is_rejected_in_direct_chat(self):
        sent = []
        original_send = rt.send_text
        rt.send_text = lambda _token, _chat, text: sent.append(text) or []
        session = {"session_id": "session", "persona_id": "", "model_id": rt.DEFAULT_MODEL, "author_note": "", "world_file": "", "system_prompt": "", "response_language": "auto"}
        try:
            handled = rt.handle_command_route(self.db, "token", "key", rt.DEFAULT_MODEL, {}, "chat", "/group", "/group", session, "session", rt.DEFAULT_MODEL, "", "Punto")
        finally:
            rt.send_text = original_send
        self.assertTrue(handled)
        self.assertEqual(sent, ["Group sessions are available only inside a Telegram Forum Topic."])

    def test_new_group_session_starts_character_wizard_in_topic(self):
        chat_id = "chat|topic:7"
        session = rt.ensure_session(self.db, chat_id, rt.DEFAULT_MODEL)
        opened = []
        original_close = rt.close_panel_message
        original_menu = rt.send_character_menu
        original_context = rt.set_panel_session_context
        original_send = rt.send_text
        rt.close_panel_message = lambda *_args, **_kwargs: None
        rt.send_character_menu = lambda _token, _chat, _character: opened.append(True)
        rt.set_panel_session_context = lambda session_id: opened.append(session_id)
        rt.send_text = lambda *_args, **_kwargs: []
        callback = {"id": "callback", "from": {"id": "user"}, "data": "group:new_session", "message": {"message_id": 10, "chat": {"id": chat_id}}}
        try:
            rt.handle_group_panel_callback(self.db, "token", chat_id, session, "group:new_session", callback["message"], sender_id="user")
            pending = rt.get_meta(self.db, f"session_name_input:{chat_id}", "")
            self.assertTrue(pending)
            rt.handle_pending_input(self.db, "token", chat_id, session, "Named Group", operation_id=77)
        finally:
            rt.close_panel_message = original_close
            rt.send_character_menu = original_menu
            rt.set_panel_session_context = original_context
            rt.send_text = original_send
        active_id = rt.get_meta(self.db, f"active_session:{chat_id}", "")
        setup = rt.group_setup_state(self.db, chat_id, active_id)
        self.assertEqual(active_id, "group-77")
        self.assertIsNotNone(setup)
        self.assertEqual(setup["stage"], "character")
        self.assertEqual(opened[-1], True)

    def test_group_wizard_chains_character_to_world_then_group(self):
        chat_id = "chat|topic:8"
        session = rt.ensure_session(self.db, chat_id, rt.DEFAULT_MODEL)
        rt.set_meta(self.db, f"group_setup:{chat_id}", rt.json.dumps({"session_id": session["session_id"], "stage": "character", "expires_at": rt.time.time() + 600}))
        original_resolve = rt.resolve_dynamic_callback_token
        original_char_path = rt.safe_character_path
        original_world_path = rt.safe_world_path
        original_fields = rt.card_fields_from_file
        original_close = rt.close_panel_message
        original_world_menu = rt.send_world_menu
        original_remove = rt.remove_inline_keyboard
        original_group_menu = rt.send_group_menu
        opened_world = []
        opened_group = []
        rt.resolve_dynamic_callback_token = lambda _value, kind, _chat: "chosen.png" if kind == "character" else "lore.json"
        rt.safe_character_path = lambda _name: Path("/tmp/chosen.png")
        rt.safe_world_path = lambda _name: Path("/tmp/lore.json")
        rt.card_fields_from_file = lambda _name: {"name": "Chosen"}
        rt.close_panel_message = lambda *_args, **_kwargs: None
        rt.send_world_menu = lambda *_args, **_kwargs: opened_world.append(True)
        rt.remove_inline_keyboard = lambda *_args, **_kwargs: None
        rt.send_group_menu = lambda *_args, **_kwargs: opened_group.append(True)
        try:
            character_callback = self._callback("character:character-token")
            rt.handle_character_callback(self.db, "token", character_callback, lambda *_args: None, character_callback["data"], chat_id, character_callback["message"], session, session["session_id"], None)
            setup = rt.group_setup_state(self.db, chat_id, session["session_id"])
            self.assertEqual(setup["stage"], "world")
            self.assertEqual(rt.load_session(self.db, chat_id, session["session_id"], rt.DEFAULT_MODEL)["character_file"], "chosen.png")
            world_callback = self._callback("world:world-token")
            rt.handle_world_callback(self.db, "token", world_callback, lambda *_args: None, world_callback["data"], chat_id, world_callback["message"], session, session["session_id"], None)
            self.assertEqual(rt.active_world_files(rt.load_session(self.db, chat_id, session["session_id"], rt.DEFAULT_MODEL)["world_file"]), ["lore.json"])
            done_callback = self._callback("world:done")
            rt.handle_world_callback(self.db, "token", done_callback, lambda *_args: None, done_callback["data"], chat_id, done_callback["message"], session, session["session_id"], None)
        finally:
            rt.resolve_dynamic_callback_token = original_resolve
            rt.safe_character_path = original_char_path
            rt.safe_world_path = original_world_path
            rt.card_fields_from_file = original_fields
            rt.close_panel_message = original_close
            rt.send_world_menu = original_world_menu
            rt.remove_inline_keyboard = original_remove
            rt.send_group_menu = original_group_menu
        self.assertTrue(opened_world)
        self.assertEqual(opened_group, [True])
        self.assertEqual(rt.get_meta(self.db, f"group_setup:{chat_id}", ""), "")

    def test_character_cancel_clears_new_group_wizard_state(self):
        chat_id = "chat|topic:9"
        session = rt.ensure_session(self.db, chat_id, rt.DEFAULT_MODEL)
        rt.set_meta(self.db, f"group_setup:{chat_id}", rt.json.dumps({"session_id": session["session_id"], "stage": "character", "expires_at": rt.time.time() + 600}))
        original_close = rt.close_panel_message
        rt.close_panel_message = lambda *_args, **_kwargs: None
        try:
            callback = self._callback("character:cancel")
            handled = rt.handle_character_callback(self.db, "token", callback, lambda *_args: None, callback["data"], chat_id, callback["message"], session, session["session_id"], None)
        finally:
            rt.close_panel_message = original_close
        self.assertTrue(handled)
        self.assertEqual(rt.get_meta(self.db, f"group_setup:{chat_id}", ""), "")


if __name__ == "__main__":
    unittest.main()
