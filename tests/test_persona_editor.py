from application_test_setup import ensure_application_extensions, make_test_memory_service, make_native_test_persona_service

ensure_application_extensions()

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge.config as config
import bridge.command_routes as _m_command_routes
import bridge.input_flows as _m_input_flows
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.persona_sync as _m_persona_sync
import bridge.session_naming as _m_session_naming
import bridge.sync_core as _m_sync_core
import bridge.cards as _m_cards
import bridge.telegram as _m_telegram
class FakePersonaService:
    def __init__(self):
        self.calls = []
        self.personas = {
            "bridge-user.png": {
                "name": "Test User",
                "description": "Original description",
                "sillytavern_avatar": "bridge-user.png",
            }
        }
        self.create_error = None
        self.update_error = None
        self.delete_error = None
        self.personas["other.png"] = {
            "name": "Other",
            "description": "Other description",
            "sillytavern_avatar": "other.png",
        }

    def list(self):
        return dict(self.personas)

    def get(self, persona_id):
        return self.personas.get(persona_id)

    def name(self, persona_id):
        persona = self.get(persona_id)
        return str(persona.get("name") or "") if persona else ""

    def create_and_select(
        self,
        db,
        chat_id,
        session_id,
        logical_id,
        name,
        description,
        *,
        operation_id=None,
    ):
        self.calls.append(
            (
                "create_and_select",
                db,
                chat_id,
                session_id,
                logical_id,
                name,
                description,
                operation_id,
            )
        )
        if self.create_error is not None:
            raise self.create_error
        avatar = f"bridge-{logical_id}.png"
        self.personas[avatar] = {
            "name": name,
            "description": description,
            "sillytavern_avatar": avatar,
        }
        return avatar

    def update(self, persona_id, name, description):
        self.calls.append(
            ("update", persona_id, name, description)
        )
        if self.update_error is not None:
            raise self.update_error
        self.personas[persona_id] = {
            "name": name,
            "description": description,
            "sillytavern_avatar": persona_id,
        }
        return persona_id

    def select(
        self,
        db,
        chat_id,
        session_id,
        persona_id,
        *,
        operation_id=None,
    ):
        self.calls.append(
            (
                "select",
                db,
                chat_id,
                session_id,
                persona_id,
                operation_id,
            )
        )
        return persona_id in self.personas

    def disable(
        self,
        db,
        chat_id,
        session_id,
        *,
        operation_id=None,
    ):
        self.calls.append(
            ("disable", db, chat_id, session_id, operation_id)
        )

    def delete_if_unused(self, db, persona_id):
        self.calls.append(("delete_if_unused", db, persona_id))
        if self.delete_error is not None:
            raise self.delete_error
        return self.personas.pop(persona_id, None) is not None


class PersonaEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_db = config.DB_FILE
        self.old_settings = _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = _m_persona_sync._NATIVE_PERSONA_CACHE
        self.old_cache_time = _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = _m_persona_sync.phase3_api_configured
        config.DB_FILE = root / "bridge.sqlite3"
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = root / "User Avatars"
        _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"avatar")
        self.native = {"user_avatar": "user-default.png", "power_user": {"personas": {"bridge-user.png": "Test User"}, "persona_descriptions": {"bridge-user.png": {"description": "Original description", "connections": ["keep"]}}}, "unrelated": {"keep": True}}
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.native), encoding="utf-8")
        _m_persona_sync._NATIVE_PERSONA_CACHE = {}
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        _m_persona_sync.phase3_api_configured = lambda: False
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(self.db, "chat", "provider/model", session_id="persona-session")
        self.calls = []
        self.old_panel_request = _m_panel_callback_routes.telegram_request
        self.old_cards_request = _m_cards.telegram_request
        self.old_input_request = _m_input_flows.telegram_request
        self.old_input_send_text = _m_input_flows.send_text
        self.old_pending_send_text = _m_message_commands.send_text
        self.old_input_close = _m_input_flows.close_panel_message
        request_stub = lambda _token, method, payload: self.calls.append((method, payload)) or {"message_id": 500}
        _m_panel_callback_routes.telegram_request = request_stub
        _m_cards.telegram_request = request_stub
        _m_input_flows.telegram_request = request_stub
        send_stub = lambda _token, _chat, _text: [501]
        _m_input_flows.send_text = send_stub
        _m_message_commands.send_text = send_stub
        _m_input_flows.close_panel_message = lambda _db, _token, _chat, _callback: None
        self.persona_service = make_native_test_persona_service()

    def tearDown(self):
        _m_panel_callback_routes.telegram_request = self.old_panel_request
        _m_cards.telegram_request = self.old_cards_request
        _m_input_flows.telegram_request = self.old_input_request
        _m_input_flows.send_text = self.old_input_send_text
        _m_message_commands.send_text = self.old_pending_send_text
        _m_input_flows.close_panel_message = self.old_input_close
        _m_persona_sync._NATIVE_PERSONA_CACHE = self.old_cache
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        _m_persona_sync.phase3_api_configured = self.old_phase3
        self.db.close()
        config.DB_FILE = self.old_db
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(_m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.read_text(encoding="utf-8"))

    def _start(self, mode, persona_id="", *, persona_service=None):
        callback = {"id": "callback", "message": {"message_id": 77}}
        _m_input_flows.start_persona_input(
            self.db,
            "token",
            "chat",
            self.session["session_id"],
            mode,
            persona_id,
            callback,
            persona_service=persona_service or self.persona_service,
        )
        return json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat"))

    def test_pending_create_uses_injected_persona_service(self):
        self._start("create")
        fake = FakePersonaService()
        with patch.object(
            _m_persona_sync,
            "upsert_native_persona",
            side_effect=AssertionError("raw upsert bypassed service"),
        ):
            handled = _m_message_commands.handle_pending_input(
                self.db,
                "token",
                "chat",
                self.session,
                "writer | Writer | I write concise notes.",
                operation_id=12,
                persona_service=fake,
             memory_service=make_test_memory_service())

        self.assertTrue(handled)
        call = fake.calls[-1]
        self.assertEqual(call[0], "create_and_select")
        self.assertIs(call[1], self.db)
        self.assertEqual(
            call[2:],
            (
                "chat",
                self.session["session_id"],
                "writer",
                "Writer",
                "I write concise notes.",
                12,
            ),
        )

    def test_pending_edit_uses_injected_persona_service(self):
        self._start("edit", "bridge-user.png")
        fake = FakePersonaService()
        with patch.object(
            _m_persona_sync,
            "upsert_native_persona",
            side_effect=AssertionError("raw upsert bypassed service"),
        ):
            handled = _m_message_commands.handle_pending_input(
                self.db,
                "token",
                "chat",
                self.session,
                "Updated Name | Updated description",
                persona_service=fake,
             memory_service=make_test_memory_service())

        self.assertTrue(handled)
        self.assertEqual(
            fake.calls[-1],
            (
                "update",
                "bridge-user.png",
                "Updated Name",
                "Updated description",
            ),
        )

    def test_invalid_pending_create_does_not_call_injected_service(self):
        state = self._start("create")
        fake = FakePersonaService()
        self.assertTrue(
            _m_message_commands.handle_pending_input(
                self.db,
                "token",
                "chat",
                self.session,
                "bad input",
                persona_service=fake,
             memory_service=make_test_memory_service())
        )
        self.assertEqual(fake.calls, [])
        current = json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat"))
        self.assertEqual(current["mode"], "create")
        self.assertEqual(
            current["prompt_message_ids"],
            state["prompt_message_ids"] + [501],
        )

    def test_injected_service_failure_keeps_pending_state(self):
        self._start("create")
        fake = FakePersonaService()
        fake.create_error = RuntimeError("offline")
        self.assertTrue(
            _m_message_commands.handle_pending_input(
                self.db,
                "token",
                "chat",
                self.session,
                "writer | Writer | Description",
                persona_service=fake,
             memory_service=make_test_memory_service())
        )
        self.assertTrue(
            json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat"))
        )

    def test_persona_menu_reads_from_injected_service(self):
        fake = FakePersonaService()
        fake.personas = {
            "injected.png": {
                "name": "Injected Persona",
                "description": "Injected",
                "sillytavern_avatar": "injected.png",
            }
        }
        _m_command_routes.send_persona_menu(
            "token",
            "chat",
            "injected.png",
            persona_service=fake,
        )
        payload = self.calls[-1][1]
        labels = [
            button["text"]
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn("✅ Injected Persona", labels)
        self.assertIn("Current Persona: Injected Persona", payload["text"])

    def test_persona_edit_menu_reads_from_injected_service(self):
        fake = FakePersonaService()
        fake.personas = {
            "injected.png": {
                "name": "Injected Persona",
                "description": "Injected description",
                "sillytavern_avatar": "injected.png",
            }
        }
        _m_input_flows.send_persona_edit_menu(
            "token",
            "chat",
            "injected.png",
            77,
            persona_service=fake,
        )
        self.assertIn("Injected Persona", self.calls[-1][1]["text"])
        self.assertIn(
            "Injected description",
            self.calls[-1][1]["text"],
        )

    def test_persona_delete_menu_reads_from_injected_service(self):
        fake = FakePersonaService()
        fake.personas = {
            "current.png": {
                "name": "Current",
                "description": "Current",
                "sillytavern_avatar": "current.png",
            },
            "injected.png": {
                "name": "Injected Persona",
                "description": "Injected",
                "sillytavern_avatar": "injected.png",
            },
        }
        _m_input_flows.send_persona_delete_menu(
            "token",
            "chat",
            "current.png",
            persona_service=fake,
        )
        labels = [
            button["text"]
            for row in self.calls[-1][1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn("🗑️ Injected Persona", labels)
        self.assertNotIn("🗑️ Current", labels)

    def test_persona_command_route_forwards_injected_service(self):
        fake = FakePersonaService()
        services = type("Services", (), {"persona": fake})()
        captured = {}
        with patch.object(_m_command_routes, "send_persona_menu",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ):
            handled = _m_command_routes._handle_entities(
                self.db,
                "token",
                "provider/model",
                {},
                "chat",
                "/persona",
                self.session,
                self.session["session_id"],
                "provider/model",
                self.session.get("persona_id") or "",
                services=services,
            )
        self.assertTrue(handled)
        self.assertIs(captured["persona_service"], fake)

    def test_native_catalog_loads_and_create_selects_avatar(self):
        self._start("create")
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | I write concise notes.", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,))
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-writer.png"], "Writer")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-writer.png"]["description"], "I write concise notes.")
        self.assertTrue((_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / "bridge-writer.png").is_file())
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", "persona-session", "provider/model")["persona_id"], "bridge-writer.png")
        self.assertEqual(settings["unrelated"], {"keep": True})

    def test_default_persona_resolves_only_native_persona(self):
        self.assertEqual(_m_persona_sync.default_persona_id(), "")
        self.assertEqual(_m_sync_core.persona_name(""), "")

    def test_edit_name_and_description_updates_native_settings(self):
        self._start("edit", "bridge-user.png")
        _m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "Updated Name | Updated description", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,)
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-user.png"], "Updated Name")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["description"], "Updated description")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["connections"], ["keep"])

    def test_edit_description_only_preserves_native_name(self):
        self._start("edit_description", "bridge-user.png")
        _m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "Description only", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,)
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-user.png"], "Test User")
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["description"], "Description only")

    def test_edit_callback_reads_native_metadata(self):
        _m_session_naming.update_session(self.db, "chat", self.session["session_id"], persona_id="bridge-user.png")
        self.session = _m_memory_curator.load_session(self.db, "chat", self.session["session_id"], "provider/model")
        answers = []
        handled = _m_panel_callback_routes.handle_persona_callback(self.db, "token", {"id": "cb"}, lambda _t, _i, text: answers.append(text), "persona:edit", "chat", {"message_id": 77}, self.session, "persona-session", None,
                persona_service=self.persona_service,)
        self.assertTrue(handled)
        self.assertEqual(answers, ["Review persona"])
        self.assertIn("Original description", self.calls[-1][1]["text"])
        self.assertEqual(self.calls[-1][1]["parse_mode"], "HTML")
        self.assertIn("<pre>Original description</pre>", self.calls[-1][1]["text"])

    def test_callback_select_uses_injected_persona_service(self):
        fake = FakePersonaService()
        token_value = _m_panel_callback_routes.dynamic_callback_token(
            "persona",
            "other.png",
            "chat",
        )
        answers = []
        with patch.object(
            _m_telegram,
            "update_session",
            side_effect=AssertionError("direct session mutation"),
        ):
            handled = _m_panel_callback_routes.handle_persona_callback(
                self.db,
                "token",
                {"id": "cb"},
                lambda _t, _i, text: answers.append(text),
                f"persona:{token_value}",
                "chat",
                {"message_id": 77},
                self.session,
                self.session["session_id"],
                33,
                persona_service=fake,
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[-1][0], "select")
        self.assertEqual(fake.calls[-1][4:], ("other.png", 33))
        self.assertEqual(answers, ["Persona selected"])

    def test_callback_off_uses_injected_persona_service(self):
        fake = FakePersonaService()
        answers = []
        with patch.object(
            _m_telegram,
            "update_session",
            side_effect=AssertionError("direct session mutation"),
        ):
            handled = _m_panel_callback_routes.handle_persona_callback(
                self.db,
                "token",
                {"id": "cb"},
                lambda _t, _i, text: answers.append(text),
                "persona:off",
                "chat",
                {"message_id": 77},
                self.session,
                self.session["session_id"],
                34,
                persona_service=fake,
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[-1][0], "disable")
        self.assertEqual(fake.calls[-1][-1], 34)
        self.assertEqual(answers, ["Persona off"])

    def test_callback_delete_uses_injected_persona_service(self):
        fake = FakePersonaService()
        token_value = _m_panel_callback_routes.dynamic_callback_token(
            "persona",
            "other.png",
            "chat",
        )
        answers = []
        with patch.object(
            _m_persona_sync,
            "delete_native_persona",
            side_effect=AssertionError("raw delete bypassed service"),
        ):
            handled = _m_panel_callback_routes.handle_persona_callback(
                self.db,
                "token",
                {"id": "cb"},
                lambda _t, _i, text: answers.append(text),
                f"personadeleteconfirm:{token_value}",
                "chat",
                {"message_id": 77},
                self.session,
                self.session["session_id"],
                None,
                persona_service=fake,
            )

        self.assertTrue(handled)
        self.assertEqual(
            fake.calls[-1],
            ("delete_if_unused", self.db, "other.png"),
        )
        self.assertEqual(answers, ["Deleted"])

    def test_callback_delete_maps_reference_refusal_feedback(self):
        fake = FakePersonaService()
        fake.delete_error = ValueError(
            "Persona is used by another session"
        )
        token_value = _m_panel_callback_routes.dynamic_callback_token(
            "persona",
            "other.png",
            "chat",
        )
        answers = []
        handled = _m_panel_callback_routes.handle_persona_callback(
            self.db,
            "token",
            {"id": "cb"},
            lambda _t, _i, text: answers.append(text),
            f"personadeleteconfirm:{token_value}",
            "chat",
            {"message_id": 77},
            self.session,
            self.session["session_id"],
            None,
            persona_service=fake,
        )
        self.assertTrue(handled)
        self.assertEqual(
            answers,
            ["Deletion refused: Persona is used by another session"],
        )

    def test_delete_refuses_persona_referenced_by_another_chat(self):
        target = _m_persona_sync.upsert_native_persona("shared", "Shared", "Shared description")
        other = _m_session_naming.create_session(self.db, "other-chat", "provider/model", session_id="other-session")
        _m_session_naming.update_session(self.db, "other-chat", other["session_id"], persona_id=target)
        token_value = _m_panel_callback_routes.dynamic_callback_token("persona", target, "chat")
        answers = []

        handled = _m_panel_callback_routes.handle_persona_callback(
            self.db,
            "token",
            {"id": "cb"},
            lambda _token, _callback_id, text: answers.append(text),
            f"personadeleteconfirm:{token_value}",
            "chat",
            {"message_id": 77},
            self.session,
            self.session["session_id"],
            None,
                persona_service=self.persona_service,
        )

        self.assertTrue(handled)
        self.assertEqual(answers, ["Deletion refused: Persona is used by another session"])
        self.assertIsNotNone(_m_sync_core.get_persona(target))

    def test_invalid_create_keeps_pending_state_and_native_file(self):
        state = self._start("create")
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "bad input", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,))
        self.assertEqual(self._settings(), self.native)
        current = json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat"))
        self.assertEqual(current["mode"], "create")
        self.assertEqual(current["prompt_message_ids"], state["prompt_message_ids"] + [501])

    def test_cancel_clears_pending_persona_input(self):
        self._start("create")
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "/cancel", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,))
        self.assertEqual(_m_session_naming.get_meta(self.db, "persona_input:chat"), "")
        self.assertEqual(self._settings(), self.native)

    def test_pending_persona_input_is_session_scoped(self):
        self._start("create")
        other = dict(self.session)
        other["session_id"] = "other-session"
        self.assertFalse(_m_message_commands.handle_pending_input(self.db, "token", "chat", other, "writer | Writer | Should not apply", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,))
        self.assertEqual(self._settings(), self.native)

    def test_save_failure_keeps_native_settings_and_pending_state(self):
        self._start("edit_description", "bridge-user.png")
        original_save = _m_persona_sync._save_native_settings
        _m_persona_sync._save_native_settings = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "Attempted update", memory_service=make_test_memory_service(),
                persona_service=self.persona_service,))
        finally:
            _m_persona_sync._save_native_settings = original_save
        self.assertEqual(self._settings(), self.native)
        self.assertTrue(json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat")))


if __name__ == "__main__":
    unittest.main()
