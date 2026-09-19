from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
