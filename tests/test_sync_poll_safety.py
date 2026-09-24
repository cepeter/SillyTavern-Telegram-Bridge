from pathlib import Path
import sqlite3
import threading
import unittest

from bridge.sync_poll_safety import SyncPollSafetyAdapter


class ExpectedSyncError(RuntimeError):
    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


class SyncPollSafetyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            """CREATE TABLE sync_bindings (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                realtime_enabled INTEGER NOT NULL DEFAULT 0,
                realtime_failures INTEGER NOT NULL DEFAULT 0,
                realtime_next_retry_at REAL NOT NULL DEFAULT 0,
                last_checked_at REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(chat_id, session_id)
            )"""
        )
        self.db.execute(
            """CREATE TABLE jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                state TEXT NOT NULL
            )"""
        )
        self.db.commit()

        self.now_value = 1000.0
        self.interval = 2.0
        self.calls = []
        self.disabled = []
        self.warnings = []
        self.locks = {}

        def lock_for(chat_id):
            return self.locks.setdefault(
                str(chat_id),
                threading.Lock(),
            )

        self.lock_for = lock_for
        self.sync_now = lambda _db, chat_id, session_id: self.calls.append(
            (chat_id, session_id)
        )

        def disable(db, chat_id, session_id, error):
            persisted = db.execute(
                "SELECT realtime_failures,last_error "
                "FROM sync_bindings "
                "WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
            self.disabled.append(
                (chat_id, session_id, error, persisted)
            )
            db.execute(
                "UPDATE sync_bindings "
                "SET realtime_enabled=0,realtime_next_retry_at=0,"
                "last_error=? "
                "WHERE chat_id=? AND session_id=?",
                (str(error)[:1000], chat_id, session_id),
            )
            db.commit()

        self.disable = disable

        def warning(message, *args, **kwargs):
            self.warnings.append(
                (message, args, kwargs)
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=self.sync_now,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=warning,
        )

    def tearDown(self):
        self.db.close()

    def add_binding(
        self,
        chat_id,
        session_id,
        *,
        failures=0,
        retry_at=0.0,
        checked_at=0.0,
        enabled=1,
    ):
        self.db.execute(
            "INSERT INTO sync_bindings("
            "chat_id,session_id,realtime_enabled,"
            "realtime_failures,realtime_next_retry_at,"
            "last_checked_at,last_error"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                enabled,
                failures,
                retry_at,
                checked_at,
                "",
            ),
        )
        self.db.commit()

    def test_oldest_binding_is_attempted_first(self):
        self.add_binding(
            "newer",
            "s-new",
            checked_at=20.0,
        )
        self.add_binding(
            "oldest",
            "s-old",
            checked_at=1.0,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.calls,
            [
                ("oldest", "s-old"),
                ("newer", "s-new"),
            ],
        )

    def test_future_retry_candidate_is_not_attempted(self):
        self.add_binding(
            "future",
            "s1",
            retry_at=self.now_value + 1,
        )

        self.adapter.poll(self.db)

        self.assertEqual(self.calls, [])

    def test_locked_candidate_does_not_consume_attempt_budget(self):
        for index in range(3):
            self.add_binding(
                f"locked-{index}",
                f"s{index}",
                checked_at=float(index),
            )
            self.lock_for(
                f"locked-{index}"
            ).acquire()
        self.add_binding(
            "eligible",
            "s3",
            checked_at=3.0,
        )
        self.adapter = SyncPollSafetyAdapter(
            sync_now=self.sync_now,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
            attempt_limit=1,
        )

        try:
            self.adapter.poll(self.db)
        finally:
            for index in range(3):
                self.lock_for(
                    f"locked-{index}"
                ).release()

        self.assertEqual(
            self.calls,
            [("eligible", "s3")],
        )

    def test_scans_past_32_locked_candidates(self):
        for index in range(32):
            chat_id = f"a{index:02d}"
            self.add_binding(
                chat_id,
                f"s{index:02d}",
                checked_at=0.0,
            )
            self.lock_for(chat_id).acquire()
        self.add_binding(
            "z-eligible",
            "s32",
            checked_at=0.0,
        )

        try:
            self.adapter.poll(self.db)
        finally:
            for index in range(32):
                self.lock_for(
                    f"a{index:02d}"
                ).release()

        self.assertEqual(
            self.calls,
            [("z-eligible", "s32")],
        )

    def test_attempt_limit_counts_only_eligible_acquired_candidates(self):
        for index in range(4):
            self.add_binding(
                f"chat-{index}",
                f"s{index}",
                checked_at=float(index),
            )
        self.adapter = SyncPollSafetyAdapter(
            sync_now=self.sync_now,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
            attempt_limit=2,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.calls,
            [
                ("chat-0", "s0"),
                ("chat-1", "s1"),
            ],
        )

    def test_each_durable_job_state_blocks_sync_and_releases_lock(self):
        for state in (
            "queued",
            "scheduled",
            "running",
        ):
            with self.subTest(state=state):
                chat_id = f"chat-{state}"
                session_id = f"s-{state}"
                self.add_binding(chat_id, session_id)
                self.db.execute(
                    "INSERT INTO jobs(chat_id,state) "
                    "VALUES(?,?)",
                    (chat_id, state),
                )
                self.db.commit()

        self.adapter.poll(self.db)

        self.assertEqual(self.calls, [])
        for state in (
            "queued",
            "scheduled",
            "running",
        ):
            lock = self.lock_for(
                f"chat-{state}"
            )
            self.assertTrue(
                lock.acquire(blocking=False)
            )
            lock.release()

    def test_done_job_does_not_block_sync(self):
        self.add_binding("chat", "session")
        self.db.execute(
            "INSERT INTO jobs(chat_id,state) "
            "VALUES('chat','done')"
        )
        self.db.commit()

        self.adapter.poll(self.db)

        self.assertEqual(
            self.calls,
            [("chat", "session")],
        )

    def test_durable_job_query_failure_releases_lock_and_skips(self):
        class BrokenDb:
            def execute(inner_self, sql, params=()):
                if "FROM sync_bindings" in sql:
                    return self.db.execute(
                        sql,
                        params,
                    )
                raise sqlite3.OperationalError(
                    "database is locked"
                )

        self.add_binding("chat", "session")
        broken = BrokenDb()

        self.adapter.poll(broken)

        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.warnings), 1)
        self.assertEqual(
            self.warnings[0][0],
            "Could not inspect durable jobs before sync for chat %s",
        )
        self.assertEqual(
            self.warnings[0][1],
            ("chat",),
        )
        self.assertTrue(
            self.warnings[0][2]["exc_info"]
        )
        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()

    def test_success_releases_chat_lock(self):
        self.add_binding("chat", "session")

        self.adapter.poll(self.db)

        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()

    def test_expected_non_transient_error_disables_immediately(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise ExpectedSyncError(
                "bad request",
                transient=False,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled,
            [
                (
                    "chat",
                    "session",
                    "bad request",
                    (1, ""),
                )
            ],
        )

    def test_expected_transient_error_uses_exponential_backoff(self):
        self.add_binding(
            "chat",
            "session",
            failures=2,
        )

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        row = self.db.execute(
            "SELECT realtime_enabled,realtime_failures,"
            "realtime_next_retry_at,last_error "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()
        self.assertEqual(
            row,
            (
                1,
                3,
                self.now_value + 16.0,
                "temporary",
            ),
        )
        self.assertEqual(self.disabled, [])

    def test_expected_backoff_is_capped_at_sixty_seconds(self):
        self.interval = 30.0
        self.add_binding(
            "chat",
            "session",
            failures=3,
        )

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        retry_at = self.db.execute(
            "SELECT realtime_next_retry_at "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()[0]
        self.assertEqual(
            retry_at,
            self.now_value + 60.0,
        )

    def test_expected_fifth_failure_is_persisted_before_disable(self):
        self.add_binding(
            "chat",
            "session",
            failures=4,
        )

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled,
            [
                (
                    "chat",
                    "session",
                    "temporary",
                    (5, ""),
                )
            ],
        )

    def test_retry_error_text_is_truncated_to_1000_characters(self):
        self.add_binding("chat", "session")
        message = "x" * 1200

        def fail(*_args):
            raise ExpectedSyncError(
                message,
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        stored = self.db.execute(
            "SELECT last_error "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()[0]
        self.assertEqual(
            stored,
            "x" * 1000,
        )

    def test_terminal_expected_error_passes_full_string_to_disable(self):
        self.add_binding("chat", "session")
        message = "x" * 1200

        def fail(*_args):
            raise ExpectedSyncError(
                message,
                transient=False,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled[0][2],
            message,
        )

    def test_unexpected_error_uses_hardened_exponential_backoff(self):
        self.add_binding(
            "chat",
            "session",
            failures=1,
        )

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        row = self.db.execute(
            "SELECT realtime_failures,"
            "realtime_next_retry_at,last_error "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()
        self.assertEqual(
            row,
            (
                2,
                self.now_value + 8.0,
                "unexpected Live Sync polling failure",
            ),
        )

    def test_unexpected_failure_logs_warning(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda message, *args, **kwargs:
                self.warnings.append(
                    (message, args, kwargs)
                ),
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.warnings[0][0],
            "Live Sync polling failed for session %s",
        )
        self.assertEqual(
            self.warnings[0][1],
            ("session",),
        )
        self.assertTrue(
            self.warnings[0][2]["exc_info"]
        )

    def test_unexpected_fifth_failure_is_persisted_before_disable(self):
        self.add_binding(
            "chat",
            "session",
            failures=4,
        )

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled,
            [
                (
                    "chat",
                    "session",
                    "unexpected Live Sync polling failure",
                    (5, ""),
                )
            ],
        )

    def test_expected_transient_error_releases_chat_lock(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()

    def test_error_path_releases_chat_lock(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()


class SyncPollSafetySourceBoundaryTests(unittest.TestCase):
    def test_sync_poll_safety_has_no_runtime_sync_or_ui_imports(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_poll_safety.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "bridge.runtime",
            "bridge.sync_api",
            "bridge.sync_safety",
            "bridge.telegram",
            "bridge.recovery",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    source,
                )

    def test_hardened_query_has_no_sql_limit_32(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_poll_safety.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("LIMIT 32", source)
        self.assertIn(
            "ORDER BY last_checked_at ASC,chat_id,session_id",
            source,
        )


if __name__ == "__main__":
    unittest.main()
