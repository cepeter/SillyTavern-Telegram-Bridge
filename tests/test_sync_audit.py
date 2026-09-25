from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import bridge.config as config
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.schema as _m_schema
import bridge.schema as schema
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
import bridge.telegram as _m_telegram


class SyncAuditHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _binding(self, chat_id="chat", session_id="session"):
        self.db.execute(
            "INSERT OR REPLACE INTO sync_bindings("
            "chat_id,session_id,sync_id,realtime_enabled,"
            "realtime_failures,realtime_next_retry_at,last_checked_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                f"stb-{session_id.encode().hex()[:32]:0<32}",
                1,
                0,
                0,
                0,
            ),
        )
        self.db.commit()

    def _many_bindings(self):
        for index in range(32):
            self.db.execute(
                "INSERT INTO sync_bindings("
                "chat_id,session_id,sync_id,realtime_enabled,"
                "realtime_next_retry_at,last_checked_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    f"a{index:02d}",
                    f"s{index:02d}",
                    f"stb-{index:032x}",
                    1,
                    0,
                    0,
                ),
            )
        self.db.execute(
            "INSERT INTO sync_bindings("
            "chat_id,session_id,sync_id,realtime_enabled,"
            "realtime_next_retry_at,last_checked_at"
            ") VALUES(?,?,?,?,?,?)",
            ("z-eligible", "s32", f"stb-{32:032x}", 1, 0, 0),
        )
        self.db.commit()

    def test_direct_poll_adapter_uses_final_runtime_sync_now(self):
        self._binding()
        calls = []

        original = _m_sync_api.live_sync_now
        _m_sync_api.live_sync_now = lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        try:
            _m_sync_api._SYNC_POLL_SAFETY.poll(
                self.db,
            )
        finally:
            _m_sync_api.live_sync_now = original

        self.assertEqual(
            calls,
            [("chat", "session")],
        )

    def test_live_sync_skips_chat_with_active_job_lock(self):
        self._binding()
        calls = []
        original = _m_sync_api.live_sync_now
        _m_sync_api.live_sync_now = lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        lock = _m_sync_api.chat_job_lock("chat")
        lock.acquire()
        try:
            _m_sync_api.live_sync_poll(self.db)
            self.assertEqual(calls, [])
        finally:
            lock.release()
            _m_sync_api.live_sync_now = original

    def test_live_sync_poll_scans_past_32_locked_candidates(self):
        self._many_bindings()
        calls = []
        original = _m_sync_api.live_sync_now
        _m_sync_api.live_sync_now = lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        locks = [_m_sync_api.chat_job_lock(f"a{index:02d}") for index in range(32)]
        for lock in locks:
            lock.acquire()
        try:
            _m_sync_api.live_sync_poll(self.db)
        finally:
            for lock in locks:
                lock.release()
            _m_sync_api.live_sync_now = original
        self.assertEqual(calls, [("z-eligible", "s32")])

    def test_live_sync_bounded_poll_prioritizes_oldest_binding(self):
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
        original = _m_sync_api.live_sync_now

        def fake_sync(db, chat_id, session_id):
            seen.append(session_id)
            db.execute(
                "UPDATE sync_bindings SET last_checked_at=? WHERE chat_id=? AND session_id=?",
                (time.time(), chat_id, session_id),
            )
            db.commit()
            return "unchanged"

        _m_sync_api.live_sync_now = fake_sync
        try:
            _m_sync_api.live_sync_poll(self.db)
        finally:
            _m_sync_api.live_sync_now = original
        self.assertEqual(len(seen), 32)
        self.assertIn("s32", seen)

    def test_live_sync_unexpected_failures_disable_after_five_attempts(self):
        self._binding()
        self.db.execute("UPDATE sync_bindings SET realtime_failures=4 WHERE chat_id='chat' AND session_id='session'")
        self.db.commit()
        original = _m_sync_api.live_sync_now
        _m_sync_api.live_sync_now = lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            _m_sync_api.live_sync_poll(self.db)
        finally:
            _m_sync_api.live_sync_now = original
        row = self.db.execute(
            "SELECT realtime_enabled,realtime_failures,last_error "
            "FROM sync_bindings WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()
        self.assertEqual(row[0], 0)
        self.assertEqual(row[1], 5)
        self.assertEqual(row[2], "unexpected Live Sync polling failure")

    def test_public_poll_uses_hardened_adapter(self):
        self._many_bindings()
        calls = []
        original = _m_sync_api.live_sync_now

        _m_sync_api.live_sync_now = lambda _db, chat_id, session_id: calls.append((chat_id, session_id))
        locks = [_m_sync_api.chat_job_lock(f"a{index:02d}") for index in range(32)]
        for lock in locks:
            lock.acquire()
        try:
            _m_sync_api.live_sync_poll(self.db)
        finally:
            for lock in locks:
                lock.release()
            _m_sync_api.live_sync_now = original

        self.assertEqual(
            calls,
            [("z-eligible", "s32")],
        )

    def test_session_delete_cascades_sync_binding_cleanup(self):
        active = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        inactive = _m_session_naming.create_session(
            self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="inactive"
        )
        _m_session_naming.set_meta(self.db, "active_session:chat", active["session_id"])
        _m_sync_core.ensure_sync_binding(self.db, "chat", inactive["session_id"])
        self.db.execute("UPDATE sync_bindings SET realtime_enabled=1 WHERE chat_id='chat' AND session_id='inactive'")
        self.db.commit()

        deleted, reason = _m_panel_callback_routes.delete_session_data(
            self.db,
            "chat",
            inactive["session_id"],
            active["session_id"],
            operation_id=991,
            memory_service=SimpleNamespace(purge_session=lambda *_args: 0),
        )

        self.assertTrue(deleted, reason)
        self.assertIsNone(
            self.db.execute("SELECT 1 FROM sync_bindings WHERE chat_id='chat' AND session_id='inactive'").fetchone()
        )

    def test_canonical_startup_cleanup_removes_orphan_sync_binding(self):
        valid = _m_session_naming.create_session(
            self.db,
            "valid-chat",
            _m_memory_curator.DEFAULT_MODEL,
            session_id="valid-session",
        )
        _m_sync_core.ensure_sync_binding(
            self.db,
            "valid-chat",
            valid["session_id"],
        )
        self.db.execute(
            "INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES(?,?,?)",
            (
                "orphan-chat",
                "missing-session",
                "stb-orphan",
            ),
        )
        self.db.commit()

        schema._run_startup_database_cleanup(
            self.db,
        )
        self.db.commit()

        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings WHERE chat_id='orphan-chat' AND session_id='missing-session'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings WHERE chat_id='valid-chat' AND session_id='valid-session'"
            ).fetchone()
        )

    def test_sync_startup_cleanup_removes_orphan_without_structural_ddl(self):
        self.db.execute(
            "INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES('orphan-chat','missing-session','stb-orphan')"
        )
        self.db.commit()

        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            _m_schema.initialize_database_schema(self.db)
        finally:
            self.db.set_trace_callback(None)

        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings WHERE chat_id='orphan-chat' AND session_id='missing-session'"
            ).fetchone()
        )
        sync_structural = [
            sql
            for sql in traced
            if sql.lstrip()
            .upper()
            .startswith(
                (
                    "CREATE ",
                    "ALTER ",
                    "DROP ",
                )
            )
            and "sync_" in sql.casefold()
        ]
        self.assertEqual(
            sync_structural,
            [],
            sync_structural,
        )

    def test_phase6e_does_not_add_schema_migration(self):
        self.assertEqual(
            tuple(migration.version for migration in _m_schema.SCHEMA_MIGRATIONS),
            (1,),
        )


if __name__ == "__main__":
    unittest.main()
