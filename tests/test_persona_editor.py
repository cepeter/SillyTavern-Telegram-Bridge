from application_test_setup import (
    ensure_application_extensions,
    make_native_test_persona_service,
    make_test_group_service,
    make_test_input_flow_service,
    make_test_memory_service,
    make_test_provider_port,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.callback_tokens as _owner_callback_tokens
import bridge.persona_callbacks as _owner_persona_callbacks
import bridge.persona_delete_panel as _owner_persona_delete_panel
import bridge.persona_input as _owner_persona_input
import bridge.persona_panels as _owner_persona_panels
import bridge.session_core as _owner_session_core
from bridge import persona_callbacks, persona_input, persona_panels

ensure_application_extensions()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.persona_sync as _m_persona_sync
import bridge.session_naming as _m_session_naming
import bridge.sillytavern_api as _m_sillytavern_api
import bridge.sync_core as _m_sync_core
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
        self.calls.append(("update", persona_id, name, description))
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
        self.calls.append(("disable", db, chat_id, session_id, operation_id))

    def delete_if_unused(self, db, persona_id):
        self.calls.append(("delete_if_unused", db, persona_id))
        if self.delete_error is not None:
            raise self.delete_error
        return self.personas.pop(persona_id, None) is not None


class PersonaEditorTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_db = self.app_settings_builder.db_file
        self.old_settings = self.app_settings_builder.native_persona_settings_file
        self.old_avatars = self.app_settings_builder.native_persona_avatar_dir
        self.old_backups = self.app_settings_builder.native_persona_backup_dir
        self.old_phase3 = _m_sillytavern_api.live_sync_api_configured
        self.app_settings_builder.db_file = root / "bridge.sqlite3"
        self.app_settings_builder.native_persona_settings_file = root / "settings.json"
        self.app_settings_builder.native_persona_avatar_dir = root / "User Avatars"
        self.app_settings_builder.native_persona_backup_dir = root / "backups"
        self.app_settings_builder.native_persona_avatar_dir.mkdir()
        (self.app_settings_builder.native_persona_avatar_dir / "user-default.png").write_bytes(b"avatar")
        self.native = {
            "user_avatar": "user-default.png",
            "power_user": {
                "personas": {"bridge-user.png": "Test User"},
                "persona_descriptions": {
                    "bridge-user.png": {"description": "Original description", "connections": ["keep"]}
                },
            },
            "unrelated": {"keep": True},
        }
        self.app_settings_builder.native_persona_settings_file.write_text(json.dumps(self.native), encoding="utf-8")
        _m_sillytavern_api.live_sync_api_configured = lambda *, app_settings=None: False
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "provider/model",
            session_id="persona-session",
            app_settings=self.app_settings_builder.build(),
        )
        self.request_context = make_test_request_context(
            self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
        )
        self.calls = []

        def request_stub(_token, method, payload, **_kwargs):
            return self.calls.append((method, payload)) or {"message_id": 500}

        def send_stub(_token, _chat, _text):
            return [501]

        for owner in (_m_cards, persona_panels):
            patcher = patch.object(owner, "send_panel_request", side_effect=request_stub)
            patcher.start()
            self.addCleanup(patcher.stop)
        for owner in (persona_input, persona_callbacks, _m_message_commands):
            patcher = patch.object(owner, "send_text", side_effect=send_stub)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(persona_input, "close_panel_message", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.persona_service = make_native_test_persona_service(app_settings=self.app_settings_builder.build())
        self.prompt_deletions = []

        def cleanup_request(_token, method, payload):
            self.assertEqual(method, "deleteMessage")
            self.prompt_deletions.append(payload)
            return {}

        cleanup_patch = patch.object(_m_telegram, "telegram_request", side_effect=cleanup_request)
        cleanup_patch.start()
        self.addCleanup(cleanup_patch.stop)

    def tearDown(self):
        _m_sillytavern_api.live_sync_api_configured = self.old_phase3
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.app_settings_builder.native_persona_settings_file = self.old_settings
        self.app_settings_builder.native_persona_avatar_dir = self.old_avatars
        self.app_settings_builder.native_persona_backup_dir = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(self.app_settings_builder.native_persona_settings_file.read_text(encoding="utf-8"))

    def _start(self, mode, persona_id="", *, persona_service=None):
        callback = {"id": "callback", "message": {"message_id": 77}}
        _owner_persona_input.start_persona_input(
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
            handled = make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "writer | Writer | I write concise notes.",
                operation_id=12,
                persona_service=fake,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
            )

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
            handled = make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "Updated Name | Updated description",
                persona_service=fake,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
            )

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
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "bad input",
                persona_service=fake,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
            )
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
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "writer | Writer | Description",
                persona_service=fake,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
            )
        )
        self.assertTrue(json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat")))

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
            request_context=self.request_context,
        )
        payload = self.calls[-1][1]
        labels = [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
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
        _owner_persona_panels.send_persona_edit_menu(
            "token",
            "chat",
            "injected.png",
            77,
            persona_service=fake,
            request_context=self.request_context,
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
        _owner_persona_delete_panel.send_persona_delete_menu(
            "token",
            "chat",
            "current.png",
            persona_service=fake,
            request_context=self.request_context,
        )
        labels = [button["text"] for row in self.calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn("🗑️ Injected Persona", labels)
        self.assertNotIn("🗑️ Current", labels)

    def test_persona_command_route_forwards_injected_service(self):
        fake = FakePersonaService()
        services = type("Services", (), {"persona": fake})()
        captured = {}
        with patch.object(
            _m_command_routes,
            "send_persona_menu",
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
                request_context=self.request_context,
                persona_service=services.persona,
            )
        self.assertTrue(handled)
        self.assertIs(captured["persona_service"], fake)

    def test_native_catalog_loads_and_create_selects_avatar(self):
        self._start("create")
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "writer | Writer | I write concise notes.",
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
                persona_service=self.persona_service,
            )
        )
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-writer.png"], "Writer")
        self.assertEqual(
            settings["power_user"]["persona_descriptions"]["bridge-writer.png"]["description"], "I write concise notes."
        )
        self.assertTrue((self.app_settings_builder.native_persona_avatar_dir / "bridge-writer.png").is_file())
        self.assertEqual(
            _m_memory_curator.load_session(
                self.db, "chat", "persona-session", "provider/model", app_settings=self.app_settings_builder.build()
            )["persona_id"],
            "bridge-writer.png",
        )
        self.assertEqual(settings["unrelated"], {"keep": True})

    def test_default_persona_resolves_only_native_persona(self):
        self.assertEqual(_m_persona_sync.default_persona_id(app_settings=self.app_settings_builder.build()), "")
        self.assertEqual(_m_sync_core.persona_name("", app_settings=self.app_settings_builder.build()), "")

    def test_edit_name_and_description_updates_native_settings(self):
        self._start("edit", "bridge-user.png")
        make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
            self.db,
            "token",
            "chat",
            self.session,
            "Updated Name | Updated description",
            group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
            provider_port=make_test_provider_port(),
            memory_service=make_test_memory_service(),
            request_context=self.request_context,
            persona_service=self.persona_service,
        )
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-user.png"], "Updated Name")
        self.assertEqual(
            settings["power_user"]["persona_descriptions"]["bridge-user.png"]["description"], "Updated description"
        )
        self.assertEqual(settings["power_user"]["persona_descriptions"]["bridge-user.png"]["connections"], ["keep"])

    def test_edit_description_only_preserves_native_name(self):
        self._start("edit_description", "bridge-user.png")
        make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
            self.db,
            "token",
            "chat",
            self.session,
            "Description only",
            group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
            provider_port=make_test_provider_port(),
            memory_service=make_test_memory_service(),
            request_context=self.request_context,
            persona_service=self.persona_service,
        )
        settings = self._settings()
        self.assertEqual(settings["power_user"]["personas"]["bridge-user.png"], "Test User")
        self.assertEqual(
            settings["power_user"]["persona_descriptions"]["bridge-user.png"]["description"], "Description only"
        )

    def test_edit_callback_reads_native_metadata(self):
        _m_session_naming.update_session(self.db, "chat", self.session["session_id"], persona_id="bridge-user.png")
        self.session = _m_memory_curator.load_session(
            self.db,
            "chat",
            self.session["session_id"],
            "provider/model",
            app_settings=self.app_settings_builder.build(),
        )
        answers = []
        handled = _owner_persona_callbacks.handle_persona_callback(
            self.db,
            "token",
            {"id": "cb"},
            lambda _t, _i, text: answers.append(text),
            "persona:edit",
            "chat",
            {"message_id": 77},
            self.session,
            "persona-session",
            None,
            persona_service=self.persona_service,
            request_context=self.request_context,
        )
        self.assertTrue(handled)
        self.assertEqual(answers, ["Review persona"])
        self.assertIn("Original description", self.calls[-1][1]["text"])
        self.assertEqual(self.calls[-1][1]["parse_mode"], "HTML")
        self.assertIn("<pre>Original description</pre>", self.calls[-1][1]["text"])

    def test_callback_select_uses_injected_persona_service(self):
        fake = FakePersonaService()
        token_value = _owner_callback_tokens.dynamic_callback_token(
            "persona",
            "other.png",
            "chat",
            db=self.db,
        )
        answers = []
        with patch.object(
            _owner_session_core,
            "update_session",
            side_effect=AssertionError("direct session mutation"),
        ):
            handled = _owner_persona_callbacks.handle_persona_callback(
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
                request_context=self.request_context,
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[-1][0], "select")
        self.assertEqual(fake.calls[-1][4:], ("other.png", 33))
        self.assertEqual(answers, ["Persona selected"])

    def test_callback_off_uses_injected_persona_service(self):
        fake = FakePersonaService()
        answers = []
        with patch.object(
            _owner_session_core,
            "update_session",
            side_effect=AssertionError("direct session mutation"),
        ):
            handled = _owner_persona_callbacks.handle_persona_callback(
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
                request_context=self.request_context,
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[-1][0], "disable")
        self.assertEqual(fake.calls[-1][-1], 34)
        self.assertEqual(answers, ["Persona off"])

    def test_callback_delete_uses_injected_persona_service(self):
        fake = FakePersonaService()
        token_value = _owner_callback_tokens.dynamic_callback_token(
            "persona",
            "other.png",
            "chat",
            db=self.db,
        )
        answers = []
        with patch.object(
            _m_persona_sync,
            "delete_native_persona",
            side_effect=AssertionError("raw delete bypassed service"),
        ):
            handled = _owner_persona_callbacks.handle_persona_callback(
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
                request_context=self.request_context,
            )

        self.assertTrue(handled)
        self.assertEqual(
            fake.calls[-1],
            ("delete_if_unused", self.db, "other.png"),
        )
        self.assertEqual(answers, ["Deleted"])

    def test_callback_delete_maps_reference_refusal_feedback(self):
        fake = FakePersonaService()
        fake.delete_error = ValueError("Persona is used by another session")
        token_value = _owner_callback_tokens.dynamic_callback_token(
            "persona",
            "other.png",
            "chat",
            db=self.db,
        )
        answers = []
        handled = _owner_persona_callbacks.handle_persona_callback(
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
            request_context=self.request_context,
        )
        self.assertTrue(handled)
        self.assertEqual(
            answers,
            ["Deletion refused: Persona is used by another session"],
        )

    def test_delete_refuses_persona_referenced_by_another_chat(self):
        target = _m_persona_sync.upsert_native_persona(
            "shared", "Shared", "Shared description", app_settings=self.app_settings_builder.build()
        )
        other = _m_session_naming.create_session(
            self.db,
            "other-chat",
            "provider/model",
            session_id="other-session",
            app_settings=self.app_settings_builder.build(),
        )
        _m_session_naming.update_session(self.db, "other-chat", other["session_id"], persona_id=target)
        token_value = _owner_callback_tokens.dynamic_callback_token("persona", target, "chat", db=self.db)
        answers = []

        handled = _owner_persona_callbacks.handle_persona_callback(
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
            request_context=self.request_context,
        )

        self.assertTrue(handled)
        self.assertEqual(answers, ["Deletion refused: Persona is used by another session"])
        self.assertIsNotNone(_m_sync_core.get_persona(target, app_settings=self.app_settings_builder.build()))

    def test_invalid_create_keeps_pending_state_and_native_file(self):
        state = self._start("create")
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "bad input",
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
                persona_service=self.persona_service,
            )
        )
        self.assertEqual(self._settings(), self.native)
        current = json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat"))
        self.assertEqual(current["mode"], "create")
        self.assertEqual(current["prompt_message_ids"], state["prompt_message_ids"] + [501])

    def test_cancel_clears_pending_persona_input(self):
        self._start("create")
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "/cancel",
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
                persona_service=self.persona_service,
            )
        )
        self.assertEqual(_m_session_naming.get_meta(self.db, "persona_input:chat"), "")
        self.assertEqual(self._settings(), self.native)
        self.assertEqual(self.prompt_deletions, [{"chat_id": "chat", "message_id": 501}])

    def test_pending_persona_input_is_session_scoped(self):
        self._start("create")
        other = dict(self.session)
        other["session_id"] = "other-session"
        self.assertFalse(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                other,
                "writer | Writer | Should not apply",
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                request_context=self.request_context,
                persona_service=self.persona_service,
            )
        )
        self.assertEqual(self._settings(), self.native)

    def test_save_failure_keeps_native_settings_and_pending_state(self):
        self._start("edit_description", "bridge-user.png")
        original_save = _m_persona_sync._save_native_settings
        _m_persona_sync._save_native_settings = lambda *_args, app_settings=None: (_ for _ in ()).throw(
            RuntimeError("offline")
        )
        try:
            self.assertTrue(
                make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                    self.db,
                    "token",
                    "chat",
                    self.session,
                    "Attempted update",
                    group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                    provider_port=make_test_provider_port(),
                    memory_service=make_test_memory_service(),
                    request_context=self.request_context,
                    persona_service=self.persona_service,
                )
            )
        finally:
            _m_persona_sync._save_native_settings = original_save
        self.assertEqual(self._settings(), self.native)
        self.assertTrue(json.loads(_m_session_naming.get_meta(self.db, "persona_input:chat")))


if __name__ == "__main__":
    unittest.main()
