import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import bridge.config as config
import bridge.database as database
import sqlite3
import time
import urllib
from dependency_patch import dependency_module

_m_main = dependency_module("bridge.main")
_m_media = dependency_module("bridge.media")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_message_commands = dependency_module("bridge.message_commands")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_session_naming = dependency_module("bridge.session_naming")
_m_sync_api = dependency_module("bridge.sync_api")
_m_telegram = dependency_module("bridge.telegram")


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
        self.old_db_file = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        _m_media.set_db_connection_context(self.db)

    def tearDown(self):
        _m_media.set_db_connection_context(None)
        self.db.close()
        config.DB_FILE = self.old_db_file
        self.tmp.cleanup()

    def test_failed_poll_update_restores_durable_offset(self):
        _m_session_naming.set_meta(self.db, "telegram_offset", "100")
        self.assertEqual(_m_main.restore_poll_offset(self.db, 101), 100)
        self.assertEqual(_m_main.restore_poll_offset(self.db, 100), 100)

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
            _m_sync_api.run_write_txn(self.db, transaction)

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(maximum, 1)

        original_connect = _m_memory_curator.db_connect
        _m_memory_curator.db_connect = lambda: (_ for _ in ()).throw(AssertionError("opened nested SQLite connection"))
        try:
            token = _m_panel_callback_routes.dynamic_callback_token("persona", "bridge-user.png", "chat")
        finally:
            _m_memory_curator.db_connect = original_connect
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM callback_tokens WHERE token=?", (token,)).fetchone())

    def test_raw_connection_writes_share_mutex(self):
        barrier = threading.Barrier(2)
        errors = []
        connect = _m_memory_curator.db_connect

        def worker(index):
            db = connect()
            try:
                barrier.wait()
                db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (f"raw-mutex-{index}", "ok"))
                time.sleep(0.01)
                db.commit()
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

    def test_begin_operation_commits_before_external_work(self):
        operation_id = "lock-release-test"
        self.assertTrue(_m_panel_callback_routes.begin_operation(self.db, operation_id, "generation"))
        self.assertFalse(self.db.in_transaction)

    def test_job_writers_from_separate_connections_do_not_lock_each_other(self):
        barrier = threading.Barrier(2)
        errors = []
        old_connect = _m_memory_curator.db_connect

        def worker(index):
            db = old_connect()
            try:
                barrier.wait()
                job_id = _m_main.enqueue_job(db, 100 + index, "chat", "session", 100 + index, "generation", {"text": "x"})
                _m_main.finish_job(db, job_id, "done")
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
        original_urlopen = urllib.request.urlopen
        original_connect = _m_memory_curator.db_connect
        original_session = _m_telegram.panel_session_context()
        original_actor = _m_telegram.panel_actor_context()
        _m_session_naming.set_panel_session_context("session")
        _m_media.set_panel_actor_context("user")
        _m_memory_curator.db_connect = lambda: (_ for _ in ()).throw(AssertionError("opened nested SQLite connection"))
        urllib.request.urlopen = lambda *_args, **_kwargs: _FakeTelegramResponse()
        try:
            result = _m_panel_callback_routes.telegram_request("token", "sendMessage", {
                "chat_id": "chat",
                "text": "panel",
                "reply_markup": {"inline_keyboard": []},
            })
            calls.append(result)
        finally:
            urllib.request.urlopen = original_urlopen
            _m_memory_curator.db_connect = original_connect
            _m_session_naming.set_panel_session_context(original_session)
            _m_media.set_panel_actor_context(original_actor)
        self.assertEqual(calls, [{"message_id": 900}])
        self.assertIsNotNone(self.db.execute(
            "SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=? AND session_id=?",
            ("chat", "900", "session"),
        ).fetchone())

    def test_send_message_retries_transient_not_found(self):
        calls = []
        original_urlopen = urllib.request.urlopen
        original_sleep = time.sleep
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
                raise urllib.error.HTTPError("https://api.telegram.org", 404, "Not Found", {}, __import__("io").BytesIO(b'{"ok": false, "description": "Not Found"}'))
            return Response()
        urllib.request.urlopen = fake_urlopen
        time.sleep = lambda _seconds: None
        try:
            result = _m_panel_callback_routes.telegram_request("token", "sendMessage", {"chat_id": "chat", "text": "panel"})
        finally:
            urllib.request.urlopen = original_urlopen
            time.sleep = original_sleep
        self.assertEqual(result, {"message_id": 901})
        self.assertEqual(len(calls), 3)

    def test_delivery_metadata_lock_does_not_turn_sent_reply_into_backend_failure(self):
        class LockedDb:
            def __init__(self):
                self.rolled_back = False

            def execute(self, *_args):
                raise sqlite3.OperationalError("database is locked")

            def rollback(self):
                self.rolled_back = True

        db = LockedDb()
        persisted = _m_media.persist_assistant_delivery_ids(db, 42, [900])
        self.assertFalse(persisted)
        self.assertTrue(db.rolled_back)

    def test_native_edit_post_commit_failure_is_not_treated_as_uncommitted(self):
        operation_id = "edit-test"
        self.assertTrue(_m_panel_callback_routes.begin_operation(self.db, operation_id, "edit"))
        _m_message_commands.set_operation_phase(self.db, operation_id, "edit", "local_committed")
        self.assertTrue(_m_main.native_edit_committed_after_failure(self.db, operation_id, RuntimeError("database is locked")))
        self.assertFalse(_m_main.native_edit_committed_after_failure(self.db, operation_id, RuntimeError("Telegram sendMessage failed: Not Found")))

        class LockedDb:
            def __init__(self):
                self.rolled_back = False

            def execute(self, *_args):
                raise sqlite3.OperationalError("database is locked")

            def rollback(self):
                self.rolled_back = True

        db = LockedDb()
        finished = _m_main.finish_job(db, 7, "done")
        self.assertFalse(finished)
        self.assertTrue(db.rolled_back)

    def test_enqueue_job_does_not_retry_after_full_timeout(self):
        """Regression: enqueue_job should not catch every OperationalError and retry after 30s busy timeout."""
        import inspect
        source = inspect.getsource(_m_main.enqueue_job)
        # Should not have BEGIN IMMEDIATE which would wait 30s on lock before retry
        self.assertNotIn("BEGIN IMMEDIATE", source, "enqueue_job should not use BEGIN IMMEDIATE to avoid 30s busy timeout retry")
        # Should not catch every OperationalError and retry with duplicate inserts
        has_catch_all_retry = "except sqlite3.OperationalError" in source and source.count("INSERT OR IGNORE INTO jobs") > 1
        self.assertFalse(has_catch_all_retry, "enqueue_job should not have catch-all OperationalError retry")

    def test_generate_and_store_reply_does_not_use_unbounded_fallback(self):
        """Regression: generate_and_store_reply should not catch every OperationalError with unbounded retry."""
        # Check that the function does not contain BEGIN IMMEDIATE + catch-all OperationalError pattern
        import inspect
        source = inspect.getsource(_m_message_commands.generate_and_store_reply)
        # Should not have BEGIN IMMEDIATE that would wait 30s
        self.assertNotIn("BEGIN IMMEDIATE", source, "generate_and_store_reply should not use BEGIN IMMEDIATE to avoid 30s timeout")
        # Should not catch every OperationalError and retry
        # The fixed version should have simple inserts without try/except OperationalError retry loop
        has_catch_all_retry = "except sqlite3.OperationalError" in source and source.count("INSERT INTO messages") > 2
        self.assertFalse(has_catch_all_retry, "generate_and_store_reply should not have catch-all OperationalError retry with duplicate inserts")

    def test_explicit_path_workers_still_use_serialized_connection(self):
        path = Path(self.tmp.name) / "explicit-worker.sqlite3"
        db = database.db_connect(path)
        try:
            self.assertIsInstance(db, database._SerializedSQLiteConnection)
            self.assertEqual(
                db.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "wal",
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
