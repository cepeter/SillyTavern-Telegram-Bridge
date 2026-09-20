"""Explicit database/scheduler safety collaborators for durable jobs."""

from __future__ import annotations

from collections.abc import Callable
import functools
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import TypeVar


T = TypeVar("T")


class DatabaseConnectionGate:
    """Initialize each database path once, then use lightweight handles."""

    def __init__(
        self,
        initialize: Callable[[Path], T],
        open_lightweight: Callable[[Path], T],
    ) -> None:
        self._initialize = initialize
        self._open_lightweight = open_lightweight
        self._ready_paths: set[Path] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(database_path: Path) -> Path:
        return Path(database_path).expanduser().resolve()

    def connect(self, database_path: Path) -> T:
        path = self._normalize(database_path)
        if path in self._ready_paths:
            return self._open_lightweight(path)

        with self._lock:
            if path in self._ready_paths:
                return self._open_lightweight(path)
            connection = self._initialize(path)
            self._ready_paths.add(path)
            return connection


class DurableWorkerGuard:
    """Requeue not-yet-running durable work after transient SQLite failure."""

    def __init__(
        self,
        open_requeue_connection,
        *,
        sleep=time.sleep,
        delays=(0.0, 0.25, 1.0),
    ) -> None:
        self._open_requeue_connection = open_requeue_connection
        self._sleep = sleep
        self._delays = tuple(delays)

    @staticmethod
    def _transient(exc: BaseException) -> bool:
        if not isinstance(exc, sqlite3.OperationalError):
            return False
        text = str(exc).casefold()
        return "locked" in text or "busy" in text

    @staticmethod
    def _database_path(db) -> Path | None:
        try:
            row = db.execute("PRAGMA database_list").fetchone()
        except sqlite3.Error:
            return None
        if not row or len(row) < 3 or not row[2]:
            return None
        return Path(str(row[2])).expanduser().resolve()

    def _requeue(
        self,
        job_id: int,
        exc: BaseException,
        database_path: Path | None,
    ) -> None:
        if not self._transient(exc) or database_path is None:
            return

        last_error = f"worker database startup failed: {exc}"[:1000]
        for delay in self._delays:
            if delay:
                self._sleep(delay)
            connection = None
            try:
                connection = self._open_requeue_connection(
                    database_path,
                    timeout=10.0,
                )
                connection.execute(
                    "UPDATE jobs SET state='queued', last_error=?, updated_at=? "
                    "WHERE job_id=? AND state IN ('queued','scheduled')",
                    (last_error, time.time(), int(job_id)),
                )
                connection.commit()
                logging.warning(
                    "Requeued durable job %s after transient DB startup failure",
                    job_id,
                )
                return
            except sqlite3.OperationalError:
                logging.warning(
                    "Could not yet requeue durable job %s",
                    job_id,
                    exc_info=True,
                )
            finally:
                if connection is not None:
                    connection.close()

        logging.error(
            "Durable job %s remains recoverable on restart after DB startup failure",
            job_id,
        )

    def prepare(self, db, job_id: int, worker):
        database_path = self._database_path(db)

        @functools.wraps(worker)
        def guarded_worker(*worker_args):
            try:
                return worker(*worker_args)
            except BaseException as exc:
                self._requeue(
                    int(job_id),
                    exc,
                    database_path,
                )
                raise

        return guarded_worker
