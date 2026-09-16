from pathlib import Path
import sqlite3
import tempfile
import unittest

import bridge.runtime as rt


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_begin_operation_commits_prepared_marker(self):
        self.assertTrue(rt.begin_operation(self.db, 101, "test"))

        second = rt.db_connect()
        try:
            self.assertEqual(rt.operation_phase(second, 101), "in_progress")
            second.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('writer_probe','ok')"
            )
            second.commit()
        finally:
            second.close()

    def test_startup_recovery_is_bounded(self):
        now = rt.time.time()
        for index in range(200):
            self.db.execute(
                "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,attempts,last_error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'queued',0,'',?,?)",
                (
                    1000 + index,
                    "chat",
                    "session",
                    str(index),
                    "generation",
                    "{}",
                    now + index * 0.001,
                    now + index * 0.001,
                ),
            )
        self.db.commit()

        rows = rt.recover_jobs(self.db, recover_running=True)
        self.assertEqual(len(rows), 128)

    def test_swipe_callbacks_are_session_scoped(self):
        for callback_data in ("swipe:prev", "swipe:next", "swipe:keep", "swipe:cancel"):
            with self.subTest(callback_data=callback_data):
                self.assertTrue(rt.is_session_scoped_panel_callback(callback_data))

    def test_transient_worker_boot_failure_requeues_scheduled_job(self):
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,attempts,last_error,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'scheduled',0,'',?,?)",
            (999, "chat", "session", "1", "generation", "{}", now, now),
        )
        self.db.commit()
        job_id = int(
            self.db.execute("SELECT job_id FROM jobs WHERE update_id=999").fetchone()[0]
        )

        rt._requeue_worker_boot_failure(
            job_id, sqlite3.OperationalError("database is locked")
        )

        state = self.db.execute(
            "SELECT state FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        self.assertEqual(state, "queued")

    def test_selection_commands_open_panels(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = {
            "name": "Test",
            "first_mes": "",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        opened = []
        originals = {
            "card": rt.card_fields_from_file,
            "persona": rt.send_persona_menu,
            "preset": rt.send_preset_menu,
            "branch": rt.send_swipe_menu,
        }
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_persona_menu = lambda *_args, **_kwargs: opened.append("persona")
        rt.send_preset_menu = lambda *_args, **_kwargs: opened.append("preset")
        rt.send_swipe_menu = lambda *_args, **_kwargs: opened.append("branch")
        try:
            for command in ("/persona punto", "/preset use creative", "/branch 2"):
                rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", command)
        finally:
            rt.card_fields_from_file = originals["card"]
            rt.send_persona_menu = originals["persona"]
            rt.send_preset_menu = originals["preset"]
            rt.send_swipe_menu = originals["branch"]
        self.assertEqual(opened, ["persona", "preset", "branch"])
        self.assertFalse(hasattr(rt, "handle_preset_command"))

    def test_unknown_provider_action_does_not_generate(self):
        fields = {
            "name": "Test",
            "first_mes": "",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        sent = []
        original_card = rt.card_fields_from_file
        original_send = rt.send_text
        original_generate = rt.generate_text
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.generate_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unknown provider action must not generate"))
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/providers unknown")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_text = original_send
            rt.generate_text = original_generate
        self.assertEqual(sent, ["Unknown /providers action. Use /providers, /providers health, or /providers refresh."])

    def test_stscript_reset_opens_confirmation_panel(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "keep this", rt.time.time()),
        )
        self.db.commit()
        fields = {
            "name": "Test",
            "first_mes": "Hello",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        opened = []
        original_card = rt.card_fields_from_file
        original_panel = rt.send_reset_confirmation_menu
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_reset_confirmation_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/stscript reset")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_reset_confirmation_menu = original_panel
        self.assertEqual(opened, [True])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_stt_language_panel_has_auto_and_user_input(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_stt_language_menu("token", "chat", self.db)
        finally:
            rt.telegram_request = original_request
        callbacks = {button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertIn("enum:sttlanguage:auto", callbacks)
        self.assertIn("enum:stt:language_input", callbacks)

    def test_stt_language_user_input_is_session_scoped(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.set_meta(self.db, "stt_language_input:chat", rt.json.dumps({"session_id": session["session_id"], "expires_at": rt.time.time() + 600}))
        fields = {
            "name": "Test",
            "first_mes": "",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        original_card = rt.card_fields_from_file
        original_menu = rt.send_voice_input_menu
        original_send = rt.send_text
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_voice_input_menu = lambda *_args, **_kwargs: None
        rt.send_text = lambda *_args, **_kwargs: []
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "id")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_voice_input_menu = original_menu
            rt.send_text = original_send
        self.assertEqual(rt.get_meta(self.db, "stt_language:chat", ""), "id")
        self.assertEqual(rt.get_meta(self.db, "stt_language_input:chat", ""), "")

        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = {
            "name": "Test",
            "first_mes": "",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        sent = []
        original_card = rt.card_fields_from_file
        original_send = rt.send_text
        original_generate = rt.generate_text
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.generate_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("removed command must not generate"))
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/model provider/model")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_text = original_send
            rt.generate_text = original_generate
        self.assertEqual(sent, ["Unknown or removed command. Use /help to see available commands."])
        self.assertEqual(rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)["model_id"], session["model_id"])

    def test_preset_panel_has_save_action(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_preset_menu("token", "chat", self.db)
        finally:
            rt.telegram_request = original_request
        callbacks = {button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertIn("enum:preset:save", callbacks)

    def test_preset_save_two_step_input(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = {
            "name": "Test",
            "first_mes": "",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        rt.set_meta(self.db, "preset_save_input:chat", rt.json.dumps({"session_id": session["session_id"], "expires_at": rt.time.time() + 600}))
        original_card = rt.card_fields_from_file
        original_menu = rt.send_preset_menu
        original_send = rt.send_text
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_preset_menu = lambda *_args, **_kwargs: None
        rt.send_text = lambda *_args, **_kwargs: []
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "creative")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_preset_menu = original_menu
            rt.send_text = original_send
        self.assertIsNotNone(rt.load_generation_preset(self.db, "chat", "creative"))
        self.assertEqual(rt.get_meta(self.db, "preset_save_input:chat", ""), "")

        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "old conversation", rt.time.time()),
        )
        self.db.commit()
        fields = {
            "name": "Test",
            "first_mes": "Hello, {{user}}!",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "post_history_instructions": "",
        }
        sent = []
        original_card = rt.card_fields_from_file
        original_panel = rt.send_reset_confirmation_menu
        original_purge = rt.purge_hindsight_bank
        original_reply = rt.send_reply
        original_generate = rt.generate_text
        panel = []
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_reset_confirmation_menu = lambda *_args, **_kwargs: panel.append(True)
        rt.purge_hindsight_bank = lambda _chat_id: None
        rt.send_reply = lambda _token, _chat_id, text, *_args: sent.append(text)
        rt.generate_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reset must not generate"))
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/reset")
            self.assertEqual(panel, [True])
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            rt.reset_session_to_greeting(self.db, "token", "chat", session, fields, operation_id=902)
        finally:
            rt.card_fields_from_file = original_card
            rt.send_reset_confirmation_menu = original_panel
            rt.purge_hindsight_bank = original_purge
            rt.send_reply = original_reply
            rt.generate_text = original_generate

        rows = self.db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY rowid",
            ("chat", session["session_id"]),
        ).fetchall()
        self.assertEqual(rows, [("assistant", "Hello, Punto!")])
        self.assertEqual(sent, ["Hello, Punto!"])
        self.assertEqual(rt.operation_phase(self.db, 902), "applied")

    def test_reset_confirmation_panel_has_destructive_confirm_and_cancel(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_reset_confirmation_menu("token", "chat")
        finally:
            rt.telegram_request = original_request
        self.assertEqual(len(calls), 1)
        self.assertIn("Reset active session and purge chat memory?", calls[0][1]["text"])
        self.assertIn("including other sessions", calls[0][1]["text"])
        markup = calls[0][1]["reply_markup"]["inline_keyboard"]
        callbacks = {button["callback_data"] for row in markup for button in row}
        self.assertEqual(callbacks, {"reset:confirm", "reset:cancel"})

    def test_reset_purge_deletes_and_recreates_chat_bank(self):
        calls = []

        class FakeClient:
            def delete_bank(self, **kwargs):
                calls.append(("delete", kwargs))

            def create_bank(self, **kwargs):
                calls.append(("create", kwargs))

        original_client = rt.hindsight_client
        rt.hindsight_client = FakeClient
        try:
            rt.purge_hindsight_bank("chat")
        finally:
            rt.hindsight_client = original_client
        self.assertEqual([name for name, _kwargs in calls], ["delete", "create"])
        self.assertEqual(calls[0][1]["bank_id"], calls[1][1]["bank_id"])


    def test_response_language_is_added_to_prompt(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.update_session(self.db, "chat", session["session_id"], response_language="en")
        session = rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)
        fields = {
            "name": "Test",
            "system_prompt": "",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "first_mes": "",
            "post_history_instructions": "",
        }

        messages = rt.build_chat_messages(session, fields, "Halo", [])

        self.assertIn("Reply in English (en)", messages[0]["content"])

    def test_response_language_validation_and_pagination(self):
        self.assertEqual(rt.normalize_response_language("bahasa indonesia"), "id")
        with self.assertRaises(ValueError):
            rt.normalize_response_language("xx")

        first_page = rt.language_menu_markup("auto", 0)["inline_keyboard"]
        selectable = [
            row[0]["callback_data"]
            for row in first_page
            if row and row[0]["callback_data"].startswith("language:")
            and row[0]["callback_data"] != "language:cancel"
            and not row[0]["callback_data"].startswith("language:page:")
        ]
        self.assertEqual(len(selectable), 8)

        captured = {}
        original = rt._ORIGINAL_EXPORT_SESSION
        context_token = rt._OPERATION_CONTEXT.set(88)
        try:
            def fake_export(token, db, session, fields, chat_id, operation_id=None):
                captured["operation_id"] = operation_id
                return "ok"

            rt._ORIGINAL_EXPORT_SESSION = fake_export
            result = rt.export_session("token", self.db, {}, {}, "chat")
        finally:
            rt._ORIGINAL_EXPORT_SESSION = original
            rt._OPERATION_CONTEXT.reset(context_token)

        self.assertEqual(result, "ok")
        self.assertEqual(captured["operation_id"], 88)

    def test_import_replay_does_not_duplicate_messages_or_variants(self):
        original_parser = rt.parse_sillytavern_jsonl
        rt.parse_sillytavern_jsonl = lambda _raw: (
            {"name": "Imported regression chat"},
            [("user", "hello"), ("assistant", "world")],
        )
        try:
            rt.import_chat_session(
                self.db,
                "chat",
                b"{}",
                rt.DEFAULT_MODEL,
                operation_id=77,
            )
            session_id = "import-job-77"
            message_count = self.db.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                ("chat", session_id),
            ).fetchone()[0]
            variant_count = self.db.execute(
                "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                ("chat", session_id),
            ).fetchone()[0]
            self.assertEqual(message_count, 2)
            self.assertEqual(variant_count, 1)

            self.db.execute(
                "UPDATE meta SET value='created' WHERE key='import_phase:77'"
            )
            self.db.commit()
            rt.import_chat_session(
                self.db,
                "chat",
                b"{}",
                rt.DEFAULT_MODEL,
                operation_id=77,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                    ("chat", session_id),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                    ("chat", session_id),
                ).fetchone()[0],
                1,
            )

            self.db.execute(
                "UPDATE meta SET value='summary_loaded' WHERE key='import_phase:77'"
            )
            self.db.commit()
            rt.import_chat_session(
                self.db,
                "chat",
                b"{}",
                rt.DEFAULT_MODEL,
                operation_id=77,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                    ("chat", session_id),
                ).fetchone()[0],
                1,
            )
        finally:
            rt.parse_sillytavern_jsonl = original_parser


if __name__ == "__main__":
    unittest.main()
