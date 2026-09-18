import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class _FakeTelegramResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"ok": True, "result": {"message_id": 900}}).encode("utf-8")


class SqliteContentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_file = rt.DB_FILE
        self.old_schema_ready = rt._DB_SCHEMA_READY
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        rt.set_db_connection_context(self.db)

    def tearDown(self):
        rt.set_db_connection_context(None)
        self.db.close()
        rt.DB_FILE = self.old_db_file
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = self.old_schema_ready
        self.tmp.cleanup()

    def test_callback_token_reuses_job_connection(self):
        original_connect = rt.db_connect
        rt.db_connect = lambda: (_ for _ in ()).throw(AssertionError("opened nested SQLite connection"))
        try:
            token = rt.dynamic_callback_token("persona", "bridge-user.png", "chat")
        finally:
            rt.db_connect = original_connect
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM callback_tokens WHERE token=?", (token,)).fetchone())

    def test_panel_binding_reuses_job_connection(self):
        calls = []
        original_urlopen = rt.urllib.request.urlopen
        original_connect = rt.db_connect
        original_session = rt.panel_session_context()
        original_actor = rt.panel_actor_context()
        rt.set_panel_session_context("session")
        rt.set_panel_actor_context("user")
        rt.db_connect = lambda: (_ for _ in ()).throw(AssertionError("opened nested SQLite connection"))
        rt.urllib.request.urlopen = lambda *_args, **_kwargs: _FakeTelegramResponse()
        try:
            result = rt.telegram_request("token", "sendMessage", {
                "chat_id": "chat",
                "text": "panel",
                "reply_markup": {"inline_keyboard": []},
            })
            calls.append(result)
        finally:
            rt.urllib.request.urlopen = original_urlopen
            rt.db_connect = original_connect
            rt.set_panel_session_context(original_session)
            rt.set_panel_actor_context(original_actor)
        self.assertEqual(calls, [{"message_id": 900}])
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=? AND session_id=?",
            ("chat", "900", "session"),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
