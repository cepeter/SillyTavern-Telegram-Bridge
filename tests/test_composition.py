from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bridge.runtime as rt

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
        services = build_bridge_services(
            config,
            db_factory=lambda: sqlite3.connect(":memory:"),
            telegram=telegram,
            background=background,
        )

        self.assertIs(services.config, config)
        self.assertIs(services.telegram, telegram)
        self.assertIs(services.background, background)
        with self.assertRaises(FrozenInstanceError):
            services.telegram = telegram

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
        self.old_db_file = rt.DB_FILE
        self.old_ready = rt._DB_SCHEMA_READY

    def tearDown(self):
        rt.DB_FILE = self.old_db_file
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = self.old_ready
            if hasattr(rt, "_DB_SCHEMA_READY_PATHS"):
                rt._DB_SCHEMA_READY_PATHS.clear()
        self.tmp.cleanup()

    def test_explicit_database_paths_initialize_independently(self):
        default_path = self.root / "default.sqlite3"
        explicit_a = self.root / "a.sqlite3"
        explicit_b = self.root / "b.sqlite3"
        rt.DB_FILE = default_path
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
            if hasattr(rt, "_DB_SCHEMA_READY_PATHS"):
                rt._DB_SCHEMA_READY_PATHS.clear()

        default_db = rt.db_connect()
        default_db.close()

        a = rt.db_connect(explicit_a)
        a.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('which','a')"
        )
        a.commit()
        a.close()

        b = rt.db_connect(explicit_b)
        self.assertIsNone(
            b.execute(
                "SELECT value FROM meta WHERE key='which'"
            ).fetchone()
        )
        b.close()

        self.assertTrue(explicit_a.is_file())
        self.assertTrue(explicit_b.is_file())

    def test_explicit_factory_does_not_mutate_global_db_file(self):
        original = rt.DB_FILE
        explicit = self.root / "factory.sqlite3"
        db = rt.db_connect(explicit)
        db.close()
        self.assertEqual(rt.DB_FILE, original)


class WorkerInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workers.sqlite3"
        self.db = rt.db_connect(self.db_path)
        self.db.close()
        self.opened = 0
        self.sent = []
        self.global_sent = []

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
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _db_factory(self):
        self.opened += 1
        return rt.db_connect(self.db_path)

    def test_all_worker_signatures_receive_services_not_startup_bundle(self):
        expectations = {
            rt.process_message_job: (
                "services", "fields", "chat_id", "text", "message_id",
                "queued_session_id", "model_override", "job_id",
            ),
            rt.process_image_job: (
                "services", "chat_id", "file_id", "caption", "file_size",
                "message_id", "queued_session_id", "model_override", "job_id",
            ),
            rt.process_callback_job: (
                "services", "chat_id", "callback", "job_id",
            ),
            rt.process_edit_job: (
                "services", "chat_id", "message_id", "text",
                "model_override", "job_id",
            ),
            rt.process_voice_job: (
                "services", "fields", "chat_id", "voice", "message_id",
                "queued_session_id", "model_override", "job_id",
            ),
            rt.process_document_job: (
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

        with patch.object(
            rt, "committed_assistant_for_message", return_value=None
        ), patch.object(
            rt, "process_message", side_effect=fake_process_message
        ):
            rt.process_message_job(
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

    def test_recovered_model_override_wins_over_config_default(self):
        captured = {}

        with patch.object(
            rt, "committed_assistant_for_message", return_value=None
        ), patch.object(
            rt,
            "process_message",
            side_effect=lambda _db, _token, _key, model, *_args, **_kwargs:
                captured.setdefault("model", model),
        ):
            rt.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                11,
                None,
                "stored::model",
            )

        self.assertEqual(captured["model"], "stored::model")

    def test_callback_failure_uses_injected_send_text(self):
        with patch.object(
            rt,
            "process_callback",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            rt,
            "send_text",
            side_effect=lambda *args, **_kwargs: self.global_sent.append(args),
        ):
            rt.process_callback_job(
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
        self.db = rt.db_connect(self.path)
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
            db_factory=lambda: rt.db_connect(self.path),
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=self.background,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _submit(self, label, chat_id, function, *args):
        self.submitted.append((label, chat_id, function, args))
        return True

    def test_submit_durable_chat_job_uses_injected_background_and_explicit_job_id(self):
        with patch.object(
            rt,
            "submit_chat_background",
            side_effect=AssertionError("global background used"),
        ), patch.object(rt, "mark_job_scheduled") as scheduled:
            queued = rt.submit_durable_chat_job(
                self.db,
                self.background,
                "generation",
                "chat",
                41,
                rt.process_message_job,
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                None,
                None,
            )

        self.assertTrue(queued)
        self.assertIs(self.submitted[0][3][0], self.services)
        self.assertEqual(self.submitted[0][3][-1], 41)
        scheduled.assert_called_once_with(self.db, 41)

    def test_recovered_job_propagates_same_services_and_stored_model(self):
        row = (
            51,
            "chat",
            "session",
            "10",
            "generation",
            '{"text":"hello","model":"stored::model"}',
        )
        with patch.object(
            rt,
            "recover_jobs",
            return_value=[row],
        ), patch.object(
            rt,
            "mark_job_scheduled",
        ):
            rt.dispatch_recovered_jobs(
                self.db,
                self.services,
                {"name": "Mira"},
            )

        label, chat_id, function, args = self.submitted[0]
        self.assertEqual(label, "generation")
        self.assertEqual(chat_id, "chat")
        self.assertIs(inspect.unwrap(function), rt.process_message_job)
        self.assertIs(args[0], self.services)
        self.assertEqual(args[-2], "stored::model")
        self.assertEqual(args[-1], 51)

    def test_failed_background_submission_does_not_mark_job_scheduled(self):
        background = BackgroundRuntime(
            submit_chat=lambda *_args, **_kwargs: False,
            register_backlog_dispatcher=lambda _callback: None,
            begin_shutdown=lambda: None,
        )
        with patch.object(
            rt,
            "submit_chat_background",
            side_effect=AssertionError("global background used"),
        ), patch.object(rt, "mark_job_scheduled") as scheduled:
            queued = rt.submit_durable_chat_job(
                self.db,
                background,
                "generation",
                "chat",
                52,
                rt.process_message_job,
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                None,
                None,
            )
        self.assertFalse(queued)
        scheduled.assert_not_called()

    def test_backlog_dispatcher_reuses_same_services_instance(self):
        opened = []
        seen = []

        def factory():
            db = rt.db_connect(self.path)
            opened.append(db)
            return db

        services = BridgeServices(
            config=self.services.config,
            db_factory=factory,
            telegram=self.services.telegram,
            background=self.services.background,
        )

        with patch.object(
            rt,
            "dispatch_recovered_jobs",
            side_effect=lambda db, actual_services, fields, **kwargs:
                seen.append((db, actual_services, fields, kwargs)),
        ):
            dispatcher = rt.make_durable_backlog_dispatcher(
                services,
                {"name": "Mira"},
            )
            dispatcher()

        self.assertEqual(len(opened), 1)
        self.assertIs(seen[0][1], services)
        self.assertEqual(seen[0][3], {"recover_running": False})


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
            db_factory=lambda: rt.db_connect(self.config.db_file),
            telegram=TelegramRuntime(
                request=self._request,
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, token, method, payload=None):
        self.requests.append((token, method, payload))
        return {"username": "bridge_bot"}

    def test_run_check_uses_prebuilt_services_without_reloading_environment(self):
        with patch.object(
            rt,
            "load_env_file",
            side_effect=AssertionError("run_check must not reload env"),
        ), patch.object(
            rt,
            "card_fields",
            return_value={"name": "Mira"},
        ), patch.object(
            rt,
            "read_png_chara",
            return_value={},
        ), patch.object(
            rt,
            "phase3_api_configured",
            return_value=False,
        ):
            self.assertEqual(rt.run_check(self.services), 0)

        self.assertEqual(
            self.requests,
            [("token", "getMe", None)],
        )

    def test_signal_handler_captures_injected_shutdown_callable(self):
        installed = {}
        calls = []

        def fake_signal(signum, handler):
            installed[signum] = handler

        with patch.object(rt.signal, "signal", side_effect=fake_signal):
            rt.install_bridge_signal_handlers(
                lambda: calls.append("shutdown")
            )

        handler = installed[rt.signal.SIGTERM]
        handler(rt.signal.SIGTERM, None)
        self.assertTrue(rt._SHUTDOWN_EVENT.is_set())
        self.assertEqual(calls, ["shutdown"])
        rt._SHUTDOWN_EVENT.clear()

    def test_main_check_builds_services_once_and_passes_same_object(self):
        parsed = rt.argparse.Namespace(check=True)
        with patch.object(
            rt.argparse.ArgumentParser,
            "parse_args",
            return_value=parsed,
        ), patch.object(
            rt,
            "load_env_file",
        ), patch.object(
            rt,
            "refresh_phase3_config",
        ), patch.object(
            rt,
            "enforce_runtime_permissions",
        ), patch.object(
            rt,
            "_load_startup_config",
            return_value=self.config,
        ) as load_config, patch.object(
            rt,
            "_build_startup_services",
            return_value=self.services,
        ) as build_services, patch.object(
            rt,
            "validate_startup_credential",
        ), patch.object(
            rt,
            "set_bot_commands",
        ), patch.object(
            rt,
            "run_check",
            return_value=0,
        ) as run_check:
            self.assertEqual(rt.main(), 0)

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
            "def dispatch_recovered_jobs",
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

    def test_phase5_service_classes_are_not_introduced(self):
        root = Path(__file__).parents[1] / "bridge"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.glob("*.py")
        )
        for forbidden in (
            "class GroupDirectorService",
            "class MemoryService",
            "class PersonaService",
            "class SyncService",
            "class JobService",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
