from application_test_setup import (
    ensure_application_extensions,
    make_native_test_sync_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.sync_callbacks as _owner_sync_callbacks

ensure_application_extensions()

import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import bridge.cards as _m_cards
import bridge.memory_curator as _m_memory_curator
import bridge.session_core as _m_telegram
import bridge.session_naming as _m_session_naming
import bridge.sillytavern_api as _m_sillytavern_api
import bridge.status_panels as _m_status_panels
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
from bridge.sync_service import SyncStatus


class _ApiHandler(BaseHTTPRequestHandler):
    records: ClassVar[list] = []
    seen: ClassVar[list] = []
    settings: ClassVar[dict] = {"power_user": {"personas": {}, "persona_descriptions": {}}}

    def log_message(self, *_args):
        return

    def _json(self, payload, cookie=False):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", "session=test; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/csrf-token":
            self._json({"token": "csrf-test"}, cookie=True)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.seen.append((self.path, dict(self.headers), body))
        if self.headers.get("X-CSRF-Token") != "csrf-test" or "session=test" not in self.headers.get("Cookie", ""):
            self.send_error(403)
            return
        if self.path == "/api/users/login":
            self._json({"handle": body.get("handle")})
        elif self.path == "/api/ping":
            self._json({})
        elif self.path == "/api/chats/get":
            self._json(copy.deepcopy(self.__class__.records))
        elif self.path == "/api/chats/save":
            self.__class__.records = copy.deepcopy(body["chat"])
            self._json({"ok": True})
        elif self.path == "/api/settings/get":
            self._json({"settings": json.dumps(self.__class__.settings)})
        elif self.path == "/api/settings/save":
            self.__class__.settings = copy.deepcopy(body)
            self._json({"result": "ok"})
        else:
            self.send_error(404)


class _FakeApi:
    def __init__(self):
        self.records = []
        self.saved = 0
        self.error = None

    def get_chat(self, _session, _file_id, _is_group):
        if self.error:
            raise self.error
        return copy.deepcopy(self.records)

    def save_chat(self, _session, _fields, _file_id, _is_group, records):
        if self.error:
            raise self.error
        self.records = copy.deepcopy(records)
        self.saved += 1


class _FakeSyncService:
    def __init__(self):
        self.calls = []
        self.sync_result = "unchanged"
        self.toggle_result = "realtime API sync disabled"
        self.status_value = SyncStatus(
            session_id="live-sync",
            message_count=9,
            sync_id="stb-injected",
            last_synced_at=0.0,
            last_direction="",
            realtime_enabled=True,
            api_configured=False,
        )

    def status(self, db, chat_id, session_id):
        self.calls.append(("status", db, chat_id, session_id))
        return self.status_value

    def sync_now(self, db, chat_id, session_id):
        self.calls.append(("sync_now", db, chat_id, session_id))
        return self.sync_result

    def toggle_realtime(self, db, chat_id, session_id):
        self.calls.append(("toggle_realtime", db, chat_id, session_id))
        return self.toggle_result


class Phase3SyncTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.old_url = self.app_settings_builder.live_sync_api_url
        self.old_handle = self.app_settings_builder.live_sync_api_handle
        self.old_password = self.app_settings_builder.live_sync_api_password
        self.old_interval = self.app_settings_builder.live_sync_interval_seconds
        self.old_timeout = self.app_settings_builder.live_sync_timeout_seconds
        self.old_client = _m_sillytavern_api.live_sync_client
        self.old_sync_api_card = _m_sync_api.card_fields_from_file
        self.old_sync_core_card = _m_sync_core.card_fields_from_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.app_settings_builder.live_sync_api_url = "http://127.0.0.1:8000"

        def card_stub(_name, *, app_settings=None):
            return {
                "name": "Test",
                "first_mes": "",
                "description": "",
                "personality": "",
                "scenario": "",
            }

        _m_sync_api.card_fields_from_file = card_stub
        _m_sync_core.card_fields_from_file = card_stub
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db, "chat", "provider/model", session_id="live-sync", app_settings=self.app_settings_builder.build()
        )
        self.fake = _FakeApi()
        _m_sillytavern_api.live_sync_client = lambda *, app_settings=None, retain_memory=None: self.fake

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.app_settings_builder.live_sync_api_url = self.old_url
        self.app_settings_builder.live_sync_api_handle = self.old_handle
        self.app_settings_builder.live_sync_api_password = self.old_password
        self.app_settings_builder.live_sync_interval_seconds = self.old_interval
        self.app_settings_builder.live_sync_timeout_seconds = self.old_timeout
        _m_sillytavern_api.live_sync_client = self.old_client
        _m_sync_api.card_fields_from_file = self.old_sync_api_card
        _m_sync_core.card_fields_from_file = self.old_sync_core_card
        self.tmp.cleanup()

    def _add(self, content):
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "live-sync", "user", content, time.time()),
        )
        self.db.commit()

    def test_loopback_url_policy(self):
        self.assertEqual(
            _m_sillytavern_api.validate_live_sync_api_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000"
        )
        for value in (
            "http://0.0.0.0:8000",
            "https://example.com",
            "http://user:pass@127.0.0.1:8000",
            "http://127.0.0.1:8000/path",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _m_sillytavern_api.validate_live_sync_api_url(value)

    def test_live_sync_settings_are_loaded_explicitly_and_reject_bad_numbers(self):
        from bridge.config_values import ConfigurationError
        from bridge.settings import load_app_settings

        values = {
            "SILLYTAVERN_SYNC_API_URL": "http://localhost:8123",
            "SILLYTAVERN_SYNC_API_HANDLE": "tester",
            "SILLYTAVERN_SYNC_API_PASSWORD": "secret",
        }
        settings = load_app_settings(values, home=Path(self.tmp.name))
        self.assertEqual(settings.live_sync_api_url, "http://localhost:8123")
        self.assertEqual(settings.live_sync_api_handle, "tester")
        self.assertEqual(settings.live_sync_api_password, "secret")
        self.assertEqual(settings.live_sync_interval_seconds, 2.0)
        self.assertEqual(settings.live_sync_timeout_seconds, 10)
        for key in ("SILLYTAVERN_SYNC_API_INTERVAL_SECONDS", "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS"):
            with self.subTest(key=key), self.assertRaisesRegex(ConfigurationError, key):
                load_app_settings(dict(values, **{key: "bad"}), home=Path(self.tmp.name))

    def test_http_client_uses_cookie_login_and_csrf(self):
        _ApiHandler.records, _ApiHandler.seen = [], []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = _m_sillytavern_api.SillyTavernApiClient(
                f"http://127.0.0.1:{server.server_port}", "tester", "password"
            )
            session = {"character_file": "Test.png"}
            client.save_chat(
                session, {"name": "Test"}, "chat-id", False, [{"chat_metadata": {}}, {"is_user": True, "mes": "Hello"}]
            )
            records = client.get_chat(session, "chat-id", False)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.assertEqual(records[1]["mes"], "Hello")
        paths = [item[0] for item in _ApiHandler.seen]
        self.assertEqual(paths, ["/api/users/login", "/api/ping", "/api/chats/save", "/api/chats/get"])

    def test_http_client_reads_and_saves_native_persona_settings(self):
        _ApiHandler.settings = {"power_user": {"personas": {}, "persona_descriptions": {}}, "unchanged": True}
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = _m_sillytavern_api.SillyTavernApiClient(f"http://127.0.0.1:{server.server_port}")
            settings = client.get_settings()
            settings["power_user"]["personas"]["user-default.png"] = "Test User"
            client.save_settings(settings)
            self.assertEqual(client.get_settings()["power_user"]["personas"]["user-default.png"], "Test User")
            self.assertTrue(client.get_settings()["unchanged"])
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_realtime_round_trip_and_conflict_stop(self):
        self._add("Original")
        self.assertIn(
            "realtime API sync enabled",
            _m_sync_api.live_sync_toggle_realtime(
                self.db,
                "chat",
                "live-sync",
                app_settings=self.app_settings_builder.build(),
                retain_memory=lambda _db, _chat, _session, _fields: None,
            ),
        )
        self.assertEqual(self.fake.saved, 1)
        self.fake.records[1]["mes"] = "Edited in SillyTavern"
        self.assertEqual(
            _m_sync_api.live_sync_now(
                self.db,
                "chat",
                "live-sync",
                app_settings=self.app_settings_builder.build(),
                retain_memory=lambda _db, _chat, _session, _fields: None,
            ),
            "imported SillyTavern API changes",
        )
        stored = self.db.execute("SELECT content FROM messages WHERE session_id='live-sync'").fetchone()[0]
        self.assertEqual(stored, "Edited in SillyTavern")
        self.db.execute("UPDATE messages SET content='Edited in Telegram' WHERE session_id='live-sync'")
        self.db.commit()
        self.assertEqual(
            _m_sync_api.live_sync_now(
                self.db,
                "chat",
                "live-sync",
                app_settings=self.app_settings_builder.build(),
                retain_memory=lambda _db, _chat, _session, _fields: None,
            ),
            "exported bridge changes through API",
        )
        self.assertEqual(self.fake.records[1]["mes"], "Edited in Telegram")
        self.db.execute("UPDATE messages SET content='Local conflict' WHERE session_id='live-sync'")
        self.db.commit()
        self.fake.records[1]["mes"] = "Remote conflict"
        self.assertEqual(
            _m_sync_api.live_sync_now(
                self.db,
                "chat",
                "live-sync",
                app_settings=self.app_settings_builder.build(),
                retain_memory=lambda _db, _chat, _session, _fields: None,
            ),
            "conflict detected; realtime stopped",
        )
        binding = _m_sync_api.sync_binding(self.db, "chat", "live-sync")
        self.assertEqual(binding["realtime_enabled"], 0)
        self.assertEqual(binding["conflict"], "conflict")

    def test_auth_failure_disables_realtime(self):
        self._add("Original")
        _m_sync_core.ensure_sync_binding(self.db, "chat", "live-sync")
        self.db.execute("UPDATE sync_bindings SET realtime_enabled=1 WHERE chat_id='chat' AND session_id='live-sync'")
        self.db.commit()
        self.fake.error = _m_sillytavern_api.SillyTavernApiError("authentication failed", status=403)
        _m_sync_api.live_sync_poll(
            self.db,
            app_settings=self.app_settings_builder.build(),
            retain_memory=lambda _db, _chat, _session, _fields: None,
        )
        binding = _m_sync_api.sync_binding(self.db, "chat", "live-sync")
        self.assertEqual(binding["realtime_enabled"], 0)

    def test_sync_status_text_renders_injected_service_status(self):
        fake = _FakeSyncService()

        text = _m_status_panels.sync_status_text(
            self.db,
            "chat",
            self.session,
            sync_service=fake,
        )

        self.assertIn("Messages: 9", text)
        self.assertIn("Sync ID: stb-injected", text)
        self.assertIn(
            "Live API sync: on (not configured)",
            text,
        )
        self.assertEqual(
            fake.calls,
            [("status", self.db, "chat", "live-sync")],
        )

    def test_sync_now_callback_uses_injected_service(self):
        fake = _FakeSyncService()
        answers = []
        callback = {
            "id": "cb",
            "message": {"message_id": 90, "chat": {"id": "chat"}},
        }
        with (
            patch.object(
                _m_sync_api,
                "live_sync_now",
                side_effect=AssertionError("raw sync bypassed service"),
            ),
            patch.object(
                _m_sync_api,
                "_live_sync_disable",
                side_effect=AssertionError("raw disable bypassed service"),
            ),
            patch.object(
                _owner_sync_callbacks,
                "send_sync_menu",
                return_value=None,
            ),
        ):
            handled = _owner_sync_callbacks.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:now",
                "chat",
                callback["message"],
                self.session,
                "live-sync",
                None,
                sync_service=fake,
                request_context=make_test_request_context(
                    self.db, "live-sync", app_settings=self.app_settings_builder.build()
                ),
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[0][0], "sync_now")
        self.assertEqual(fake.calls[0][2:], ("chat", "live-sync"))
        self.assertEqual(answers, ["unchanged"])

    def test_sync_realtime_callback_uses_injected_service(self):
        fake = _FakeSyncService()
        answers = []
        callback = {
            "id": "cb",
            "message": {"message_id": 90, "chat": {"id": "chat"}},
        }
        with (
            patch.object(
                _m_sync_api,
                "live_sync_toggle_realtime",
                side_effect=AssertionError("raw toggle bypassed service"),
            ),
            patch.object(
                _owner_sync_callbacks,
                "send_sync_menu",
                return_value=None,
            ),
        ):
            handled = _owner_sync_callbacks.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:realtime",
                "chat",
                callback["message"],
                self.session,
                "live-sync",
                None,
                sync_service=fake,
                request_context=make_test_request_context(
                    self.db, "live-sync", app_settings=self.app_settings_builder.build()
                ),
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[0][0], "toggle_realtime")
        self.assertEqual(answers, ["realtime API sync disabled"])

    def test_sync_callback_preserves_service_manual_failure_feedback(self):
        fake = _FakeSyncService()
        fake.sync_result = "Live API unavailable: API unavailable"
        answers = []
        callback = {
            "id": "cb",
            "message": {"message_id": 90, "chat": {"id": "chat"}},
        }
        with patch.object(_owner_sync_callbacks, "send_sync_menu", return_value=None):
            handled = _owner_sync_callbacks.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:now",
                "chat",
                callback["message"],
                self.session,
                "live-sync",
                None,
                sync_service=fake,
                request_context=make_test_request_context(
                    self.db, "live-sync", app_settings=self.app_settings_builder.build()
                ),
            )

        self.assertTrue(handled)
        self.assertEqual(
            answers,
            ["Live API unavailable: API unavailable"],
        )

    def test_service_backed_import_uses_final_state_integrity_snapshot(self):
        self._add("Original")
        self.db.execute(
            "UPDATE sessions SET persona_id=?,world_file=? WHERE chat_id=? AND session_id=?",
            (
                "existing.png",
                '["existing.json"]',
                "chat",
                "live-sync",
            ),
        )
        self.db.commit()
        self.session = _m_memory_curator.load_session(
            self.db,
            "chat",
            "live-sync",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertIn(
            "realtime API sync enabled",
            _m_sync_api.live_sync_toggle_realtime(
                self.db,
                "chat",
                "live-sync",
                app_settings=self.app_settings_builder.build(),
                retain_memory=lambda _db, _chat, _session, _fields: None,
            ),
        )
        metadata = self.fake.records[0]["chat_metadata"]
        metadata["persona"] = ""
        metadata["world_info"] = []
        self.fake.records[1]["mes"] = "Remote edit"

        retained = []

        def retain_memory(*args, app_settings=None, **kwargs):
            return retained.append((args, kwargs))

        result = make_native_test_sync_service(
            app_settings=self.app_settings_builder.build(), retain_memory=retain_memory
        ).sync_now(
            self.db,
            "chat",
            "live-sync",
        )

        self.assertEqual(
            result,
            "imported SillyTavern API changes",
        )
        refreshed = _m_memory_curator.load_session(
            self.db,
            "chat",
            "live-sync",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(refreshed["persona_id"], "")
        self.assertEqual(refreshed["world_file"], "")
        self.assertEqual(len(retained), 1)

    def test_public_snapshot_absent_persona_and_world_preserve_assignments(self):
        self.db.execute(
            "UPDATE sessions SET persona_id=?,world_file=? WHERE chat_id=? AND session_id=?",
            (
                "existing.png",
                '["existing.json"]',
                "chat",
                "live-sync",
            ),
        )
        self.db.commit()

        def retain_memory(_db, _chat, _session, _fields):
            return None

        with (
            patch.object(
                _m_telegram,
                "get_persona",
                side_effect=lambda persona_id, *, app_settings=None, retain_memory=None: (
                    {"name": "Existing"} if persona_id == "existing.png" else None
                ),
            ),
            patch.object(
                _m_telegram,
                "safe_world_path",
                return_value=True,
            ),
            patch.object(
                _m_telegram,
                "active_world_files",
                return_value=["existing.json"],
            ),
            patch.object(
                _m_sync_core,
                "card_fields_from_file",
                return_value={"name": "Test"},
            ),
        ):
            current = _m_memory_curator.load_session(
                self.db,
                "chat",
                "live-sync",
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )
            result = _m_sync_api.apply_sync_snapshot(
                self.db,
                "chat",
                current,
                {"name": "Remote"},
                [("user", "remote transcript")],
                {},
                app_settings=self.app_settings_builder.build(),
                retain_memory=retain_memory,
            )
            refreshed = _m_memory_curator.load_session(
                self.db,
                "chat",
                "live-sync",
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(
            refreshed["persona_id"],
            "existing.png",
        )
        self.assertEqual(
            refreshed["world_file"],
            '["existing.json"]',
        )
        self.assertEqual(
            result,
            _m_sync_api.sync_transcript_hash([("user", "remote transcript")]),
        )

    def test_public_snapshot_uses_final_runtime_memory_collaborators(self):
        current = _m_memory_curator.load_session(
            self.db,
            "chat",
            "live-sync",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
        retained = []

        def retain_memory(db, chat_id, session, fields, *, app_settings=None, retain_memory=None):
            return retained.append(
                (
                    db,
                    chat_id,
                    session["session_id"],
                    fields["name"],
                )
            )

        with patch.object(
            _m_sync_core,
            "card_fields_from_file",
            return_value={"name": "patched-card"},
        ):
            _m_sync_api.apply_sync_snapshot(
                self.db,
                "chat",
                current,
                {},
                [("user", "remote transcript")],
                {},
                app_settings=self.app_settings_builder.build(),
                retain_memory=retain_memory,
            )

        self.assertEqual(
            retained,
            [
                (
                    self.db,
                    "chat",
                    "live-sync",
                    "patched-card",
                )
            ],
        )

    def test_direct_integrity_adapter_uses_final_runtime_memory_collaborators(self):
        current = _m_memory_curator.load_session(
            self.db,
            "chat",
            "live-sync",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
        retained = []

        def retain_memory(db, chat_id, session, fields, *, app_settings=None, retain_memory=None):
            return retained.append(
                (
                    db,
                    chat_id,
                    session["session_id"],
                    fields["name"],
                )
            )

        with patch.object(
            _m_sync_core,
            "card_fields_from_file",
            return_value={"name": "patched-card"},
        ):
            _m_sync_core._make_sync_snapshot_integrity(
                app_settings=self.app_settings_builder.build(), retain_memory=retain_memory
            ).apply(
                self.db,
                "chat",
                current,
                {},
                [("user", "remote transcript")],
                {},
            )

        self.assertEqual(
            retained,
            [
                (
                    self.db,
                    "chat",
                    "live-sync",
                    "patched-card",
                )
            ],
        )

    def test_sync_panel_exposes_realtime_control(self):
        calls = []
        original = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _owner_sync_callbacks.send_sync_menu(
                "token",
                "chat",
                self.db,
                self.session,
                sync_service=make_native_test_sync_service(app_settings=self.app_settings_builder.build()),
                request_context=make_test_request_context(
                    self.db, "live-sync", app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_cards.send_panel_request = original
        callbacks = {
            button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row
        }
        self.assertIn("sync:realtime", callbacks)
        self.assertNotIn("sync:auto", callbacks)
        self.assertIn("Live API sync", calls[-1][1]["text"])

    def test_manual_sync_now_failure_disables_realtime_without_callback_error(self):
        _m_sync_core.ensure_sync_binding(self.db, "chat", "live-sync")
        self.db.execute("UPDATE sync_bindings SET realtime_enabled=1 WHERE chat_id='chat' AND session_id='live-sync'")
        self.db.commit()
        original_sync = _m_sync_api.live_sync_now
        original_menu = _owner_sync_callbacks.send_sync_menu
        _m_sync_api.live_sync_now = lambda *_args, app_settings=None, retain_memory=None: (_ for _ in ()).throw(
            _m_sillytavern_api.SillyTavernApiError("API unavailable", transient=True)
        )
        _owner_sync_callbacks.send_sync_menu = lambda *_args, **_kwargs: None
        answers = []
        callback = {"id": "cb", "message": {"message_id": 90, "chat": {"id": "chat"}}}
        try:
            handled = _owner_sync_callbacks.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:now",
                "chat",
                callback["message"],
                self.session,
                "live-sync",
                None,
                sync_service=make_native_test_sync_service(app_settings=self.app_settings_builder.build()),
                request_context=make_test_request_context(
                    self.db, "live-sync", app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_sync_api.live_sync_now = original_sync
            _owner_sync_callbacks.send_sync_menu = original_menu
        self.assertTrue(handled)
        self.assertIn("Live API unavailable", answers[0])
        self.assertEqual(_m_sync_api.sync_binding(self.db, "chat", "live-sync")["realtime_enabled"], 0)


class SyncSnapshotOwnershipTests(SettingsTestCase):
    def test_sync_core_composes_snapshot_integrity_adapter(self):
        source = (Path(__file__).parents[1] / "bridge" / "sync_core.py").read_text(encoding="utf-8")

        self.assertIn(
            "return _SyncSnapshotIntegrityAdapter(",
            source,
        )
        self.assertIn(
            "apply_backend=_partial(_apply_sync_snapshot_backend, app_settings=app_settings),",
            source,
        )
        self.assertIn(
            "def _apply_sync_snapshot_backend(",
            source,
        )
        self.assertIn(
            "def apply_sync_snapshot(",
            source,
        )

    def test_state_integrity_module_is_retired(self):
        path = Path(__file__).parents[1] / "bridge" / "state_integrity.py"
        self.assertFalse(path.exists())

    def test_no_original_apply_sync_snapshot_capture_remains(self):
        root = Path(__file__).parents[1] / "bridge"
        offenders = []
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "_ORIGINAL_APPLY_SYNC_SNAPSHOT" in source:
                offenders.append(path.name)

        self.assertEqual(offenders, [])


class SyncPollOwnershipTests(SettingsTestCase):
    def test_sync_api_composes_poll_safety_adapter(self):
        source = (Path(__file__).parents[1] / "bridge" / "sync_api.py").read_text(encoding="utf-8")

        self.assertIn(
            "SyncPollSafetyAdapter as _SyncPollSafetyAdapter",
            source,
        )
        self.assertIn(
            "return _SyncPollSafetyAdapter(",
            source,
        )

    def test_sync_safety_module_is_retired(self):
        path = Path(__file__).parents[1] / "bridge" / "sync_safety.py"
        self.assertFalse(path.exists())

    def test_no_sync_safety_original_captures_remain(self):
        root = Path(__file__).parents[1] / "bridge"
        offenders = []
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA" in source or "_ORIGINAL_LIVE_SYNC_NOW_FOR_POLL" in source:
                offenders.append(path.name)

        self.assertEqual(offenders, [])


class SyncWorkerInjectionTests(SettingsTestCase):
    class FakeStopEvent:
        def __init__(self, results):
            self.results = iter(results)

        def wait(self, _timeout):
            return next(self.results)

    class FakeDb:
        def __init__(self):
            self.in_transaction = False
            self.closed = False
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1
            self.in_transaction = False

        def close(self):
            self.closed = True

    def test_worker_loop_polls_through_injected_service(self):
        db = self.FakeDb()
        calls = []

        class FakeSync:
            def poll(self, actual_db):
                calls.append(actual_db)

        with (
            patch.object(
                _m_sync_api,
                "_LIVE_SYNC_STOP_EVENT",
                self.FakeStopEvent([False, True]),
            ),
            patch.object(
                _m_sync_api,
                "db_connect",
                return_value=db,
            ),
            patch.object(
                _m_sync_api,
                "live_sync_poll",
                side_effect=AssertionError("raw poll bypassed service"),
            ),
        ):
            _m_sync_api._live_sync_worker_loop(sync_service=FakeSync(), app_settings=self.app_settings_builder.build())

        self.assertEqual(calls, [db])
        self.assertTrue(db.closed)

    def test_worker_reconnects_after_sqlite_poll_failure(self):
        first = self.FakeDb()
        second = self.FakeDb()
        connections = iter([first, second])
        seen = []

        class FakeSync:
            def poll(self, db):
                seen.append(db)
                if db is first:
                    raise sqlite3.OperationalError("database is locked")

        with (
            patch.object(
                _m_sync_api,
                "_LIVE_SYNC_STOP_EVENT",
                self.FakeStopEvent([False, False, True]),
            ),
            patch.object(
                _m_sync_api,
                "db_connect",
                side_effect=lambda *, app_settings=None, retain_memory=None: next(connections),
            ),
        ):
            _m_sync_api._live_sync_worker_loop(sync_service=FakeSync(), app_settings=self.app_settings_builder.build())

        self.assertEqual(seen, [first, second])
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)

    def test_worker_requires_injected_sync_service(self):
        with self.assertRaises(TypeError):
            _m_sync_api._live_sync_worker_loop(app_settings=self.app_settings_builder.build())


if __name__ == "__main__":
    unittest.main()
