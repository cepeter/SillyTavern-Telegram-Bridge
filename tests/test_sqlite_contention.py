import json
from pathlib import Path
import tempfile
import threading
import time
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

    def test_shared_write_mutex_serializes_transactions(self):
        active = 0
        maximum = 0
        guard = threading.Lock()
        barrier = threading.Barrier(2)

        def transaction():
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with guard:
                active -= 1

        def invoke():
            barrier.wait()
            rt.run_write_txn(self.db, transaction)

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(maximum, 1)

        original_connect = rt.db_connect
        rt.db_connect = lambda: (_ for _ in ()).throw(AssertionError("opened nested SQLite connection"))
        try:
            token = rt.dynamic_callback_token("persona", "bridge-user.png", "chat")
        finally:
            rt.db_connect = original_connect
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM callback_tokens WHERE token=?", (token,)).fetchone())

    def test_job_writers_from_separate_connections_do_not_lock_each_other(self):
        barrier = threading.Barrier(2)
        errors = []
        old_connect = rt.db_connect

        def worker(index):
            db = old_connect()
            try:
                barrier.wait()
                job_id = rt.enqueue_job(db, 100 + index, "chat", "session", 100 + index, "generation", {"text": "x"})
                rt.finish_job(db, job_id, "done")
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs WHERE update_id>=100").fetchone()[0], 2)

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

    def test_send_message_retries_transient_not_found(self):
        calls = []
        original_urlopen = rt.urllib.request.urlopen
        original_sleep = rt.time.sleep
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return b'{"ok": true, "result": {"message_id": 901}}'
        def fake_urlopen(*_args, **_kwargs):
            calls.append(True)
            if len(calls) <= 2:
                raise rt.urllib.error.HTTPError("https://api.telegram.org", 404, "Not Found", {}, __import__("io").BytesIO(b'{"ok": false, "description": "Not Found"}'))
            return Response()
        rt.urllib.request.urlopen = fake_urlopen
        rt.time.sleep = lambda _seconds: None
        try:
            result = rt.telegram_request("token", "sendMessage", {"chat_id": "chat", "text": "panel"})
        finally:
            rt.urllib.request.urlopen = original_urlopen
            rt.time.sleep = original_sleep
        self.assertEqual(result, {"message_id": 901})
        self.assertEqual(len(calls), 3)

    def test_delivery_metadata_lock_does_not_turn_sent_reply_into_backend_failure(self):
        class LockedDb:
            def __init__(self):
                self.rolled_back = False

            def execute(self, *_args):
                raise rt.sqlite3.OperationalError("database is locked")

            def rollback(self):
                self.rolled_back = True

        db = LockedDb()
        persisted = rt.persist_assistant_delivery_ids(db, 42, [900])
        self.assertFalse(persisted)
        self.assertTrue(db.rolled_back)

    def test_native_edit_post_commit_failure_is_not_treated_as_uncommitted(self):
        operation_id = "edit-test"
        self.assertTrue(rt.begin_operation(self.db, operation_id, "edit"))
        rt.set_operation_phase(self.db, operation_id, "edit", "local_committed")
        self.assertTrue(rt.native_edit_committed_after_failure(self.db, operation_id, RuntimeError("database is locked")))
        self.assertFalse(rt.native_edit_committed_after_failure(self.db, operation_id, RuntimeError("Telegram sendMessage failed: Not Found")))

        class LockedDb:
            def __init__(self):
                self.rolled_back = False

            def execute(self, *_args):
                raise rt.sqlite3.OperationalError("database is locked")

            def rollback(self):
                self.rolled_back = True

        db = LockedDb()
        finished = rt.finish_job(db, 7, "done")
        self.assertFalse(finished)
        self.assertTrue(db.rolled_back)

    def test_enqueue_job_does_not_retry_after_full_timeout(self):
        """Regression: enqueue_job should not catch every OperationalError and retry after 30s busy timeout."""
        import inspect
        source = inspect.getsource(rt.enqueue_job)
        # Should not have BEGIN IMMEDIATE which would wait 30s on lock before retry
        self.assertNotIn("BEGIN IMMEDIATE", source, "enqueue_job should not use BEGIN IMMEDIATE to avoid 30s busy timeout retry")
        # Should not catch every OperationalError and retry with duplicate inserts
        has_catch_all_retry = "except sqlite3.OperationalError" in source and source.count("INSERT OR IGNORE INTO jobs") > 1
        self.assertFalse(has_catch_all_retry, "enqueue_job should not have catch-all OperationalError retry")

    def test_generate_and_store_reply_does_not_use_unbounded_fallback(self):
        """Regression: generate_and_store_reply should not catch every OperationalError with unbounded retry."""
        # Check that the function does not contain BEGIN IMMEDIATE + catch-all OperationalError pattern
        import inspect
        source = inspect.getsource(rt.generate_and_store_reply)
        # Should not have BEGIN IMMEDIATE that would wait 30s
        self.assertNotIn("BEGIN IMMEDIATE", source, "generate_and_store_reply should not use BEGIN IMMEDIATE to avoid 30s timeout")
        # Should not catch every OperationalError and retry
        # The fixed version should have simple inserts without try/except OperationalError retry loop
        has_catch_all_retry = "except sqlite3.OperationalError" in source and source.count("INSERT INTO messages") > 2
        self.assertFalse(has_catch_all_retry, "generate_and_store_reply should not have catch-all OperationalError retry with duplicate inserts")


if __name__ == "__main__":
    unittest.main()
