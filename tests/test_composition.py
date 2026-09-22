from application_test_setup import ensure_application_extensions

ensure_application_extensions()

from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import bridge.config as bridge_config
import argparse
import signal
import sqlite3
import time
import bridge.callbacks as _m_callbacks
import bridge.command_routes as _m_command_routes
import bridge.help as _m_help
import bridge.main as _m_main
import bridge.media as _m_media
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.card_content as _m_card_content
import bridge.cards as _m_cards
import bridge.catalog as _m_catalog
import bridge.commands as _m_commands
import bridge.common as _m_common
import bridge.database as _m_database
import bridge.group_core as _m_group_core
import bridge.input_flows as _m_input_flows
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.persona_sync as _m_persona_sync
import bridge.status_panels as _m_status_panels
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
import bridge.telegram as _m_telegram
from bridge.group_director_service import GroupDirectorService
from bridge.job_service import DurableJob, JobService, JobSubmission
from bridge.memory_service import MemoryService
from bridge.persona_service import PersonaService
from bridge.sync_service import SyncService

from bridge.composition import (
    BackgroundRuntime,
    BridgeConfig,
    BridgeServices,
    TelegramRuntime,
    build_bridge_services,
    load_bridge_config,
    validate_bridge_config,
)


class CompositionConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.character_dir = self.root / "characters"
        self.character_dir.mkdir()
        self.card = self.character_dir / "mira.png"
        self.card.write_bytes(b"card")
        self.db_file = self.root / "bridge.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def _environ(self):
        return {
            "SILLYTAVERN_TELEGRAM_BOT_TOKEN": "secret-token",
            "LLM_API_KEY": "secret-api-key",
            "SILLYTAVERN_MODEL": "provider::model",
            "SILLYTAVERN_DEFAULT_CHARACTER": "mira.png",
            "SILLYTAVERN_TELEGRAM_ALLOWED_USERS": " 100,200, 100 ,,",
        }

    def test_load_bridge_config_is_pure_and_parses_once(self):
        environ = self._environ()
        before = dict(environ)

        config = load_bridge_config(
            environ,
            character_dir=self.character_dir,
            db_file=self.db_file,
        )

        self.assertEqual(environ, before)
        self.assertEqual(config.bot_token, "secret-token")
        self.assertEqual(config.api_key, "secret-api-key")
        self.assertEqual(config.default_model, "provider::model")
        self.assertEqual(config.default_character_file, "mira.png")
        self.assertEqual(config.card_file, self.card)
        self.assertEqual(config.db_file, self.db_file)
        self.assertEqual(config.allowed_users, frozenset({"100", "200"}))

    def test_config_and_services_are_immutable_and_hide_credentials(self):
        config = load_bridge_config(
            self._environ(),
            character_dir=self.character_dir,
            db_file=self.db_file,
        )
        rendered = repr(config)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("secret-api-key", rendered)

        with self.assertRaises(FrozenInstanceError):
            config.default_model = "other"

        telegram = TelegramRuntime(
            request=lambda *_args, **_kwargs: {},
            send_text=lambda *_args, **_kwargs: None,
        )
        background = BackgroundRuntime(
            submit_chat=lambda *_args, **_kwargs: True,
            register_backlog_dispatcher=lambda _callback: None,
            begin_shutdown=lambda: None,
        )
        memory = MemoryService(
            recall_context=lambda *_args, **_kwargs: "",
            summary_for_prompt=lambda *_args, **_kwargs: "",
            summary_state=lambda *_args, **_kwargs: ("", 0),
            retain_session=lambda *_args, **_kwargs: None,
            purge_session_memory=lambda *_args, **_kwargs: 0,
        )
        group_director = object()
        persona = object()
        sync = object()
        jobs = object()
        services = build_bridge_services(
            config,
            db_factory=lambda: sqlite3.connect(":memory:"),
            telegram=telegram,
            background=background,
            group_director=group_director,
            memory=memory,
            persona=persona,
            sync=sync,
            jobs=jobs,
        )

        self.assertIs(services.config, config)
        self.assertIs(services.telegram, telegram)
        self.assertIs(services.background, background)
        self.assertIs(services.group_director, group_director)
        self.assertIs(services.memory, memory)
        self.assertIs(services.persona, persona)
        self.assertIs(services.sync, sync)
        self.assertIs(services.jobs, jobs)
        with self.assertRaises(FrozenInstanceError):
            services.telegram = telegram

    def test_build_bridge_services_requires_complete_application_graph(self):
        signature = inspect.signature(build_bridge_services)
        for name in ("jobs", "group_director", "memory", "persona", "sync"):
            self.assertEqual(
                signature.parameters[name].default,
                inspect.Parameter.empty,
                name,
            )

    def test_validate_bridge_config_rejects_missing_required_values(self):
        base = self._environ()
        cases = (
            ("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "Telegram bot token"),
            ("SILLYTAVERN_MODEL", "SILLYTAVERN_MODEL"),
            ("SILLYTAVERN_DEFAULT_CHARACTER", "SILLYTAVERN_DEFAULT_CHARACTER"),
        )
        for key, message in cases:
            with self.subTest(key=key):
                environ = dict(base)
                environ[key] = ""
                config = load_bridge_config(
                    environ,
                    character_dir=self.character_dir,
                    db_file=self.db_file,
                )
                with self.assertRaisesRegex(ValueError, message):
                    validate_bridge_config(config)

    def test_validate_bridge_config_rejects_missing_card(self):
        environ = self._environ()
        environ["SILLYTAVERN_DEFAULT_CHARACTER"] = "missing.png"
        config = load_bridge_config(
            environ,
            character_dir=self.character_dir,
            db_file=self.db_file,
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_bridge_config(config)

    def test_validate_bridge_config_accepts_valid_config(self):
        config = load_bridge_config(
            self._environ(),
            character_dir=self.character_dir,
            db_file=self.db_file,
        )
        self.assertIsNone(validate_bridge_config(config))


class DatabaseFactoryPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_db_file = bridge_config.DB_FILE

    def tearDown(self):
        bridge_config.DB_FILE = self.old_db_file
        self.tmp.cleanup()

    def test_explicit_database_paths_initialize_independently(self):
        default_path = self.root / "default.sqlite3"
        explicit_a = self.root / "a.sqlite3"
        explicit_b = self.root / "b.sqlite3"
        bridge_config.DB_FILE = default_path

        default_db = _m_memory_curator.db_connect()
        default_db.close()

        a = _m_memory_curator.db_connect(explicit_a)
        a.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('which','a')"
        )
        a.commit()
        a.close()

        b = _m_memory_curator.db_connect(explicit_b)
        self.assertIsNone(
            b.execute(
                "SELECT value FROM meta WHERE key='which'"
            ).fetchone()
        )
        b.close()

        self.assertTrue(explicit_a.is_file())
        self.assertTrue(explicit_b.is_file())

    def test_explicit_factory_does_not_mutate_global_db_file(self):
        original = bridge_config.DB_FILE
        explicit = self.root / "factory.sqlite3"
        db = _m_memory_curator.db_connect(explicit)
        db.close()
        self.assertEqual(bridge_config.DB_FILE, original)


class WorkerInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workers.sqlite3"
        self.db = _m_memory_curator.db_connect(self.db_path)
        self.db.close()
        self.opened = 0
        self.sent = []
        self.global_sent = []
        self.group_director_service = object()
        self.memory_service = object()
        self.persona_service = object()
        self.sync_service = object()

        config = BridgeConfig(
            bot_token="injected-token",
            api_key="injected-key",
            default_model="injected::model",
            default_character_file="mira.png",
            card_file=Path(self.tmp.name) / "mira.png",
            db_file=self.db_path,
            allowed_users=frozenset({"100"}),
        )
        self.services = BridgeServices(
            config=config,
            db_factory=self._db_factory,
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *args, **_kwargs: self.sent.append(args),
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
            jobs=Mock(),
            group_director=self.group_director_service,
            memory=self.memory_service,
            persona=self.persona_service,
            sync=self.sync_service,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _db_factory(self):
        self.opened += 1
        return _m_memory_curator.db_connect(self.db_path)

    def test_all_worker_signatures_receive_services_not_startup_bundle(self):
        expectations = {
            _m_main.process_message_job: (
                "services", "fields", "chat_id", "text", "message_id",
                "queued_session_id", "model_override", "job_id",
            ),
            _m_main.process_image_job: (
                "services", "chat_id", "file_id", "caption", "file_size",
                "message_id", "queued_session_id", "model_override", "job_id",
            ),
            _m_main.process_callback_job: (
                "services", "chat_id", "callback", "job_id",
            ),
            _m_main.process_edit_job: (
                "services", "chat_id", "message_id", "text",
                "model_override", "job_id",
            ),
            _m_media.process_voice_job: (
                "services", "fields", "chat_id", "voice", "message_id",
                "queued_session_id", "model_override", "job_id",
            ),
            _m_help.process_document_job: (
                "services", "chat_id", "document", "message_id",
                "model_override", "job_id",
            ),
        }
        for function, expected in expectations.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(
                    tuple(inspect.signature(function).parameters),
                    expected,
                )

    def test_message_worker_uses_injected_db_and_config(self):
        captured = {}

        def fake_process_message(
            db, token, api_key, model, fields, chat_id, text, message_id,
            **kwargs,
        ):
            captured.update(
                db=db,
                token=token,
                api_key=api_key,
                model=model,
                fields=fields,
                chat_id=chat_id,
                text=text,
                message_id=message_id,
                kwargs=kwargs,
            )

        with patch.object(_m_main, "committed_assistant_for_message", return_value=None
        ), patch.object(_m_main, "process_message", side_effect=fake_process_message
        ):
            _m_main.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
            )

        self.assertEqual(self.opened, 1)
        self.assertEqual(captured["token"], "injected-token")
        self.assertEqual(captured["api_key"], "injected-key")
        self.assertEqual(captured["model"], "injected::model")
        self.assertIs(captured["kwargs"]["services"], self.services)

    def test_process_message_propagates_injected_persona_service(self):
        captured = {}
        db = self._db_factory()
        try:
            with patch.object(_m_message_commands, "handle_pending_input",
                side_effect=lambda *_args, **kwargs:
                    captured.update(kwargs) or True,
            ):
                _m_message_commands.process_message(
                    db,
                    "injected-token",
                    "injected-key",
                    "injected::model",
                    {"name": "Mira"},
                    "chat",
                    "hello",
                    70,
                    services=self.services,
                )
        finally:
            db.close()

        self.assertIs(
            captured["persona_service"],
            self.persona_service,
        )
        self.assertIs(
            captured["memory_service"],
            self.memory_service,
        )

    def test_sync_command_propagates_injected_sync_service(self):
        captured = {}
        db = self._db_factory()
        try:
            session = _m_callbacks.ensure_session(
                db,
                "chat",
                "injected::model",
            )
            with patch.object(_m_command_routes, "send_sync_menu",
                side_effect=lambda *_args, **kwargs:
                    captured.update(kwargs),
            ):
                handled = _m_message_commands.handle_command_route(
                    db,
                    "injected-token",
                    "injected-key",
                    "injected::model",
                    {"name": "Mira"},
                    "chat",
                    "/sync",
                    "/sync",
                    session,
                    session["session_id"],
                    session["model_id"],
                    session.get("persona_id") or "",
                    "User",
                    services=self.services,
                )
        finally:
            db.close()

        self.assertTrue(handled)
        self.assertIs(
            captured["sync_service"],
            self.sync_service,
        )

    def test_process_callback_propagates_injected_sync_service(self):
        captured = {}
        db = self._db_factory()
        callback = {
            "id": "cb",
            "from": {"id": "100"},
            "data": "sync:status",
            "message": {"chat": {"id": "chat"}},
        }
        try:
            with patch.object(_m_callbacks, "handle_primary_panel_callback",
                side_effect=lambda *_args, **kwargs:
                    captured.update(kwargs) or True,
            ):
                _m_callbacks.process_callback(
                    db,
                    "injected-token",
                    callback,
                    services=self.services,
                )
        finally:
            db.close()

        self.assertIs(
            captured["sync_service"],
            self.sync_service,
        )

    def test_edit_worker_propagates_injected_memory_service(self):
        captured = {}

        def fake_edit(*args, **kwargs):
            captured.update(kwargs)

        with patch.object(_m_main, "edit_telegram_user_message",
            side_effect=fake_edit,
        ):
            _m_main.process_edit_job(
                self.services,
                "chat",
                77,
                "edited text",
            )

        self.assertIs(
            captured["memory_service"],
            self.memory_service,
        )

    def test_retry_propagates_services_to_nested_message(self):
        captured = {}
        failed = (
            42,
            "failed message",
            "stored::model",
            1,
            "provider failed",
            "failed-session",
        )
        db = self._db_factory()
        try:
            with patch.object(_m_command_routes, "latest_failed_turn",
                return_value=failed,
            ), patch.object(_m_command_routes, "committed_assistant_for_message",
                return_value=None,
            ), patch.object(_m_command_routes, "process_message",
                side_effect=lambda *_args, **kwargs: captured.update(kwargs),
            ), patch.object(_m_command_routes, "clear_failed_turn",
            ):
                handled = _m_command_routes._handle_basic(
                    db,
                    "injected-token",
                    "injected-key",
                    "injected::model",
                    {"name": "Mira"},
                    "chat",
                    "/retry",
                    "/retry",
                    {"session_id": "active-session"},
                    "active-session",
                    "injected::model",
                    "",
                    "Mira",
                    None,
                    services=self.services,
                )
        finally:
            db.close()

        self.assertTrue(handled)
        self.assertIs(captured["services"], self.services)

    def test_recovered_model_override_wins_over_config_default(self):
        captured = {}

        with patch.object(_m_main, "committed_assistant_for_message", return_value=None
        ), patch.object(_m_main, "process_message",
            side_effect=lambda _db, _token, _key, model, *_args, **_kwargs:
                captured.setdefault("model", model),
        ):
            _m_main.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                11,
                None,
                "stored::model",
            )

        self.assertEqual(captured["model"], "stored::model")

    def test_image_worker_propagates_injected_memory_service(self):
        captured = {}

        with patch.object(_m_main, "committed_assistant_for_message",
            return_value=None,
        ), patch.object(_m_main, "process_telegram_image",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ):
            _m_main.process_image_job(
                self.services,
                "chat",
                "file-id",
                "caption",
                100,
                55,
            )

        self.assertIs(
            captured["memory_service"],
            self.memory_service,
        )

    def test_document_worker_propagates_injected_memory_service(self):
        captured = {}

        with patch.object(_m_help, "import_telegram_document",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ):
            _m_help.process_document_job(
                self.services,
                "chat",
                {"file_name": "photo.png"},
                56,
            )

        self.assertIs(
            captured["memory_service"],
            self.memory_service,
        )

    def test_callback_worker_propagates_services(self):
        captured = {}
        callback = {
            "id": "callback",
            "from": {"id": "100"},
            "message": {"chat": {"id": "chat"}},
        }
        with patch.object(_m_main, "process_callback",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ):
            _m_main.process_callback_job(
                self.services,
                "chat",
                callback,
            )

        self.assertIs(captured["services"], self.services)

    def test_callback_failure_uses_injected_send_text(self):
        with patch.object(_m_main, "process_callback",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            _m_telegram,
            "send_text",
            side_effect=lambda *args, **_kwargs: self.global_sent.append(args),
        ):
            _m_main.process_callback_job(
                self.services,
                "chat",
                {"id": "callback"},
            )

        self.assertEqual(
            self.sent,
            [(
                "injected-token",
                "chat",
                "Callback processing failed; try the command again.",
            )],
        )
        self.assertEqual(self.global_sent, [])


class RecoveryCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recovery.sqlite3"
        self.db = _m_memory_curator.db_connect(self.path)
        self.submitted = []
        config = BridgeConfig(
            bot_token="token",
            api_key="key",
            default_model="current::model",
            default_character_file="mira.png",
            card_file=Path(self.tmp.name) / "mira.png",
            db_file=self.path,
            allowed_users=frozenset(),
        )
        self.background = BackgroundRuntime(
            submit_chat=self._submit,
            register_backlog_dispatcher=lambda _callback: None,
            begin_shutdown=lambda: None,
        )
        self.services = BridgeServices(
            config=config,
            db_factory=lambda: _m_memory_curator.db_connect(self.path),
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=self.background,
            jobs=Mock(),
            group_director=object(),
            memory=object(),
            persona=object(),
            sync=object(),
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _submit(self, label, chat_id, function, *args):
        self.submitted.append((label, chat_id, function, args))
        return True

    def test_recovered_job_resolver_preserves_stored_model_and_session(self):
        generation = DurableJob(
            job_id=51,
            chat_id="chat",
            session_id="stored-session",
            telegram_message_id=10,
            kind="generation",
            payload={
                "text": "hello",
                "model": "stored::model",
            },
        )
        submission = _m_main.resolve_recovered_job_submission(
            self.services,
            {"name": "Mira"},
            generation,
        )

        self.assertEqual(submission.label, "generation")
        self.assertIs(
            inspect.unwrap(submission.worker),
            _m_main.process_message_job,
        )
        self.assertIs(submission.args[0], self.services)
        self.assertEqual(submission.args[-2], "stored-session")
        self.assertEqual(submission.args[-1], "stored::model")
        self.assertNotIn(51, submission.args)

        active = DurableJob(
            job_id=52,
            chat_id="chat",
            session_id="stored-session",
            telegram_message_id=11,
            kind="generation",
            payload={
                "text": "hello",
                "model": "stored::model",
                "resolve_active": True,
            },
        )
        active_submission = _m_main.resolve_recovered_job_submission(
            self.services,
            {"name": "Mira"},
            active,
        )
        self.assertIsNone(active_submission.args[-2])

    def test_recovered_job_resolver_maps_all_worker_kinds(self):
        fields = {"name": "Mira"}
        cases = [
            (
                DurableJob(
                    61, "chat", "session", 21, "callback",
                    {"callback": {"id": "cb"}},
                ),
                _m_main.process_callback_job,
                {"id": "cb"},
            ),
            (
                DurableJob(
                    62, "chat", "session", 22, "edit",
                    {"text": "edited", "model": "stored::edit"},
                ),
                _m_main.process_edit_job,
                "stored::edit",
            ),
            (
                DurableJob(
                    63, "chat", "session", 23, "voice",
                    {
                        "voice": {"file_id": "voice"},
                        "model": "stored::voice",
                    },
                ),
                _m_media.process_voice_job,
                "stored::voice",
            ),
            (
                DurableJob(
                    64, "chat", "session", 24, "image",
                    {
                        "file_id": "image",
                        "caption": "caption",
                        "file_size": 7,
                        "model": "stored::image",
                    },
                ),
                _m_main.process_image_job,
                "stored::image",
            ),
            (
                DurableJob(
                    65, "chat", "session", 25, "document",
                    {
                        "document": {"file_name": "notes.txt"},
                        "model": "stored::document",
                    },
                ),
                _m_help.process_document_job,
                "stored::document",
            ),
        ]

        for job, worker, expected_tail in cases:
            with self.subTest(kind=job.kind):
                submission = _m_main.resolve_recovered_job_submission(
                    self.services,
                    fields,
                    job,
                )
                self.assertEqual(submission.label, job.kind)
                self.assertIs(
                    inspect.unwrap(submission.worker),
                    worker,
                )
                self.assertIs(submission.args[0], self.services)
                self.assertIn(expected_tail, submission.args)
                self.assertNotIn(job.job_id, submission.args)

        unknown = DurableJob(
            66, "chat", "session", 26, "unknown", {}
        )
        self.assertIsNone(
            _m_main.resolve_recovered_job_submission(
                self.services,
                fields,
                unknown,
            )
        )

    def test_backlog_dispatcher_uses_injected_job_recovery(self):
        opened = []
        fake_jobs = Mock()

        def factory():
            db = _m_memory_curator.db_connect(self.path)
            opened.append(db)
            return db

        services = BridgeServices(
            config=self.services.config,
            db_factory=factory,
            telegram=self.services.telegram,
            background=self.services.background,
            jobs=fake_jobs,
            group_director=self.services.group_director,
            memory=self.services.memory,
            persona=self.services.persona,
            sync=self.services.sync,
        )

        dispatcher = _m_main.make_durable_backlog_dispatcher(
            services,
            {"name": "Mira"},
        )
        dispatcher()

        self.assertEqual(len(opened), 1)
        fake_jobs.recover.assert_called_once()
        call = fake_jobs.recover.call_args
        self.assertIs(call.args[0], opened[0])
        self.assertFalse(call.kwargs["recover_running"])
        submission = call.args[1](
            DurableJob(
                68,
                "chat",
                "session",
                28,
                "generation",
                {"text": "hello"},
            )
        )
        self.assertEqual(submission.label, "generation")
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")


class RecordingJobs:
    def __init__(self, *, submit_result=True):
        self.calls = []
        self.submit_result = submit_result
        self.next_id = 700

    def enqueue(
        self,
        db,
        update_id,
        chat_id,
        session_id,
        message_id,
        kind,
        payload,
    ):
        job_id = self.next_id
        self.next_id += 1
        self.calls.append((
            "enqueue",
            update_id,
            chat_id,
            session_id,
            message_id,
            kind,
            payload,
        ))
        return job_id

    def submit(self, db, job_id, submission):
        self.calls.append(("submit", job_id, submission))
        return self.submit_result

    def recover(self, db, resolver, *, recover_running=True):
        self.calls.append((
            "recover",
            db,
            resolver,
            recover_running,
        ))
        return None


class StartupCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.card = root / "mira.png"
        self.card.write_bytes(b"card")
        self.config = BridgeConfig(
            bot_token="token",
            api_key="key",
            default_model="provider::model",
            default_character_file="mira.png",
            card_file=self.card,
            db_file=root / "bridge.sqlite3",
            allowed_users=frozenset({"100"}),
        )
        self.requests = []
        self.services = BridgeServices(
            config=self.config,
            db_factory=lambda: _m_memory_curator.db_connect(self.config.db_file),
            telegram=TelegramRuntime(
                request=self._request,
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
            jobs=Mock(),
            group_director=object(),
            memory=object(),
            persona=object(),
            sync=object(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, token, method, payload=None):
        self.requests.append((token, method, payload))
        return {"username": "bridge_bot"}

    def test_run_check_uses_prebuilt_services_without_reloading_environment(self):
        with patch.object(_m_common, "load_env_file",
            side_effect=AssertionError("run_check must not reload env"),
        ), patch.object(_m_main, "card_fields",
            return_value={"name": "Mira"},
        ), patch.object(_m_main, "read_png_chara",
            return_value={},
        ), patch.object(_m_main, "phase3_api_configured",
            return_value=False,
        ):
            self.assertEqual(_m_main.run_check(self.services), 0)

        self.assertEqual(
            self.requests,
            [("token", "getMe", None)],
        )

    def test_signal_handler_captures_injected_shutdown_callable(self):
        installed = {}
        calls = []

        def fake_signal(signum, handler):
            installed[signum] = handler

        with patch.object(signal, "signal", side_effect=fake_signal):
            _m_main.install_bridge_signal_handlers(
                lambda: calls.append("shutdown")
            )

        handler = installed[signal.SIGTERM]
        handler(signal.SIGTERM, None)
        self.assertTrue(_m_main._SHUTDOWN_EVENT.is_set())
        self.assertEqual(calls, ["shutdown"])
        _m_main._SHUTDOWN_EVENT.clear()

    def test_startup_builds_group_director_service(self):
        services = _m_main._build_startup_services(self.config)

        self.assertIsInstance(
            services.group_director,
            GroupDirectorService,
        )

    def test_startup_builds_memory_service(self):
        services = _m_main._build_startup_services(self.config)

        self.assertIsInstance(
            services.memory,
            MemoryService,
        )

    def test_startup_builds_persona_service_from_final_runtime_collaborators(self):
        with patch.object(_m_main, "load_personas",
        ) as load_personas, patch.object(_m_main, "default_persona_id",
        ) as default_persona, patch.object(_m_main, "upsert_native_persona",
        ) as upsert_persona, patch.object(_m_main, "delete_native_persona",
        ) as delete_persona, patch.object(_m_main, "update_session",
        ) as update_session:
            services = _m_main._build_startup_services(self.config)

        self.assertIsInstance(services.persona, PersonaService)
        self.assertIs(services.persona.load_personas, load_personas)
        self.assertIs(
            services.persona.load_default_persona,
            default_persona,
        )
        self.assertIs(services.persona.upsert_persona, upsert_persona)
        self.assertIs(services.persona.delete_persona, delete_persona)
        self.assertIs(
            services.persona.update_session_persona,
            update_session,
        )

    def test_startup_builds_sync_service_from_final_runtime_collaborators(self):
        with patch.object(_m_main, "sync_binding",
        ) as binding, patch.object(_m_main, "phase3_sync_now",
        ) as sync_now, patch.object(_m_main, "phase3_toggle_realtime",
        ) as toggle, patch.object(_m_main, "phase3_sync_poll",
        ) as poll, patch.object(_m_main, "_phase3_disable",
        ) as disable, patch.object(_m_main, "phase3_api_configured",
        ) as configured:
            services = _m_main._build_startup_services(self.config)

        self.assertIsInstance(services.sync, SyncService)
        self.assertIs(services.sync.load_binding, binding)
        self.assertIs(services.sync.sync_now_backend, sync_now)
        self.assertIs(services.sync.toggle_realtime_backend, toggle)
        self.assertIs(services.sync.poll_backend, poll)
        self.assertIs(services.sync.disable_realtime, disable)
        self.assertIs(services.sync.api_configured, configured)

    def test_startup_builds_job_service_from_final_job_collaborators(self):
        with patch.object(_m_main, "enqueue_job",
        ) as enqueue, patch.object(_m_main, "job_actor_id",
        ) as actor, patch.object(_m_main, "mark_job_scheduled",
        ) as scheduled, patch.object(_m_main, "mark_job_running",
        ) as running, patch.object(_m_main, "finish_job",
        ) as finish, patch.object(_m_main, "recover_jobs",
        ) as recover, patch.object(_m_main, "submit_chat_background",
        ) as submit_chat:
            services = _m_main._build_startup_services(self.config)

        self.assertIsInstance(services.jobs, JobService)
        self.assertIs(services.jobs.enqueue_backend, enqueue)
        self.assertIs(services.jobs.actor_backend, actor)
        self.assertIs(services.jobs.schedule_backend, scheduled)
        self.assertIs(services.jobs.start_backend, running)
        self.assertIs(services.jobs.finish_backend, finish)
        self.assertIs(services.jobs.recover_backend, recover)
        self.assertIs(services.jobs.submit_chat, submit_chat)
        self.assertIsNotNone(services.jobs.prepare_worker)
        self.assertIs(
            services.jobs.prepare_worker.__self__,
            _m_main._DURABLE_WORKER_GUARD,
        )

    def test_main_starts_sync_worker_with_injected_sync_service(self):
        sync_service = object()

        def request(_token, method, _payload=None):
            if method == "getUpdates":
                _m_main._SHUTDOWN_EVENT.set()
                return []
            return {}

        services = BridgeServices(
            config=self.config,
            db_factory=lambda: _m_memory_curator.db_connect(self.config.db_file),
            telegram=TelegramRuntime(
                request=request,
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
            sync=sync_service,
            jobs=Mock(),
            group_director=object(),
            memory=object(),
            persona=object(),
        )
        parsed = argparse.Namespace(check=False)

        with patch.object(
            argparse.ArgumentParser,
            "parse_args",
            return_value=parsed,
        ), patch.object(_m_main, "load_env_file"
        ), patch.object(_m_main, "refresh_phase3_config"
        ), patch.object(_m_main, "enforce_runtime_permissions"
        ), patch.object(_m_main, "configure_logging"
        ), patch.object(
            _m_main, "_load_startup_config", return_value=self.config
        ), patch.object(
            _m_main, "_build_startup_services", return_value=services
        ), patch.object(
            _m_main, "validate_startup_credential"
        ), patch.object(_m_main, "set_bot_commands"
        ), patch.object(_m_main, "read_png_chara", return_value={}
        ), patch.object(_m_main, "card_fields", return_value={"name": "Mira"}
        ), patch.object(
            _m_main, "install_bridge_signal_handlers"
        ), patch.object(_m_main, "start_phase3_sync_worker"
        ) as start_sync, patch.object(_m_main, "stop_phase3_sync_worker", return_value=True
        ), patch.object(_m_main, "shutdown_background_executors", return_value=True
        ), patch.object(_m_main, "run_database_maintenance"
        ):
            self.assertEqual(_m_main.main(), 0)

        start_sync.assert_called_once_with(
            sync_service=sync_service
        )
        services.jobs.recover.assert_called_once()
        recover_call = services.jobs.recover.call_args
        self.assertTrue(
            recover_call.kwargs["recover_running"]
        )
        _m_main._SHUTDOWN_EVENT.clear()

    def _run_one_update(self, update, *, submit_result=True):
        jobs = RecordingJobs(submit_result=submit_result)
        sent = []
        delivered = False

        def request(_token, method, _payload=None):
            nonlocal delivered
            if method == "getUpdates":
                if not delivered:
                    delivered = True
                    _m_main._SHUTDOWN_EVENT.set()
                    return [update]
                return []
            return {}

        services = BridgeServices(
            config=self.config,
            db_factory=lambda: _m_memory_curator.db_connect(self.config.db_file),
            telegram=TelegramRuntime(
                request=request,
                send_text=lambda *args, **_kwargs: sent.append(args),
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
            sync=object(),
            jobs=jobs,
            group_director=object(),
            memory=object(),
            persona=object(),
        )
        parsed = argparse.Namespace(check=False)

        try:
            with patch.object(
                argparse.ArgumentParser,
                "parse_args",
                return_value=parsed,
            ), patch.object(_m_main, "load_env_file"
            ), patch.object(_m_main, "refresh_phase3_config"
            ), patch.object(_m_main, "enforce_runtime_permissions"
            ), patch.object(_m_main, "configure_logging"
            ), patch.object(
                _m_main, "_load_startup_config", return_value=self.config
            ), patch.object(
                _m_main, "_build_startup_services", return_value=services
            ), patch.object(
                _m_main, "validate_startup_credential"
            ), patch.object(_m_main, "set_bot_commands"
            ), patch.object(_m_main, "read_png_chara", return_value={}
            ), patch.object(_m_main, "card_fields", return_value={"name": "Mira"}
            ), patch.object(
                _m_main, "install_bridge_signal_handlers"
            ), patch.object(_m_main, "start_phase3_sync_worker"
            ), patch.object(_m_main, "stop_phase3_sync_worker", return_value=True
            ), patch.object(_m_main, "shutdown_background_executors", return_value=True
            ), patch.object(_m_main, "run_database_maintenance"
            ), patch.object(_m_main, "answer_callback"
            ), patch.object(_m_main, "group_user_turn_allowed", return_value=True
            ):
                self.assertEqual(_m_main.main(), 0)
        finally:
            _m_main._SHUTDOWN_EVENT.clear()

        return jobs, sent

    def test_main_durable_paths_enqueue_and_submit_through_jobs_service(self):
        cases = [
            (
                "callback",
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": "cb",
                        "from": {"id": 100},
                        "data": "enum:status",
                        "message": {
                            "message_id": 10,
                            "chat": {"id": "chat"},
                        },
                    },
                },
                "callback",
            ),
            (
                "edit",
                {
                    "update_id": 2,
                    "edited_message": {
                        "message_id": 11,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "text": "edited",
                    },
                },
                "edit",
            ),
            (
                "voice",
                {
                    "update_id": 3,
                    "message": {
                        "message_id": 12,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "voice": {"file_id": "voice"},
                    },
                },
                "voice",
            ),
            (
                "image",
                {
                    "update_id": 4,
                    "message": {
                        "message_id": 13,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "photo": [
                            {"file_id": "photo", "file_size": 5},
                        ],
                    },
                },
                "image",
            ),
            (
                "document",
                {
                    "update_id": 5,
                    "message": {
                        "message_id": 14,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "document": {
                            "file_id": "doc",
                            "file_name": "notes.txt",
                            "mime_type": "text/plain",
                        },
                    },
                },
                "document",
            ),
            (
                "command",
                {
                    "update_id": 6,
                    "message": {
                        "message_id": 15,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "text": "/status",
                    },
                },
                "command",
            ),
            (
                "generation",
                {
                    "update_id": 7,
                    "message": {
                        "message_id": 16,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "text": "hello",
                    },
                },
                "generation",
            ),
        ]

        for label, update, expected_kind in cases:
            with self.subTest(label=label):
                jobs, _sent = self._run_one_update(update)
                enqueue_calls = [
                    call for call in jobs.calls
                    if call[0] == "enqueue"
                ]
                submit_calls = [
                    call for call in jobs.calls
                    if call[0] == "submit"
                ]
                self.assertEqual(len(enqueue_calls), 1)
                self.assertEqual(len(submit_calls), 1)
                self.assertEqual(
                    enqueue_calls[0][5],
                    expected_kind,
                )
                self.assertIsInstance(
                    submit_calls[0][2],
                    JobSubmission,
                )
                self.assertEqual(
                    submit_calls[0][2].label,
                    expected_kind,
                )

    def test_rejected_durable_admission_preserves_restart_feedback(self):
        cases = [
            (
                {
                    "update_id": 20,
                    "message": {
                        "message_id": 20,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "text": "hello",
                    },
                },
                "⏳ Message saved for generation after restart.",
            ),
            (
                {
                    "update_id": 21,
                    "message": {
                        "message_id": 21,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "photo": [
                            {"file_id": "photo", "file_size": 5},
                        ],
                    },
                },
                "🖼️ Image saved for processing after restart.",
            ),
            (
                {
                    "update_id": 22,
                    "message": {
                        "message_id": 22,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "voice": {"file_id": "voice"},
                    },
                },
                "🎙️ Voice saved for processing after restart.",
            ),
            (
                {
                    "update_id": 23,
                    "message": {
                        "message_id": 23,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "document": {
                            "file_id": "doc",
                            "file_name": "notes.txt",
                            "mime_type": "text/plain",
                        },
                    },
                },
                "📄 Document saved for processing after restart.",
            ),
            (
                {
                    "update_id": 24,
                    "message": {
                        "message_id": 24,
                        "from": {"id": 100},
                        "chat": {"id": "chat"},
                        "text": "/summarize",
                    },
                },
                "⏳ Command saved for execution after restart.",
            ),
        ]

        for update, expected in cases:
            with self.subTest(expected=expected):
                _jobs, sent = self._run_one_update(
                    update,
                    submit_result=False,
                )
                self.assertTrue(
                    any(
                        len(call) >= 3 and call[2] == expected
                        for call in sent
                    ),
                    sent,
                )

    def test_main_check_builds_services_once_and_passes_same_object(self):
        parsed = argparse.Namespace(check=True)
        with patch.object(
            argparse.ArgumentParser,
            "parse_args",
            return_value=parsed,
        ), patch.object(_m_main, "load_env_file",
        ), patch.object(_m_main, "refresh_phase3_config",
        ), patch.object(_m_main, "enforce_runtime_permissions",
        ), patch.object(_m_main, "configure_logging",
        ) as configure_logging, patch.object(
            _m_main,
            "_load_startup_config",
            return_value=self.config,
        ) as load_config, patch.object(
            _m_main,
            "_build_startup_services",
            return_value=self.services,
        ) as build_services, patch.object(
            _m_main,
            "validate_startup_credential",
        ), patch.object(_m_main, "set_bot_commands",
        ), patch.object(
            _m_main,
            "run_check",
            return_value=0,
        ) as run_check:
            self.assertEqual(_m_main.main(), 0)

        configure_logging.assert_called_once_with()
        load_config.assert_called_once()
        build_services.assert_called_once_with(self.config)
        run_check.assert_called_once_with(self.services)


class CompositionSourceBoundaryTests(unittest.TestCase):
    def test_migrated_worker_and_check_sources_do_not_rediscover_environment(self):
        root = Path(__file__).parents[1] / "bridge"
        files = {
            "main.py": (root / "main.py").read_text(encoding="utf-8"),
            "media.py": (root / "media.py").read_text(encoding="utf-8"),
            "help.py": (root / "help.py").read_text(encoding="utf-8"),
        }

        worker_markers = (
            "def process_message_job",
            "def process_image_job",
            "def process_callback_job",
            "def process_edit_job",
            "def make_durable_backlog_dispatcher",
            "def run_check",
        )
        for function_marker in worker_markers:
            start = files["main.py"].index(function_marker)
            next_def = files["main.py"].find("\ndef ", start + 4)
            chunk = files["main.py"][
                start: next_def if next_def >= 0 else None
            ]
            self.assertNotIn("os.environ", chunk, function_marker)

        for filename, function_marker in (
            ("media.py", "def process_voice_job"),
            ("help.py", "def process_document_job"),
        ):
            start = files[filename].index(function_marker)
            next_def = files[filename].find("\ndef ", start + 4)
            chunk = files[filename][
                start: next_def if next_def >= 0 else None
            ]
            self.assertNotIn("os.environ", chunk, function_marker)

    def test_composition_module_does_not_import_compatibility_runtime(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "composition.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("bridge.runtime", source)
        self.assertNotIn("CURRENT_SERVICES", source)
        self.assertNotIn("get_services(", source)
        self.assertNotIn("set_services(", source)

    def test_job_service_worker_guard_is_explicit_not_global_lookup(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "main.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            'prepare_worker=globals().get("_guard_durable_worker")',
            source,
        )
        self.assertIn(
            "prepare_worker=_DURABLE_WORKER_GUARD.prepare",
            source,
        )

    def test_phase5_extracted_services_include_job_service(self):
        root = Path(__file__).parents[1] / "bridge"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.glob("*.py")
        )
        self.assertIn("class GroupDirectorService", source)
        self.assertIn("class MemoryService", source)
        self.assertIn("class PersonaService", source)
        self.assertIn("class SyncService", source)
        self.assertIn("class JobService", source)


if __name__ == "__main__":
    unittest.main()
