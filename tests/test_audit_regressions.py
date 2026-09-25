from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_conversation_service,
    make_test_group_service,
    make_test_input_flow_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.command_panels as _command_panels
import bridge.sqlite_store as _sqlite_store

ensure_application_extensions()

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import bridge.callbacks as _m_callbacks
import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.commands as _m_commands
import bridge.generation as _m_generation
import bridge.input_flows as _m_input_flows
import bridge.language as _m_language
import bridge.main as _m_main
import bridge.memory as _m_memory
import bridge.memory_backend as memory_backend
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.provider_transport as _m_provider_transport
import bridge.reset_panel as _m_reset_panel
import bridge.session_naming as _m_session_naming
import bridge.status_panels as _m_status_panels
import bridge.sync_core as _m_sync_core
import bridge.telegram as _m_telegram
from bridge.model_router import ModelRouter


class AuditRegressionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())

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
                return json.dumps({"choices": [{"message": {"content": "visible"}}]}).encode()

        original_urlopen = _m_provider_transport.strict_urlopen
        old_key = os.environ.get("TEST_OPENROUTER_KEY")
        old_hosts = os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        router = ModelRouter(
            load_catalog=lambda: {
                "openrouter": {
                    "transport": "openai_compatible",
                    "api_endpoint": "https://openrouter.ai/api/v1",
                    "api_key_env": "TEST_OPENROUTER_KEY",
                    "models": ["test"],
                }
            }
        )

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append(json.loads(request.data.decode()))
            return FakeResponse()

        _m_provider_transport.strict_urlopen = fake_urlopen
        os.environ["TEST_OPENROUTER_KEY"] = "test-only"
        os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "openrouter.ai"
        try:
            settings = dict(_m_sync_core.GENERATION_DEFAULTS)
            settings["reasoning_budget"] = 0
            self.assertEqual(
                _m_provider_transport.generate_provider_text(
                    router,
                    "",
                    "test",
                    [{"role": "user", "content": "hello"}],
                    settings=settings,
                    app_settings=self.app_settings_builder.build(),
                ),
                "visible",
            )
            settings["reasoning_budget"] = 1024
            self.assertEqual(
                _m_provider_transport.generate_provider_text(
                    router,
                    "",
                    "test",
                    [{"role": "user", "content": "hello"}],
                    settings=settings,
                    app_settings=self.app_settings_builder.build(),
                ),
                "visible",
            )
        finally:
            _m_provider_transport.strict_urlopen = original_urlopen
            if old_key is None:
                os.environ.pop("TEST_OPENROUTER_KEY", None)
            else:
                os.environ["TEST_OPENROUTER_KEY"] = old_key
            if old_hosts is None:
                os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
            else:
                os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = old_hosts

        self.assertEqual(captured[0]["reasoning"], {"enabled": False})
        self.assertEqual(captured[1]["reasoning"], {"max_tokens": 1024})

    def test_begin_operation_commits_prepared_marker(self):
        self.assertTrue(_m_panel_callback_routes.begin_operation(self.db, 101, "test"))

        second = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        try:
            self.assertEqual(_m_message_commands.operation_phase(second, 101), "in_progress")
            second.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('writer_probe','ok')")
            second.commit()
        finally:
            second.close()

    def test_startup_recovery_is_bounded(self):
        now = time.time()
        for index in range(200):
            self.db.execute(
                (
                    "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,p"
                    "ayload_json,state,attempts,last_error,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,'queued',0,'',?,?)"
                ),
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

        rows = _m_main.recover_jobs(self.db, recover_running=True)
        self.assertEqual(len(rows), 128)

    def test_swipe_callbacks_are_session_scoped(self):
        for callback_data in ("swipe:prev", "swipe:next", "swipe:keep", "swipe:cancel"):
            with self.subTest(callback_data=callback_data):
                self.assertTrue(_m_callbacks.is_session_scoped_panel_callback(callback_data))

    def test_transient_worker_boot_failure_requeues_scheduled_job(self):
        now = time.time()
        self.db.execute(
            (
                "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,p"
                "ayload_json,state,attempts,last_error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'scheduled',0,'',?,?)"
            ),
            (999, "chat", "session", "1", "generation", "{}", now, now),
        )
        self.db.commit()
        job_id = int(self.db.execute("SELECT job_id FROM jobs WHERE update_id=999").fetchone()[0])

        guarded = _m_main._DurableWorkerGuard(_sqlite_store._lightweight_db_connect).prepare(
            self.db,
            job_id,
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
        )
        with self.assertRaisesRegex(
            sqlite3.OperationalError,
            "database is locked",
        ):
            guarded()

        state = self.db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]
        self.assertEqual(state, "queued")

    def test_selection_commands_open_panels(self):
        _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
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
            "card": _m_message_commands.card_fields_from_file,
            "persona": _m_command_routes.send_persona_menu,
            "preset": _command_panels.send_preset_menu,
            "branch": _m_command_routes.send_swipe_menu,
        }
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _m_command_routes.send_persona_menu = lambda *_args, **_kwargs: opened.append("persona")
        _command_panels.send_preset_menu = lambda *_args, **_kwargs: opened.append("preset")
        _m_command_routes.send_swipe_menu = lambda *_args, **_kwargs: opened.append("branch")
        try:
            for command in ("/persona user", "/preset use creative", "/branch 2"):
                make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                    self.db,
                    "token",
                    "key",
                    self.app_settings_builder.default_model,
                    fields,
                    "chat",
                    command,
                )
        finally:
            _m_message_commands.card_fields_from_file = originals["card"]
            _m_command_routes.send_persona_menu = originals["persona"]
            _command_panels.send_preset_menu = originals["preset"]
            _m_command_routes.send_swipe_menu = originals["branch"]
        self.assertEqual(opened, ["persona", "preset", "branch"])
        self.assertFalse(hasattr(_m_commands, "handle_preset_command"))

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
        original_card = _m_message_commands.card_fields_from_file
        original_send = _m_command_routes.send_text
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _m_command_routes.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                fields,
                "chat",
                "/providers unknown",
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_command_routes.send_text = original_send
        self.assertEqual(sent, ["Unknown /providers action. Use /providers, /providers health, or /providers refresh."])

    def test_stscript_reset_opens_confirmation_panel(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "keep this", time.time()),
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
        original_card = _m_message_commands.card_fields_from_file
        original_panel = _command_panels.send_stscript_menu
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _command_panels.send_stscript_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                fields,
                "chat",
                "/stscript",
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _command_panels.send_stscript_menu = original_panel
        self.assertEqual(opened, [True])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_stt_language_panel_has_auto_and_user_input(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _command_panels.send_stt_language_menu(
                "token",
                "chat",
                self.db,
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_cards.send_panel_request = original_request
        callbacks = {
            button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row
        }
        self.assertIn("enum:sttlanguage:auto", callbacks)
        self.assertIn("enum:stt:language_input", callbacks)

    def test_text_commands_open_scoped_input_and_cancel_clears_it(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        original_send = _m_input_flows.send_text
        sent = []
        _m_input_flows.send_text = lambda _token, _chat, text: sent.append(text) or [101]
        try:
            _m_command_routes.start_text_action_input(
                self.db, "token", "chat", session["session_id"], "edit", "Send replacement"
            )
            self.assertIn("edit", _m_session_naming.get_meta(self.db, "text_action_input:chat", ""))
            self.assertTrue(
                make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                    self.db,
                    "token",
                    "chat",
                    session,
                    "/cancel",
                    api_key="key",
                    fields={},
                    group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                    provider_port=make_test_application_services(
                        app_settings=self.app_settings_builder.build()
                    ).provider,
                    memory_service=make_test_memory_service(),
                    persona_service=make_test_persona_service(),
                    request_context=make_test_request_context(
                        self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                    ),
                )
            )
        finally:
            _m_input_flows.send_text = original_send
        self.assertEqual(_m_session_naming.get_meta(self.db, "text_action_input:chat", ""), "")
        self.assertIn("Cancelled.", sent)

    def test_memory_databank_and_stscript_panels_expose_new_actions(self):
        calls = []
        original_request = _m_cards.send_panel_request
        original_stscript_request = _m_commands.send_panel_request

        def request_stub(_token, _method, payload):
            return calls.append(payload) or {}

        _m_cards.send_panel_request = lambda _token, _method, payload, **_kwargs: calls.append(payload) or {}
        _m_commands.send_panel_request = lambda _token, _method, payload, **_kwargs: calls.append(payload) or {}
        try:
            _command_panels.send_memory_menu(
                "token",
                "chat",
                self.db,
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
            _command_panels.send_databank_menu(
                "token",
                "chat",
                self.db,
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
            _command_panels.send_stscript_menu(
                "token",
                "chat",
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_cards.send_panel_request = original_request
            _m_commands.send_panel_request = original_stscript_request
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
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.set_meta(
            self.db,
            "stt_language_input:chat",
            json.dumps({"session_id": session["session_id"], "expires_at": time.time() + 600}),
        )
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
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_input_flows.send_voice_input_menu
        original_send = _m_input_flows.send_text
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _m_input_flows.send_voice_input_menu = lambda *_args, **_kwargs: None
        _m_input_flows.send_text = lambda *_args, **_kwargs: []
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                fields,
                "chat",
                "id",
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_input_flows.send_voice_input_menu = original_menu
            _m_input_flows.send_text = original_send
        self.assertEqual(_m_session_naming.get_meta(self.db, "stt_language:chat", ""), "id")
        self.assertEqual(_m_session_naming.get_meta(self.db, "stt_language_input:chat", ""), "")

        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
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
        original_card = _m_message_commands.card_fields_from_file
        original_send = _m_command_routes.send_text
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _m_command_routes.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                fields,
                "chat",
                "/model provider/model",
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_command_routes.send_text = original_send
        self.assertEqual(sent, ["Unknown or removed command. Use /help to see available commands."])
        self.assertEqual(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                session["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )["model_id"],
            session["model_id"],
        )

    def test_preset_panel_has_save_action(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _command_panels.send_preset_menu(
                "token",
                "chat",
                self.db,
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_cards.send_panel_request = original_request
        callbacks = {
            button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row
        }
        self.assertIn("enum:preset:save", callbacks)

    def test_preset_save_two_step_input(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
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
        _m_session_naming.set_meta(
            self.db,
            "preset_save_input:chat",
            json.dumps({"session_id": session["session_id"], "expires_at": time.time() + 600}),
        )
        original_card = _m_message_commands.card_fields_from_file
        original_menu = _m_input_flows.send_preset_menu
        original_send = _m_input_flows.send_text
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _m_input_flows.send_preset_menu = lambda *_args, **_kwargs: None
        _m_input_flows.send_text = lambda *_args, **_kwargs: []
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                fields,
                "chat",
                "creative",
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_input_flows.send_preset_menu = original_menu
            _m_input_flows.send_text = original_send
        self.assertIsNotNone(_m_commands.load_generation_preset(self.db, "chat", "creative"))
        self.assertEqual(_m_session_naming.get_meta(self.db, "preset_save_input:chat", ""), "")

        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "old conversation", time.time()),
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
        original_card = _m_message_commands.card_fields_from_file
        original_panel = _m_message_commands.send_panel_request
        original_purge = _m_memory.purge_hindsight_session
        original_reply = _m_message_commands.send_reply
        panel = []
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: fields
        _m_message_commands.send_panel_request = lambda _token, method, payload, **_kwargs: (
            panel.append((method, payload)) or {}
        )
        _m_memory.purge_hindsight_session = lambda _db, _chat_id, _session_id, *, app_settings=None: None
        _m_message_commands.send_reply = lambda _token, _chat_id, text, *_args, app_settings=None: sent.append(text)
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                fields,
                "chat",
                "/reset",
            )
            self.assertEqual(panel[0][0], "sendMessage")
            self.assertEqual(panel[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "reset:confirm")
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            _m_panel_callback_routes.reset_session(
                self.db, "token", "chat", session, operation_id=902, memory_service=make_test_memory_service()
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
            _m_message_commands.send_panel_request = original_panel
            _m_memory.purge_hindsight_session = original_purge
            _m_message_commands.send_reply = original_reply

        rows = self.db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY rowid",
            ("chat", session["session_id"]),
        ).fetchall()
        self.assertEqual(rows, [])
        self.assertEqual(sent, [])
        self.assertEqual(_m_message_commands.operation_phase(self.db, 902), "applied")

    def test_reset_confirmation_panel_has_destructive_confirm_and_cancel(self):
        method, payload = _m_reset_panel.reset_confirmation_request("chat")
        self.assertEqual(method, "sendMessage")
        expected = (
            "Reset active session and purge its memory?\n\nThis will:\n• Reset only the "
            "active session conversation.\n• Delete Hindsight memories for this active "
            "session only.\n• Delete session SQLite data, and session documents.\n\nThis "
            "cannot be undone."
        )
        self.assertEqual(payload["text"], expected)
        markup = payload["reply_markup"]["inline_keyboard"]
        callbacks = {button["callback_data"] for row in markup for button in row}
        self.assertEqual(callbacks, {"reset:confirm", "reset:cancel"})

    def test_reset_uses_session_scoped_purge_not_whole_bank(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        calls = []
        original_purge = _m_memory.purge_hindsight_session
        original_reply = _m_message_commands.send_text
        _m_memory.purge_hindsight_session = lambda _db, chat_id, session_id, *, app_settings=None: calls.append(
            (chat_id, session_id)
        )
        _m_message_commands.send_text = lambda *_args, **_kwargs: None
        try:
            _m_panel_callback_routes.reset_session(
                self.db,
                "token",
                "chat",
                session,
                memory_service=make_test_memory_service(
                    purge_session_memory=_m_memory.purge_hindsight_session,
                ),
            )
        finally:
            _m_memory.purge_hindsight_session = original_purge
            _m_message_commands.send_text = original_reply
        self.assertEqual(calls, [("chat", session["session_id"])])

    def test_response_language_is_added_to_prompt(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.update_session(self.db, "chat", session["session_id"], response_language="en")
        session = _m_memory_curator.load_session(
            self.db,
            "chat",
            session["session_id"],
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
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

        messages = _m_message_commands.build_chat_messages(
            session,
            fields,
            "Halo",
            [],
            persona_service=make_test_persona_service(),
            app_settings=self.app_settings_builder.build(),
        )

        system = messages[0]["content"]
        self.assertIn("selected output language is English (en)", system)
        self.assertIn("MUST write all visible response text in English", system)
        self.assertGreater(system.index("## Mandatory response language"), system.index("## Final instruction"))
        self.assertTrue(system.endswith(_m_generation.response_language_instruction("en")))
        self.assertIn(
            "MUST write all visible response text in Bahasa Indonesia",
            _m_generation.response_language_instruction("id"),
        )
        self.assertEqual(messages[-2]["role"], "system")
        self.assertIn("## Runtime output constraint", messages[-2]["content"])
        self.assertIn("MUST write all visible response text in English", messages[-2]["content"])
        self.assertEqual(messages[-1]["role"], "user")

    def test_hindsight_recall_is_hard_session_scoped(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.set_meta(self.db, "memory_scope:chat", "user")
        calls = []

        class FakeClient:
            def recall(self, **kwargs):
                calls.append(kwargs)
                return type("Result", (), {"results": []})()

        original_client = memory_backend.hindsight_client
        memory_backend.hindsight_client = lambda *, app_settings: FakeClient()
        try:
            self.assertEqual(_m_status_panels.memory_scope(self.db, "chat"), "session")
            self.assertEqual(
                _m_memory.recall_memory_results(
                    self.db, "chat", session, "old fact", "Test", app_settings=self.app_settings_builder.build()
                ),
                [],
            )
        finally:
            memory_backend.hindsight_client = original_client
        self.assertEqual(calls[0]["tags"], [f"session:{session['session_id']}"])
        self.assertEqual(calls[0]["tags_match"], "any_strict")

    def test_memory_scope_panel_is_removed_and_search_keeps_text_input(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _command_panels.send_memory_menu(
                "token",
                "chat",
                self.db,
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_cards.send_panel_request = original_request
        payload = calls[0][1]
        callbacks = {button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row}
        self.assertNotIn("enum:memory:scope", callbacks)
        self.assertIn("active session only (fixed)", payload["text"])

        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        sent = []
        original_recall = _m_memory.recall_memory_results
        _m_memory.recall_memory_results = lambda *_args, app_settings=None, **_kwargs: [
            type("Result", (), {"text": "session fact"})()
        ]
        try:
            _command_panels.handle_memory_command(
                self.db,
                "token",
                "chat",
                session,
                {"name": "Test"},
                "/memory search session fact",
                send_text_fn=lambda _token, _chat_id, text: sent.append(text),
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            _m_memory.recall_memory_results = original_recall
        self.assertEqual(sent, ["Recalled memories:\n- session fact"])

    def test_response_language_validation_and_pagination(self):
        self.assertEqual(_m_sync_core.normalize_response_language("bahasa indonesia"), "id")
        with self.assertRaises(ValueError):
            _m_sync_core.normalize_response_language("xx")

        first_page = _m_language.language_menu_markup("auto", 0)["inline_keyboard"]
        selectable = [
            row[0]["callback_data"]
            for row in first_page
            if row
            and row[0]["callback_data"].startswith("language:")
            and row[0]["callback_data"] != "language:cancel"
            and not row[0]["callback_data"].startswith("language:page:")
        ]
        self.assertEqual(len(selectable), 8)


if __name__ == "__main__":
    unittest.main()
