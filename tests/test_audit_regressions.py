from pathlib import Path
import sqlite3
import tempfile
import unittest

import bridge.runtime as rt


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_begin_operation_commits_prepared_marker(self):
        self.assertTrue(rt.begin_operation(self.db, 101, "test"))

        second = rt.db_connect()
        try:
            self.assertEqual(rt.operation_phase(second, 101), "in_progress")
            second.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('writer_probe','ok')"
            )
            second.commit()
        finally:
            second.close()

    def test_startup_recovery_is_bounded(self):
        now = rt.time.time()
        for index in range(200):
            self.db.execute(
                "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,attempts,last_error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'queued',0,'',?,?)",
                (
                    1000 + index,
                    "chat",
                    "session",
                    str(index),
                    "generation",
                    "{}",
                    now + index * 0.001,
                    now + index * 0.001,
                ),
            )
        self.db.commit()

        rows = rt.recover_jobs(self.db, recover_running=True)
        self.assertEqual(len(rows), 128)

    def test_swipe_callbacks_are_session_scoped(self):
        for callback_data in ("swipe:prev", "swipe:next", "swipe:keep", "swipe:cancel"):
            with self.subTest(callback_data=callback_data):
                self.assertTrue(rt.is_session_scoped_panel_callback(callback_data))

    def test_transient_worker_boot_failure_requeues_scheduled_job(self):
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,attempts,last_error,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'scheduled',0,'',?,?)",
            (999, "chat", "session", "1", "generation", "{}", now, now),
        )
        self.db.commit()
        job_id = int(
            self.db.execute("SELECT job_id FROM jobs WHERE update_id=999").fetchone()[0]
        )

        rt._requeue_worker_boot_failure(
            job_id, sqlite3.OperationalError("database is locked")
        )

        state = self.db.execute(
            "SELECT state FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        self.assertEqual(state, "queued")

    def test_export_inherits_current_operation_id(self):
        captured = {}
        original = rt._ORIGINAL_EXPORT_SESSION
        context_token = rt._OPERATION_CONTEXT.set(88)
        try:
            def fake_export(token, db, session, fields, chat_id, operation_id=None):
                captured["operation_id"] = operation_id
                return "ok"

            rt._ORIGINAL_EXPORT_SESSION = fake_export
            result = rt.export_session("token", self.db, {}, {}, "chat")
        finally:
            rt._ORIGINAL_EXPORT_SESSION = original
            rt._OPERATION_CONTEXT.reset(context_token)

        self.assertEqual(result, "ok")
        self.assertEqual(captured["operation_id"], 88)

    def test_import_replay_does_not_duplicate_messages_or_variants(self):
        original_parser = rt.parse_sillytavern_jsonl
        rt.parse_sillytavern_jsonl = lambda _raw: (
            {"name": "Imported regression chat"},
            [("user", "hello"), ("assistant", "world")],
        )
        try:
            rt.import_chat_session(
                self.db,
                "chat",
                b"{}",
                rt.DEFAULT_MODEL,
                operation_id=77,
            )
            session_id = "import-job-77"
            message_count = self.db.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                ("chat", session_id),
            ).fetchone()[0]
            variant_count = self.db.execute(
                "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                ("chat", session_id),
            ).fetchone()[0]
            self.assertEqual(message_count, 2)
            self.assertEqual(variant_count, 1)

            self.db.execute(
                "UPDATE meta SET value='created' WHERE key='import_phase:77'"
            )
            self.db.commit()
            rt.import_chat_session(
                self.db,
                "chat",
                b"{}",
                rt.DEFAULT_MODEL,
                operation_id=77,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                    ("chat", session_id),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                    ("chat", session_id),
                ).fetchone()[0],
                1,
            )

            self.db.execute(
                "UPDATE meta SET value='summary_loaded' WHERE key='import_phase:77'"
            )
            self.db.commit()
            rt.import_chat_session(
                self.db,
                "chat",
                b"{}",
                rt.DEFAULT_MODEL,
                operation_id=77,
            )
            self.assertEqual(
                self.db.execute(
                    "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                    ("chat", session_id),
                ).fetchone()[0],
                1,
            )
        finally:
            rt.parse_sillytavern_jsonl = original_parser


if __name__ == "__main__":
    unittest.main()
