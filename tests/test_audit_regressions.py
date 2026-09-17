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

    def test_openrouter_reasoning_zero_is_explicitly_disabled(self):
        captured = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return rt.json.dumps({"choices": [{"message": {"content": "visible"}}]}).encode()

        original_resolve = rt.resolve_provider_model
        original_spec = rt.get_provider_spec
        original_urlopen = rt.strict_urlopen
        old_key = rt.os.environ.get("TEST_OPENROUTER_KEY")
        old_hosts = rt.os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        rt.resolve_provider_model = lambda _model: ("openrouter", "test/model")
        rt.get_provider_spec = lambda _provider: {
            "transport": "openai_compatible",
            "api_endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "TEST_OPENROUTER_KEY",
        }

        def fake_urlopen(request, timeout):
            captured.append(rt.json.loads(request.data.decode()))
            return FakeResponse()

        rt.strict_urlopen = fake_urlopen
        rt.os.environ["TEST_OPENROUTER_KEY"] = "test-only"
        rt.os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "openrouter.ai"
        try:
            settings = dict(rt.GENERATION_DEFAULTS)
            settings["reasoning_budget"] = 0
            self.assertEqual(rt.generate_text("", "test", [{"role": "user", "content": "hello"}], settings=settings), "visible")
            settings["reasoning_budget"] = 1024
            self.assertEqual(rt.generate_text("", "test", [{"role": "user", "content": "hello"}], settings=settings), "visible")
        finally:
            rt.resolve_provider_model = original_resolve
            rt.get_provider_spec = original_spec
            rt.strict_urlopen = original_urlopen
            if old_key is None:
                rt.os.environ.pop("TEST_OPENROUTER_KEY", None)
            else:
                rt.os.environ["TEST_OPENROUTER_KEY"] = old_key
            if old_hosts is None:
                rt.os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
            else:
                rt.os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = old_hosts

        self.assertEqual(captured[0]["reasoning"], {"enabled": False})
        self.assertEqual(captured[1]["reasoning"], {"max_tokens": 1024})

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
        original_panel = rt.send_stscript_menu
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_stscript_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/stscript")
        finally:
            rt.card_fields_from_file = original_card
            rt.send_stscript_menu = original_panel
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

    def test_text_commands_open_scoped_input_and_cancel_clears_it(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        original_send = rt.send_text
        sent = []
        rt.send_text = lambda _token, _chat, text: sent.append(text) or [101]
        try:
            rt.start_text_action_input(self.db, "token", "chat", session["session_id"], "edit", "Send replacement")
            self.assertIn("edit", rt.get_meta(self.db, "text_action_input:chat", ""))
            self.assertTrue(rt.handle_pending_input(self.db, "token", "chat", session, "/cancel", api_key="key", fields={}))
        finally:
            rt.send_text = original_send
        self.assertEqual(rt.get_meta(self.db, "text_action_input:chat", ""), "")
        self.assertIn("Cancelled.", sent)

    def test_memory_databank_and_stscript_panels_expose_new_actions(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, _method, payload: calls.append(payload) or {}
        try:
            rt.send_memory_menu("token", "chat", self.db)
            rt.send_databank_menu("token", "chat", self.db)
            rt.send_stscript_menu("token", "chat")
        finally:
            rt.telegram_request = original_request
        callbacks = {
            button["callback_data"]
            for payload in calls
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        }
        self.assertIn("enum:memory:search", callbacks)
        self.assertIn("enum:rag:search", callbacks)
        self.assertIn("enum:stscript:reset", callbacks)
        self.assertNotIn("enum:stscript:note", callbacks)

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
        original_purge = rt.purge_hindsight_session
        original_reply = rt.send_reply
        original_generate = rt.generate_text
        panel = []
        rt.card_fields_from_file = lambda _filename: fields
        rt.send_reset_confirmation_menu = lambda *_args, **_kwargs: panel.append(True)
        rt.purge_hindsight_session = lambda _db, _chat_id, _session_id: None
        rt.send_reply = lambda _token, _chat_id, text, *_args: sent.append(text)
        rt.generate_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reset must not generate"))
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, fields, "chat", "/reset")
            self.assertEqual(panel, [True])
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            rt.reset_session(self.db, "token", "chat", session, fields, operation_id=902)
        finally:
            rt.card_fields_from_file = original_card
            rt.send_reset_confirmation_menu = original_panel
            rt.purge_hindsight_session = original_purge
            rt.send_reply = original_reply
            rt.generate_text = original_generate

        rows = self.db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY rowid",
            ("chat", session["session_id"]),
        ).fetchall()
        self.assertEqual(rows, [])
        self.assertEqual(sent, [])
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
        self.assertIn("Reset active session and purge its memory?", calls[0][1]["text"])
        self.assertIn("active session only", calls[0][1]["text"])
        self.assertIn("Preserve Hindsight memories from other sessions", calls[0][1]["text"])
        self.assertIn("no character opening greeting will be sent", calls[0][1]["text"])
        self.assertNotIn("Send the character opening greeting", calls[0][1]["text"])
        markup = calls[0][1]["reply_markup"]["inline_keyboard"]
        callbacks = {button["callback_data"] for row in markup for button in row}
        self.assertEqual(callbacks, {"reset:confirm", "reset:cancel"})

    def test_reset_uses_session_scoped_purge_not_whole_bank(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        fields = {"name": "Test", "first_mes": ""}
        calls = []
        original_purge = rt.purge_hindsight_session
        original_reply = rt.send_text
        rt.purge_hindsight_session = lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        rt.send_text = lambda *_args, **_kwargs: None
        try:
            rt.reset_session(self.db, "token", "chat", session, fields)
        finally:
            rt.purge_hindsight_session = original_purge
            rt.send_text = original_reply
        self.assertEqual(calls, [("chat", session["session_id"])])


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
            "post_history_instructions": "An earlier character-card instruction.",
        }

        messages = rt.build_chat_messages(session, fields, "Halo", [])

        system = messages[0]["content"]
        self.assertIn("selected output language is English (en)", system)
        self.assertIn("MUST write all visible response text in English", system)
        self.assertGreater(system.index("## Mandatory response language"), system.index("## Final instruction"))
        self.assertTrue(system.endswith(rt.response_language_instruction("en")))
        self.assertIn("MUST write all visible response text in Bahasa Indonesia", rt.response_language_instruction("id"))
        self.assertEqual(messages[-2]["role"], "system")
        self.assertIn("## Runtime output constraint", messages[-2]["content"])
        self.assertIn("MUST write all visible response text in English", messages[-2]["content"])
        self.assertEqual(messages[-1]["role"], "user")

    def test_hindsight_recall_is_hard_session_scoped(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.set_meta(self.db, "memory_scope:chat", "user")
        calls = []

        class FakeClient:
            def recall(self, **kwargs):
                calls.append(kwargs)
                return type("Result", (), {"results": []})()

        original_client = rt.hindsight_client
        rt.hindsight_client = FakeClient
        try:
            self.assertEqual(rt.memory_scope(self.db, "chat"), "session")
            self.assertEqual(rt.recall_memory_results(self.db, "chat", session, "old fact", "Test"), [])
        finally:
            rt.hindsight_client = original_client
        self.assertEqual(calls[0]["tags"], [f"session:{session['session_id']}"])
        self.assertEqual(calls[0]["tags_match"], "any_strict")

    def test_memory_scope_panel_is_removed_and_search_keeps_text_input(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_memory_menu("token", "chat", self.db)
        finally:
            rt.telegram_request = original_request
        payload = calls[0][1]
        callbacks = {button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row}
        self.assertNotIn("enum:memory:scope", callbacks)
        self.assertIn("active session only (fixed)", payload["text"])

        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        sent = []
        original_send = rt.send_text
        original_recall = rt.recall_memory_results
        rt.send_text = lambda _token, _chat_id, text: sent.append(text)
        rt.recall_memory_results = lambda *_args, **_kwargs: [type("Result", (), {"text": "session fact"})()]
        try:
            rt.handle_memory_command(self.db, "token", "chat", session, {"name": "Test"}, "/memory search session fact")
        finally:
            rt.send_text = original_send
            rt.recall_memory_results = original_recall
        self.assertEqual(sent, ["Recalled memories:\n- session fact"])

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

if __name__ == "__main__":
    unittest.main()
