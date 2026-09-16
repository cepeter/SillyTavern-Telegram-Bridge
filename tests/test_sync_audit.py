from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class SyncAuditHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _binding(self, chat_id="chat", session_id="session"):
        self.db.execute(
            "INSERT OR REPLACE INTO sync_bindings("
            "chat_id,session_id,sync_id,auto_enabled,realtime_enabled,"
            "realtime_failures,realtime_next_retry_at,last_checked_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                f"stb-{session_id.encode().hex()[:32]:0<32}",
                1,
                1,
                0,
                0,
                0,
            ),
        )
        self.db.commit()

    def test_phase2_auto_sync_skips_chat_with_active_job_lock(self):
        self._binding()
        calls = []
        original = rt._ORIGINAL_PHASE2_SYNC_NOW_FOR_POLL
        rt._ORIGINAL_PHASE2_SYNC_NOW_FOR_POLL = (
            lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        )
        lock = rt.chat_job_lock("chat")
        lock.acquire()
        try:
            rt.phase2_sync_poll(self.db)
            self.assertEqual(calls, [])
        finally:
            lock.release()
            rt._ORIGINAL_PHASE2_SYNC_NOW_FOR_POLL = original

    def test_phase2_auto_sync_skips_queued_durable_job(self):
        self._binding()
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,"
            "payload_json,state,attempts,last_error,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'queued',0,'',?,?)",
            (7001, "chat", "session", "1", "generation", "{}", now, now),
        )
        self.db.commit()
        calls = []
        original = rt._ORIGINAL_PHASE2_SYNC_NOW_FOR_POLL
        rt._ORIGINAL_PHASE2_SYNC_NOW_FOR_POLL = (
            lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        )
        try:
            rt.phase2_sync_poll(self.db)
        finally:
            rt._ORIGINAL_PHASE2_SYNC_NOW_FOR_POLL = original
        self.assertEqual(calls, [])

    def test_phase3_realtime_sync_skips_chat_with_active_job_lock(self):
        self._binding()
        calls = []
        original = rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL
        rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = (
            lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        )
        lock = rt.chat_job_lock("chat")
        lock.acquire()
        try:
            rt.phase3_sync_poll(self.db)
            self.assertEqual(calls, [])
        finally:
            lock.release()
            rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = original

    def test_phase3_bounded_poll_prioritizes_oldest_binding(self):
        for index in range(33):
            session_id = f"s{index:02d}"
            self.db.execute(
                "INSERT INTO sync_bindings("
                "chat_id,session_id,sync_id,realtime_enabled,"
                "realtime_next_retry_at,last_checked_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    f"chat-{index}",
                    session_id,
                    f"stb-{index:032x}",
                    1,
                    0,
                    100.0 if index < 32 else 0.0,
                ),
            )
        self.db.commit()
        seen = []
        original = rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL

        def fake_sync(db, chat_id, session_id):
            seen.append(session_id)
            db.execute(
                "UPDATE sync_bindings SET last_checked_at=? "
                "WHERE chat_id=? AND session_id=?",
                (rt.time.time(), chat_id, session_id),
            )
            db.commit()
            return "unchanged"

        rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = fake_sync
        try:
            rt.phase3_sync_poll(self.db)
        finally:
            rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = original
        self.assertEqual(len(seen), 32)
        self.assertIn("s32", seen)

    def test_phase3_unexpected_failures_disable_after_five_attempts(self):
        self._binding()
        self.db.execute(
            "UPDATE sync_bindings SET realtime_failures=4 "
            "WHERE chat_id='chat' AND session_id='session'"
        )
        self.db.commit()
        original = rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL
        rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = (
            lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        try:
            rt.phase3_sync_poll(self.db)
        finally:
            rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = original
        row = self.db.execute(
            "SELECT realtime_enabled,realtime_failures,last_error "
            "FROM sync_bindings WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()
        self.assertEqual(row[0], 0)
        self.assertEqual(row[1], 5)
        self.assertEqual(row[2], "unexpected Phase 3 binding failure")

    def test_session_delete_cascades_sync_binding_cleanup(self):
        active = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        inactive = rt.create_session(
            self.db, "chat", rt.DEFAULT_MODEL, session_id="inactive"
        )
        rt.set_meta(self.db, "active_session:chat", active["session_id"])
        rt.ensure_sync_binding(self.db, "chat", inactive["session_id"])
        self.db.execute(
            "UPDATE sync_bindings SET auto_enabled=1,realtime_enabled=1 "
            "WHERE chat_id='chat' AND session_id='inactive'"
        )
        self.db.commit()

        deleted, reason = rt.delete_session_data(
            self.db,
            "chat",
            inactive["session_id"],
            active["session_id"],
            operation_id=991,
        )

        self.assertTrue(deleted, reason)
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings "
                "WHERE chat_id='chat' AND session_id='inactive'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
