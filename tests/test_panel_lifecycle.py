from pathlib import Path
import tempfile
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class PanelLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_enum_close_deletes_panel_message(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 104, session["session_id"])
        calls = []
        original_answer = rt.answer_callback
        original_request = rt.telegram_request
        rt.answer_callback = lambda *_args, **_kwargs: None
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-104",
            "from": {"id": "user-1"},
            "data": "enum:close",
            "message": {"message_id": 104, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.answer_callback = original_answer
            rt.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage"])
        self.assertIsNone(rt.panel_session_for_message(self.db, "chat", 104))

    def test_settings_text_route_opens_panel_without_mutating(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = {"name": "Test", "first_mes": "", "system_prompt": "", "description": "", "personality": "", "scenario": "", "mes_example": "", "post_history_instructions": ""}
        opened = []
        original_card = rt.card_fields_from_file
        original_menu = rt.send_settings_menu
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_settings_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/settings temperature 0.7")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_settings_menu = original_menu
        self.assertEqual(opened, [True])
        settings = rt.get_generation_settings(self.db, "chat", session["session_id"])
        self.assertEqual(settings["temperature"], 0.85)

        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        original_answer = rt.answer_callback
        original_request = rt.telegram_request
        original_send = rt.send_text
        rt.answer_callback = lambda *_args, **_kwargs: None
        try:
            for message_id, data in ((101, "enum:settings:input:temperature"), (102, "enum:preset:save"), (103, "enum:stt:language_input")):
                self.db.execute("DELETE FROM panel_sessions")
                rt.bind_panel_session(self.db, "chat", message_id, session["session_id"])
                calls = []
                rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
                rt.send_text = lambda _token, _chat_id, text: calls.append(("sendText", {"text": text})) or []
                callback = {
                    "id": f"callback-{message_id}",
                    "from": {"id": "user-1"},
                    "data": data,
                    "message": {"message_id": message_id, "chat": {"id": "chat"}},
                }
                rt.process_callback(self.db, "token", callback)
                self.assertEqual(calls[0][0], "deleteMessage", data)
                self.assertEqual(calls[1][0], "sendText", data)
                self.assertIsNone(rt.panel_session_for_message(self.db, "chat", message_id), data)
        finally:
            rt.answer_callback = original_answer
            rt.telegram_request = original_request
            rt.send_text = original_send

    def test_settings_panel_displays_current_field_values(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.update_generation_settings(self.db, "chat", session["session_id"], temperature=0.7, max_tokens=900, top_p=0.8, frequency_penalty=0.2, presence_penalty=-0.1, stop_sequences="END")
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_settings_menu("token", "chat", self.db, session["session_id"])
        finally:
            rt.telegram_request = original_request
        text = calls[0][1]["text"]
        for value in ("temperature=0.7", "max_tokens=900", "top_p=0.8", "frequency_penalty=0.2", "presence_penalty=-0.1", "stop_sequences=END"):
            self.assertIn(value, text)

    def test_settings_invalid_feedback_is_deleted_by_cancel(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.set_meta(self.db, "settings_input:chat", rt.json.dumps({"key": "temperature", "session_id": session["session_id"], "expires_at": rt.time.time() + 600, "prompt_message_ids": [90]}))
        fields = {"name": "Test", "first_mes": "", "system_prompt": "", "description": "", "personality": "", "scenario": "", "mes_example": "", "post_history_instructions": ""}
        original_card = rt.card_fields_from_file
        original_menu = rt.send_settings_menu
        original_send = rt.send_text
        original_request = rt.telegram_request
        deleted = []
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_settings_menu = lambda *_args, **_kwargs: None
        rt.send_text = lambda *_args, **_kwargs: [91]
        rt.telegram_request = lambda _token, method, payload: deleted.append((method, payload)) or {}
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "99")
            pending = rt.json.loads(rt.get_meta(self.db, "settings_input:chat", "{}"))
            self.assertEqual(pending["prompt_message_ids"], [90, 91])
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/cancel")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_settings_menu = original_menu
            rt.send_text = original_send
            rt.telegram_request = original_request
        self.assertEqual(deleted, [("deleteMessage", {"chat_id": "chat", "message_id": 90}), ("deleteMessage", {"chat_id": "chat", "message_id": 91})])

    def test_character_upload_uses_closable_guidance_panel(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 105, session["session_id"])
        calls = []
        original_answer = rt.answer_callback
        original_request = rt.telegram_request
        rt.answer_callback = lambda *_args, **_kwargs: None
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-105",
            "from": {"id": "user-1"},
            "data": "character:upload",
            "message": {"message_id": 105, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.answer_callback = original_answer
            rt.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["editMessageText"])
        self.assertEqual(calls[0][1]["message_id"], 105)
        buttons = calls[0][1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[-1][1]["callback_data"], "character:cancel")

    def test_character_cancel_deletes_panel_message(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 106, session["session_id"])
        calls = []
        original_answer = rt.answer_callback
        original_request = rt.telegram_request
        rt.answer_callback = lambda *_args, **_kwargs: None
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        callback = {
            "id": "callback-106",
            "from": {"id": "user-1"},
            "data": "character:cancel",
            "message": {"message_id": 106, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.answer_callback = original_answer
            rt.telegram_request = original_request
        self.assertEqual([method for method, _payload in calls], ["deleteMessage"])
        self.assertIsNone(rt.panel_session_for_message(self.db, "chat", 106))


    def test_owned_panel_rejects_different_user_without_closing_it(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.update_session(self.db, "chat", session["session_id"], author_note="keep me")
        rt.bind_panel_session(self.db, "chat", 107, session["session_id"], "user-1")
        answers = []
        original_answer = rt.answer_callback
        original_send = rt.send_text
        rt.answer_callback = lambda _token, _callback_id, text: answers.append(text)
        rt.send_text = lambda *_args, **_kwargs: []
        callback = {
            "id": "callback-107",
            "from": {"id": "user-2"},
            "data": "note:off",
            "message": {"message_id": 107, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.answer_callback = original_answer
            rt.send_text = original_send
        self.assertEqual(answers, ["This panel belongs to another user"])
        self.assertEqual(
            rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)["author_note"],
            "keep me",
        )
        self.assertEqual(rt.panel_owner_for_message(self.db, "chat", 107), "user-1")
        self.assertEqual(rt.panel_session_for_message(self.db, "chat", 107), session["session_id"])


if __name__ == "__main__":
    unittest.main()
