import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import bridge.runtime as rt
from bridge.sync_service import SyncStatus


class _ApiHandler(BaseHTTPRequestHandler):
    records = []
    seen = []
    settings = {"power_user": {"personas": {}, "persona_descriptions": {}}}

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
            session_id="phase3",
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
        self.calls.append(
            ("toggle_realtime", db, chat_id, session_id)
        )
        return self.toggle_result


class Phase3SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        self.old_url = rt.PHASE3_SYNC_API_URL
        self.old_handle = rt.PHASE3_SYNC_API_HANDLE
        self.old_password = rt.PHASE3_SYNC_API_PASSWORD
        self.old_interval = rt.PHASE3_SYNC_INTERVAL_SECONDS
        self.old_timeout = rt.PHASE3_SYNC_TIMEOUT_SECONDS
        self.old_client = rt.phase3_client
        self.old_card = rt.card_fields_from_file
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        rt.PHASE3_SYNC_API_URL = "http://127.0.0.1:8000"
        rt.card_fields_from_file = lambda _name: {"name": "Test", "first_mes": "", "description": "", "personality": "", "scenario": ""}
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", "provider/model", session_id="phase3")
        self.fake = _FakeApi()
        rt.phase3_client = lambda: self.fake

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        rt.PHASE3_SYNC_API_URL = self.old_url
        rt.PHASE3_SYNC_API_HANDLE = self.old_handle
        rt.PHASE3_SYNC_API_PASSWORD = self.old_password
        rt.PHASE3_SYNC_INTERVAL_SECONDS = self.old_interval
        rt.PHASE3_SYNC_TIMEOUT_SECONDS = self.old_timeout
        rt.phase3_client = self.old_client
        rt.card_fields_from_file = self.old_card
        self.tmp.cleanup()

    def _add(self, content):
        self.db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", ("chat", "phase3", "user", content, rt.time.time()))
        self.db.commit()

    def test_loopback_url_policy(self):
        self.assertEqual(rt.validate_phase3_api_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        for value in ("http://0.0.0.0:8000", "https://example.com", "http://user:pass@127.0.0.1:8000", "http://127.0.0.1:8000/path"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rt.validate_phase3_api_url(value)

    def test_phase3_config_refreshes_after_env_load_and_bad_numbers_are_safe(self):
        with patch.dict(rt.os.environ, {"SILLYTAVERN_SYNC_API_URL": "http://localhost:8123", "SILLYTAVERN_SYNC_API_HANDLE": "tester", "SILLYTAVERN_SYNC_API_PASSWORD": "secret", "SILLYTAVERN_SYNC_API_INTERVAL_SECONDS": "bad", "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS": "bad"}, clear=False):
            rt.refresh_phase3_config()
        self.assertEqual(rt.PHASE3_SYNC_API_URL, "http://localhost:8123")
        self.assertEqual(rt.PHASE3_SYNC_API_HANDLE, "tester")
        self.assertEqual(rt.PHASE3_SYNC_API_PASSWORD, "secret")
        self.assertEqual(rt.PHASE3_SYNC_INTERVAL_SECONDS, 2.0)
        self.assertEqual(rt.PHASE3_SYNC_TIMEOUT_SECONDS, 10)

    def test_http_client_uses_cookie_login_and_csrf(self):
        _ApiHandler.records, _ApiHandler.seen = [], []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = rt.SillyTavernApiClient(f"http://127.0.0.1:{server.server_port}", "tester", "password")
            session = {"character_file": "Test.png"}
            client.save_chat(session, {"name": "Test"}, "chat-id", False, [{"chat_metadata": {}}, {"is_user": True, "mes": "Hello"}])
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
            client = rt.SillyTavernApiClient(f"http://127.0.0.1:{server.server_port}")
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
        self.assertIn("realtime API sync enabled", rt.phase3_toggle_realtime(self.db, "chat", "phase3"))
        self.assertEqual(self.fake.saved, 1)
        self.fake.records[1]["mes"] = "Edited in SillyTavern"
        self.assertEqual(rt.phase3_sync_now(self.db, "chat", "phase3"), "imported SillyTavern API changes")
        stored = self.db.execute("SELECT content FROM messages WHERE session_id='phase3'").fetchone()[0]
        self.assertEqual(stored, "Edited in SillyTavern")
        self.db.execute("UPDATE messages SET content='Edited in Telegram' WHERE session_id='phase3'")
        self.db.commit()
        self.assertEqual(rt.phase3_sync_now(self.db, "chat", "phase3"), "exported bridge changes through API")
        self.assertEqual(self.fake.records[1]["mes"], "Edited in Telegram")
        self.db.execute("UPDATE messages SET content='Local conflict' WHERE session_id='phase3'")
        self.db.commit()
        self.fake.records[1]["mes"] = "Remote conflict"
        self.assertEqual(rt.phase3_sync_now(self.db, "chat", "phase3"), "conflict detected; realtime stopped")
        binding = rt.sync_binding(self.db, "chat", "phase3")
        self.assertEqual(binding["realtime_enabled"], 0)
        self.assertEqual(binding["conflict"], "conflict")

    def test_auth_failure_disables_realtime(self):
        self._add("Original")
        rt.ensure_sync_binding(self.db, "chat", "phase3")
        self.db.execute("UPDATE sync_bindings SET realtime_enabled=1 WHERE chat_id='chat' AND session_id='phase3'")
        self.db.commit()
        self.fake.error = rt.SillyTavernApiError("authentication failed", status=403)
        rt.phase3_sync_poll(self.db)
        binding = rt.sync_binding(self.db, "chat", "phase3")
        self.assertEqual(binding["realtime_enabled"], 0)

    def test_sync_status_text_renders_injected_service_status(self):
        fake = _FakeSyncService()

        text = rt.sync_status_text(
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
            [("status", self.db, "chat", "phase3")],
        )

    def test_sync_now_callback_uses_injected_service(self):
        fake = _FakeSyncService()
        answers = []
        callback = {
            "id": "cb",
            "message": {"message_id": 90, "chat": {"id": "chat"}},
        }
        with patch.object(
            rt,
            "phase3_sync_now",
            side_effect=AssertionError("raw sync bypassed service"),
        ), patch.object(
            rt,
            "_phase3_disable",
            side_effect=AssertionError("raw disable bypassed service"),
        ), patch.object(
            rt,
            "send_sync_menu",
            return_value=None,
        ):
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:now",
                "chat",
                callback["message"],
                self.session,
                "phase3",
                None,
                sync_service=fake,
            )

        self.assertTrue(handled)
        self.assertEqual(fake.calls[0][0], "sync_now")
        self.assertEqual(fake.calls[0][2:], ("chat", "phase3"))
        self.assertEqual(answers, ["unchanged"])

    def test_sync_realtime_callback_uses_injected_service(self):
        fake = _FakeSyncService()
        answers = []
        callback = {
            "id": "cb",
            "message": {"message_id": 90, "chat": {"id": "chat"}},
        }
        with patch.object(
            rt,
            "phase3_toggle_realtime",
            side_effect=AssertionError("raw toggle bypassed service"),
        ), patch.object(
            rt,
            "send_sync_menu",
            return_value=None,
        ):
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:realtime",
                "chat",
                callback["message"],
                self.session,
                "phase3",
                None,
                sync_service=fake,
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
        with patch.object(rt, "send_sync_menu", return_value=None):
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                callback,
                lambda _token, _id, text: answers.append(text),
                "sync:now",
                "chat",
                callback["message"],
                self.session,
                "phase3",
                None,
                sync_service=fake,
            )

        self.assertTrue(handled)
        self.assertEqual(
            answers,
            ["Live API unavailable: API unavailable"],
        )

    def test_sync_panel_exposes_realtime_control(self):
        calls = []
        original = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_sync_menu("token", "chat", self.db, self.session)
        finally:
            rt.telegram_request = original
        callbacks = {button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertIn("sync:realtime", callbacks)
        self.assertNotIn("sync:auto", callbacks)
        self.assertIn("Live API sync", calls[-1][1]["text"])

    def test_manual_sync_now_failure_disables_realtime_without_callback_error(self):
        rt.ensure_sync_binding(self.db, "chat", "phase3")
        self.db.execute("UPDATE sync_bindings SET realtime_enabled=1 WHERE chat_id='chat' AND session_id='phase3'")
        self.db.commit()
        original_sync = rt.phase3_sync_now
        original_menu = rt.send_sync_menu
        rt.phase3_sync_now = lambda *_args: (_ for _ in ()).throw(rt.SillyTavernApiError("API unavailable", transient=True))
        rt.send_sync_menu = lambda *_args, **_kwargs: None
        answers = []
        callback = {"id": "cb", "message": {"message_id": 90, "chat": {"id": "chat"}}}
        try:
            handled = rt.handle_sync_callback(self.db, "token", callback, lambda _token, _id, text: answers.append(text), "sync:now", "chat", callback["message"], self.session, "phase3", None)
        finally:
            rt.phase3_sync_now = original_sync
            rt.send_sync_menu = original_menu
        self.assertTrue(handled)
        self.assertIn("Live API unavailable", answers[0])
        self.assertEqual(rt.sync_binding(self.db, "chat", "phase3")["realtime_enabled"], 0)


if __name__ == "__main__":
    unittest.main()
