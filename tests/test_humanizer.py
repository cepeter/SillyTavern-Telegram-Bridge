from application_test_setup import ensure_application_extensions, make_test_provider_port
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.humanize as humanize
import bridge.humanizer_settings as humanizer_settings
import bridge.session_core as session_core
from bridge.memory_curator import db_connect
from bridge.provider_port import ProviderPort


class HumanizerNormalizationTests(unittest.TestCase):
    def test_accepts_on_off_and_aliases(self):
        self.assertEqual(humanizer_settings.normalize_humanizer("on"), "on")
        self.assertEqual(humanizer_settings.normalize_humanizer("off"), "off")
        self.assertEqual(humanizer_settings.normalize_humanizer("ON"), "on")
        self.assertEqual(humanizer_settings.normalize_humanizer(" true "), "on")
        self.assertEqual(humanizer_settings.normalize_humanizer("disabled"), "off")
        self.assertEqual(humanizer_settings.normalize_humanizer("0"), "off")

    def test_rejects_unknown_values(self):
        for bad in ("", "maybe", "onwards", "yes please", "1.5"):
            with self.assertRaises(ValueError):
                humanizer_settings.normalize_humanizer(bad)

    def test_enabled_and_label(self):
        self.assertFalse(humanizer_settings.humanizer_enabled(None))
        self.assertFalse(humanizer_settings.humanizer_enabled("off"))
        self.assertTrue(humanizer_settings.humanizer_enabled("on"))
        self.assertEqual(humanizer_settings.humanizer_label("on"), "On")
        self.assertEqual(humanizer_settings.humanizer_label(None), "Off")


class HumanizerRenderTests(unittest.TestCase):
    def _port(self, backend) -> ProviderPort:
        return make_test_provider_port(generate_backend=backend)

    def test_empty_text_returns_empty_without_calling_provider(self):
        calls = []

        def backend(*args, **kwargs):
            calls.append(1)
            return "rewritten"

        result = humanize.render_humanized_response("key", "model", "  ", "s", provider_port=self._port(backend))
        self.assertEqual(result, "  ")
        self.assertEqual(calls, [])

    def test_success_returns_rewritten_text(self):
        def backend(*args, **kwargs):
            self.assertEqual(kwargs["session_id"], "telegram:1:s:humanize")
            return "humanized output"

        result = humanize.render_humanized_response(
            "key", "model", "raw output", "telegram:1:s", provider_port=self._port(backend)
        )
        self.assertEqual(result, "humanized output")

    def test_provider_failure_fails_open_to_original(self):
        def backend(*args, **kwargs):
            raise RuntimeError("provider down")

        result = humanize.render_humanized_response(
            "key", "model", "original text", "s", provider_port=self._port(backend)
        )
        self.assertEqual(result, "original text")

    def test_empty_provider_result_falls_back_to_original(self):
        result = humanize.render_humanized_response(
            "key", "model", "original text", "s", provider_port=self._port(lambda *a, **k: "")
        )
        self.assertEqual(result, "original text")


class HumanizerPersistenceTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.app_settings = self.app_settings_builder.build()
        self.db = db_connect(app_settings=self.app_settings)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_new_session_defaults_to_off(self):
        session = session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings
        )
        self.assertEqual(session["humanizer"], "off")

    def test_set_humanizer_persists_on(self):
        session = session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings
        )
        session_core.update_session(
            self.db,
            "chat",
            session["session_id"],
            humanizer="on",
        )
        reloaded = session_core.load_session(
            self.db,
            "chat",
            session["session_id"],
            self.app_settings_builder.default_model,
            app_settings=self.app_settings,
        )
        self.assertEqual(reloaded["humanizer"], "on")

    def test_update_session_accepts_humanizer_column(self):
        session = session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings
        )
        session_core.update_session(self.db, "chat", session["session_id"], humanizer="on")
        reloaded = session_core.load_session(
            self.db,
            "chat",
            session["session_id"],
            self.app_settings_builder.default_model,
            app_settings=self.app_settings,
        )
        self.assertEqual(reloaded["humanizer"], "on")


class HumanizerGenerationWiringTests(unittest.TestCase):
    def test_render_session_response_chains_humanizer_when_enabled(self):
        calls = []

        def backend(api_key, model, messages, **kwargs):
            calls.append(kwargs.get("session_id"))
            return "humanized"

        port = make_test_provider_port(generate_backend=backend)
        session = {
            "session_id": "s",
            "model_id": "model",
            "response_language": "auto",
            "humanizer": "on",
        }
        from bridge.generation import render_session_response

        result = render_session_response("key", session, "raw", "chat", {}, provider_port=port)
        self.assertEqual(result, "humanized")
        self.assertEqual(calls, ["telegram:chat:s:humanize"])

    def test_render_session_response_skips_humanizer_when_off(self):
        calls = []

        def backend(*args, **kwargs):
            calls.append(1)
            return "should not be called"

        port = make_test_provider_port(generate_backend=backend)
        session = {
            "session_id": "s",
            "model_id": "model",
            "response_language": "auto",
            "humanizer": "off",
        }
        from bridge.generation import render_session_response

        result = render_session_response("key", session, "raw", "chat", {}, provider_port=port)
        self.assertEqual(result, "raw")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
