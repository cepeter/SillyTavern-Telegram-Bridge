from pathlib import Path
import json
import tempfile
import unittest

import bridge.runtime as rt


class SessionNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        self.sent = []
        self.old_send = rt.send_text
        rt.send_text = lambda _token, _chat, text: self.sent.append(text) or []

    def tearDown(self):
        rt.send_text = self.old_send
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def _state(self, chat_id="chat"):
        return json.loads(rt.get_meta(self.db, f"session_name_input:{chat_id}", "{}"))

    def test_standard_session_is_created_only_after_valid_name(self):
        rt.start_session_name_input(self.db, "token", "chat", self.session)
        self.assertEqual(len(rt.list_sessions(self.db, "chat")), 1)
        self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "  Project   Alpha  ", operation_id=42))
        created = rt.load_session(self.db, "chat", "job-42", rt.DEFAULT_MODEL)
        self.assertEqual(created["title"], "Project Alpha")
        self.assertEqual(rt.get_meta(self.db, "active_session:chat", ""), "job-42")
        self.assertEqual(rt.get_meta(self.db, "session_name_input:chat", ""), "")

    def test_status_displays_custom_session_title_with_technical_id(self):
        rt.update_session(self.db, "chat", self.session["session_id"], title="Evening Story")
        original_card = rt.card_fields_from_file
        rt.card_fields_from_file = lambda _filename: {"name": "Test", "post_history_instructions": ""}
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, {}, "chat", "/status")
        finally:
            rt.card_fields_from_file = original_card
        self.assertIn("Session: Evening Story (default)", self.sent[-1])

    def test_invalid_name_reprompts_without_creating_session(self):
        rt.start_session_name_input(self.db, "token", "chat", self.session)
        self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "/bad", operation_id=43))
        self.assertEqual(len(rt.list_sessions(self.db, "chat")), 1)
        self.assertTrue(self._state())
        self.assertIn("cannot start with /", self.sent[-1])

    def test_cancel_leaves_no_empty_session_or_pending_character(self):
        rt.set_meta(self.db, "character_session_input:chat", json.dumps({"character_file": "Chosen.png", "character_name": "Chosen", "expires_at": rt.time.time() + 600}))
        rt.start_session_name_input(self.db, "token", "chat", self.session)
        self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", self.session, "/cancel", operation_id=44))
        self.assertEqual(len(rt.list_sessions(self.db, "chat")), 1)
        self.assertEqual(rt.get_meta(self.db, "character_session_input:chat", ""), "")

    def test_character_chain_applies_character_after_name(self):
        rt.set_meta(self.db, "character_session_input:chat", json.dumps({"character_file": "Chosen.png", "character_name": "Chosen", "expires_at": rt.time.time() + 600}))
        rt.start_session_name_input(self.db, "token", "chat", self.session)
        original_safe = rt.safe_character_path
        rt.safe_character_path = lambda name: Path("/tmp/Chosen.png") if name == "Chosen.png" else None
        try:
            rt.handle_pending_input(self.db, "token", "chat", self.session, "Chosen Story", operation_id=45)
        finally:
            rt.safe_character_path = original_safe
        created = rt.load_session(self.db, "chat", "job-45", rt.DEFAULT_MODEL)
        self.assertEqual(created["title"], "Chosen Story")
        self.assertEqual(created["character_file"], "Chosen.png")

    def test_group_session_waits_for_name_then_opens_character_stage(self):
        chat_id = "chat|topic:7"
        session = rt.ensure_session(self.db, chat_id, rt.DEFAULT_MODEL)
        old_menu = rt.send_character_menu
        opened = []
        rt.send_character_menu = lambda _token, _chat, character: opened.append(character)
        try:
            rt.start_session_name_input(self.db, "token", chat_id, session, kind="group")
            self.assertEqual(len(rt.list_sessions(self.db, chat_id)), 1)
            rt.handle_pending_input(self.db, "token", chat_id, session, "Mystery Team", operation_id=46)
        finally:
            rt.send_character_menu = old_menu
        created = rt.load_session(self.db, chat_id, "group-46", rt.DEFAULT_MODEL)
        self.assertEqual(created["title"], "Mystery Team")
        setup = rt.group_setup_state(self.db, chat_id, "group-46")
        self.assertEqual(setup["stage"], "character")
        self.assertEqual(rt.group_state(self.db, chat_id, "group-46")["title"], "Mystery Team")
        self.assertTrue(opened)

    def test_title_length_is_bounded(self):
        self.assertEqual(rt.normalize_session_title(" A   name "), "A name")
        with self.assertRaises(ValueError):
            rt.normalize_session_title("x" * 81)


if __name__ == "__main__":
    unittest.main()
