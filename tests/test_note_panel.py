from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class NotePanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

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
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_note_menu("token", "chat", "existing note")
        finally:
            rt.telegram_request = original_request
        self.assertIn("note:off", {b["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for b in row})
        self.assertIn("note:input", {b["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for b in row})
        self.assertIn("Author's Note — on", calls[0][1]["text"])

    def test_note_text_command_opens_panel_without_mutating(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = self._fields()
        opened = []
        original_card = rt.card_fields_from_file
        original_menu = rt.send_note_menu
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_note_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/note new text")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_note_menu = original_menu
        self.assertEqual(opened, [True])
        self.assertEqual(rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)["author_note"], "")

    def test_note_user_input_updates_session_and_expires_state(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = self._fields()
        rt.set_meta(self.db, "note_input:chat", rt.json.dumps({"session_id": session["session_id"], "expires_at": rt.time.time() + 600, "prompt_message_ids": [90]}))
        original_card = rt.card_fields_from_file
        original_menu = rt.send_note_menu
        original_send = rt.send_text
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_note_menu = lambda *_args, **_kwargs: None
        rt.send_text = lambda *_args, **_kwargs: []
        original_request = rt.telegram_request
        deleted = []
        rt.telegram_request = lambda _token, method, payload: deleted.append((method, payload)) or {}
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "remember this", operation_id=701)
        finally:
            rt.card_fields_from_file = original_card
            rt.send_note_menu = original_menu
            rt.send_text = original_send
            rt.telegram_request = original_request
        loaded = rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)
        self.assertEqual(loaded["author_note"], "remember this")
        self.assertEqual(rt.get_meta(self.db, "note_input:chat", ""), "")
        self.assertEqual(rt.operation_phase(self.db, 701), "applied")
        self.assertEqual(deleted, [("deleteMessage", {"chat_id": "chat", "message_id": 90})])

    def test_note_cancel_deletes_text_prompt(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = self._fields()
        rt.set_meta(self.db, "note_input:chat", rt.json.dumps({"session_id": session["session_id"], "expires_at": rt.time.time() + 600, "prompt_message_ids": [90]}))
        original_card = rt.card_fields_from_file
        original_menu = rt.send_note_menu
        original_send = rt.send_text
        original_request = rt.telegram_request
        deleted = []
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_note_menu = lambda *_args, **_kwargs: None
        rt.send_text = lambda *_args, **_kwargs: []
        rt.telegram_request = lambda _token, method, payload: deleted.append((method, payload)) or {}
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/cancel")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_note_menu = original_menu
            rt.send_text = original_send
            rt.telegram_request = original_request
        self.assertEqual(deleted, [("deleteMessage", {"chat_id": "chat", "message_id": 90})])
        self.assertEqual(rt.get_meta(self.db, "note_input:chat", ""), "")

    def test_note_user_input_closes_original_panel(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 78, session["session_id"])
        calls = []
        original_answer = rt.answer_callback
        original_request = rt.telegram_request
        original_send = rt.send_text
        rt.answer_callback = lambda *_args, **_kwargs: None
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        rt.send_text = lambda _token, _chat_id, text: calls.append(("sendText", {"text": text})) or [90]
        callback = {
            "id": "callback-2",
            "from": {"id": "user-1"},
            "data": "note:input",
            "message": {"message_id": 78, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.answer_callback = original_answer
            rt.telegram_request = original_request
            rt.send_text = original_send
        self.assertEqual(calls[0][0], "deleteMessage")
        self.assertEqual(calls[0][1]["message_id"], 78)
        self.assertEqual(calls[1][0], "sendText")
        pending = rt.json.loads(rt.get_meta(self.db, "note_input:chat", "{}"))
        self.assertEqual(pending["prompt_message_ids"], [90])
        self.assertIsNone(rt.panel_session_for_message(self.db, "chat", 78))

    def test_note_cancel_closes_previous_panel(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 77, session["session_id"])
        calls = []
        original_answer = rt.answer_callback
        original_request = rt.telegram_request
        rt.answer_callback = lambda *_args, **_kwargs: None
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-1",
            "from": {"id": "user-1"},
            "data": "note:cancel",
            "message": {"message_id": 77, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.answer_callback = original_answer
            rt.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage"])
        self.assertEqual(calls[0][1]["message_id"], 77)
        self.assertIsNone(rt.panel_session_for_message(self.db, "chat", 77))

    def test_note_close_uses_valid_marker_when_delete_is_rejected(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 79, session["session_id"])
        calls = []
        original_request = rt.telegram_request
        def request(_token, method, payload):
            calls.append((method, payload))
            if method == "deleteMessage":
                raise RuntimeError("delete rejected")
            return {}
        rt.telegram_request = request
        try:
            rt.close_panel_message("token", "chat", {"message_id": 79, "chat": {"id": "chat"}})
        finally:
            rt.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage", "editMessageText"])
        self.assertEqual(calls[0][1]["message_id"], 79)
        self.assertEqual(calls[1][1]["message_id"], 79)
        self.assertEqual(calls[1][1]["text"], "Panel closed.")
        self.assertIsNone(rt.panel_session_for_message(self.db, "chat", 79))

    def test_removed_authornote_alias_does_not_generate(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = self._fields()
        sent = []
        original_card = rt.card_fields_from_file
        original_send = rt.send_text
        original_generate = rt.generate_text
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.generate_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("removed alias must not generate"))
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/authornote old text")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_text = original_send
            rt.generate_text = original_generate
        self.assertEqual(sent, ["Unknown or removed command. Use /help to see available commands."])
        self.assertEqual(rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)["author_note"], "")

    def test_note_is_session_scoped(self):
        self.assertTrue(rt.is_session_scoped_panel_callback("note:off"))
        self.assertTrue(rt.is_session_scoped_panel_callback("note:input"))


if __name__ == "__main__":
    unittest.main()
