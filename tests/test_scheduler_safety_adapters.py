from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import bridge.runtime as rt

from bridge.scheduler_safety import DatabaseConnectionGate, DurableWorkerGuard


class DatabaseConnectionGateTests(unittest.TestCase):
    def test_first_open_initializes_then_same_path_uses_lightweight_open(self):
        calls = []

        def initialize(path):
            calls.append(("initialize", path))
            return ("primary", path)

        def lightweight(path):
            calls.append(("lightweight", path))
            return ("worker", path)

        gate = DatabaseConnectionGate(initialize, lightweight)
        path = Path("relative.sqlite3").resolve()

        self.assertEqual(gate.connect(path), ("primary", path))
        self.assertEqual(gate.connect(path), ("worker", path))
        self.assertEqual(
            calls,
            [("initialize", path), ("lightweight", path)],
        )

    def test_initialization_failure_does_not_mark_path_ready(self):
        attempts = []

        def initialize(path):
            attempts.append(path)
            if len(attempts) == 1:
                raise RuntimeError("schema failed")
            return "primary"

        gate = DatabaseConnectionGate(initialize, lambda _path: "worker")
        path = Path("retry.sqlite3").resolve()

        with self.assertRaisesRegex(RuntimeError, "schema failed"):
            gate.connect(path)

        self.assertEqual(gate.connect(path), "primary")
        self.assertEqual(attempts, [path, path])

    def test_distinct_paths_initialize_independently(self):
        initialized = []
        gate = DatabaseConnectionGate(
            lambda path: initialized.append(path) or path,
            lambda path: path,
        )
        a = Path("a.sqlite3").resolve()
        b = Path("b.sqlite3").resolve()

        gate.connect(a)
        gate.connect(b)

        self.assertEqual(initialized, [a, b])

    def test_concurrent_first_open_initializes_once(self):
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        initialize_count = 0
        results = []

        def initialize(path):
            nonlocal initialize_count
            barrier.wait()
            with lock:
                initialize_count += 1
            return ("primary", path)

        gate = DatabaseConnectionGate(
            initialize,
            lambda path: ("worker", path),
        )
        path = Path("concurrent.sqlite3").resolve()

        def first():
            results.append(gate.connect(path))

        thread = threading.Thread(target=first)
        thread.start()
        barrier.wait()
        results.append(gate.connect(path))
        thread.join()

        self.assertEqual(initialize_count, 1)
        self.assertEqual(
            sorted(item[0] for item in results),
            ["primary", "worker"],
        )


class DurableWorkerGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "jobs.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE jobs ("
            "job_id INTEGER PRIMARY KEY, "
            "state TEXT NOT NULL, "
            "last_error TEXT NOT NULL DEFAULT '', "
            "updated_at REAL NOT NULL DEFAULT 0)"
        )
        self.db.commit()
        self.sleeps = []

        def open_requeue(path, timeout=10.0):
            self.assertEqual(Path(path).resolve(), self.path.resolve())
            return sqlite3.connect(path, timeout=timeout)

        self.guard = DurableWorkerGuard(
            open_requeue,
            sleep=self.sleeps.append,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _raise(self, exc):
        raise exc

    def test_locked_worker_requeues_scheduled_job_and_reraises(self):
        self.db.execute(
            "INSERT INTO jobs(job_id,state) VALUES(1,'scheduled')"
        )
        self.db.commit()
        original = sqlite3.OperationalError("database is locked")
        wrapped = self.guard.prepare(
            self.db,
            1,
            lambda: self._raise(original),
        )

        with self.assertRaises(sqlite3.OperationalError) as caught:
            wrapped()

        self.assertIs(caught.exception, original)
        state, error = self.db.execute(
            "SELECT state,last_error FROM jobs WHERE job_id=1"
        ).fetchone()
        self.assertEqual(state, "queued")
        self.assertIn("worker database startup failed", error)
        self.assertEqual(self.sleeps, [])

    def test_busy_worker_does_not_move_running_job_backwards(self):
        self.db.execute(
            "INSERT INTO jobs(job_id,state) VALUES(2,'running')"
        )
        self.db.commit()
        wrapped = self.guard.prepare(
            self.db,
            2,
            lambda: self._raise(
                sqlite3.OperationalError("database is busy")
            ),
        )

        with self.assertRaises(sqlite3.OperationalError):
            wrapped()

        state = self.db.execute(
            "SELECT state FROM jobs WHERE job_id=2"
        ).fetchone()[0]
        self.assertEqual(state, "running")

    def test_non_transient_operational_error_does_not_open_requeue_connection(self):
        opens = []
        guard = DurableWorkerGuard(
            lambda *args, **kwargs: opens.append((args, kwargs)),
            sleep=lambda _seconds: None,
        )
        wrapped = guard.prepare(
            self.db,
            3,
            lambda: self._raise(
                sqlite3.OperationalError("no such table")
            ),
        )

        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            wrapped()

        self.assertEqual(opens, [])

    def test_non_sqlite_exception_does_not_open_requeue_connection(self):
        opens = []
        guard = DurableWorkerGuard(
            lambda *args, **kwargs: opens.append((args, kwargs)),
            sleep=lambda _seconds: None,
        )
        wrapped = guard.prepare(
            self.db,
            4,
            lambda: self._raise(RuntimeError("boom")),
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            wrapped()

        self.assertEqual(opens, [])

    def test_requeue_retries_are_bounded(self):
        attempts = []

        class FailingConnection:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                attempts.append("closed")

        guard = DurableWorkerGuard(
            lambda _path, timeout=10.0: FailingConnection(),
            sleep=self.sleeps.append,
        )
        wrapped = guard.prepare(
            self.db,
            5,
            lambda: self._raise(
                sqlite3.OperationalError("database is locked")
            ),
        )

        with self.assertRaises(sqlite3.OperationalError):
            wrapped()

        self.assertEqual(self.sleeps, [0.25, 1.0])
        self.assertEqual(attempts, ["closed", "closed", "closed"])


class CanonicalDatabaseConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "canonical.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_canonical_gate_runs_schema_initialization_once(self):
        original = rt.initialize_database_schema
        calls = []

        def traced(db):
            calls.append(db)
            return original(db)

        with patch.object(
            rt,
            "initialize_database_schema",
            side_effect=traced,
        ):
            first = rt._DB_CONNECTION_GATE.connect(self.path)
            second = rt._DB_CONNECTION_GATE.connect(self.path)

        try:
            self.assertEqual(len(calls), 1)
        finally:
            first.close()
            second.close()

    def test_canonical_gate_keeps_role_specific_cache(self):
        first = rt._DB_CONNECTION_GATE.connect(self.path)
        second = rt._DB_CONNECTION_GATE.connect(self.path)
        try:
            first_cache = first.execute("PRAGMA cache_size").fetchone()[0]
            second_cache = second.execute("PRAGMA cache_size").fetchone()[0]
            self.assertEqual(first_cache, -rt._DB_PRIMARY_CACHE_KIB)
            self.assertEqual(second_cache, -rt._DB_WORKER_CACHE_KIB)
            self.assertEqual(
                second.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )
            self.assertEqual(
                second.execute("PRAGMA busy_timeout").fetchone()[0],
                30000,
            )
        finally:
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
