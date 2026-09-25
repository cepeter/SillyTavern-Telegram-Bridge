"""SQLite connection lifecycle, transaction serialization, and maintenance."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager as _contextmanager
from pathlib import Path

import bridge.limits as _limits
from bridge.scheduler_safety import DatabaseConnectionGate as _DatabaseConnectionGate
from bridge.schema import initialize_database_schema
from bridge.settings import AppSettings

_DB_WRITE_LOCK = threading.RLock()


_WRITE_SQL_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")


class _SerializedSQLiteConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bridge_write_lock_depth = 0

    def _acquire_write_lock(self, sql: str) -> bool:
        statement = str(sql).lstrip().upper()
        if not statement.startswith(_WRITE_SQL_PREFIXES):
            return False
        if self._bridge_write_lock_depth == 0:
            _DB_WRITE_LOCK.acquire()
        self._bridge_write_lock_depth = 1
        return True

    def _release_write_lock(self) -> None:
        while self._bridge_write_lock_depth:
            self._bridge_write_lock_depth -= 1
            _DB_WRITE_LOCK.release()

    def execute(self, sql, parameters=()):
        acquired = self._acquire_write_lock(sql)
        try:
            return super().execute(sql, parameters)
        except Exception:
            if acquired:
                self._release_write_lock()
            raise

    def executemany(self, sql, seq_of_parameters):
        acquired = self._acquire_write_lock(sql)
        try:
            return super().executemany(sql, seq_of_parameters)
        except Exception:
            if acquired:
                self._release_write_lock()
            raise

    def commit(self):
        try:
            return super().commit()
        finally:
            self._release_write_lock()

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._release_write_lock()

    def close(self):
        try:
            return super().close()
        finally:
            self._release_write_lock()


def run_write_txn(db: sqlite3.Connection, operation):
    """Serialize a short SQLite write transaction within this bridge process."""
    del db
    with _DB_WRITE_LOCK:
        return operation()


@_contextmanager
def write_transaction(db: sqlite3.Connection):
    """Own one short SQLite write transaction unless the caller already does."""
    if db.in_transaction:
        yield db
        return

    with _DB_WRITE_LOCK:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()


def _apply_connection_pragmas(
    db: sqlite3.Connection,
    timeout: float = 30.0,
    *,
    cache_kib: int = _limits._DB_PRIMARY_CACHE_KIB,
    mmap_bytes: int = _limits._DB_PRIMARY_MMAP_BYTES,
) -> None:
    """Apply connection-local pragmas for latency, caching, and safety.

    Safe to run on every connection: these settings affect only the current
    handle and never negotiate database-wide state or a write lock. Long-lived
    primary handles use the larger defaults; short-lived worker handles may
    request smaller cache/mmap budgets.
    """
    timeout_ms = int(max(1.0, float(timeout)) * 1000)
    cache_kib = max(1024, int(cache_kib))
    mmap_bytes = max(0, int(mmap_bytes))
    db.execute(f"PRAGMA busy_timeout={timeout_ms}")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute(f"PRAGMA cache_size=-{cache_kib}")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        db.execute(f"PRAGMA mmap_size={mmap_bytes}")
    except sqlite3.OperationalError:
        pass


def _load_optional_vector_extension(db: sqlite3.Connection) -> bool:
    """Optionally load sqlite-vec if installed in the environment."""
    try:
        import sqlite_vec
    except ImportError:
        return False
    if not hasattr(db, "enable_load_extension"):
        return False
    try:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        return True
    except Exception:
        logging.debug("sqlite-vec extension could not be loaded", exc_info=True)
        return False
    finally:
        try:
            db.enable_load_extension(False)
        except Exception:
            logging.debug("Could not disable SQLite extension loading", exc_info=True)


def optimize_database(db: sqlite3.Connection) -> None:
    """Refresh query planner statistics on the caller's connection.

    Deliberately performs no VACUUM and never touches isolation_level:
    reclamation lives in run_database_maintenance() so request paths can never
    trigger a database-wide writer operation.
    """
    try:
        db.execute("PRAGMA optimize")
    except sqlite3.OperationalError:
        pass


def run_database_maintenance(
    vacuum_freelist_threshold: int = 500, timeout: float = 5.0, *, app_settings: AppSettings
) -> bool:
    """Reclaim disk space on a dedicated autocommit connection.

    VACUUM is a database-wide writer operation that can starve durable job
    transitions, so it only ever runs here: on its own connection, outside all
    request transactions, guarded by a short busy timeout so maintenance yields
    to live traffic instead of blocking it, and only when freelist slack
    actually justifies the rewrite.
    """
    path = _database_path(app_settings=app_settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    reclaimed = False
    try:
        _apply_connection_pragmas(db, timeout=timeout)
        try:
            db.execute("PRAGMA optimize")
        except sqlite3.OperationalError:
            pass
        try:
            freelist_row = db.execute("PRAGMA freelist_count").fetchone()
            freelist = int(freelist_row[0]) if freelist_row else 0
            if freelist >= vacuum_freelist_threshold:
                db.execute("VACUUM")
                db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                reclaimed = True
            else:
                db.execute("PRAGMA incremental_vacuum")
        except sqlite3.OperationalError as exc:
            logging.warning("Database maintenance skipped after lock timeout: %s", exc)
    finally:
        db.close()
    return reclaimed


def _database_path(database_path: Path | None = None, *, app_settings: AppSettings) -> Path:
    path = Path(database_path) if database_path is not None else app_settings.db_file
    return path.expanduser().resolve()


def _open_initialized_database(
    database_path: Path,
) -> sqlite3.Connection:
    path = Path(database_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=30,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(
        db,
        timeout=30.0,
        cache_kib=_limits._DB_PRIMARY_CACHE_KIB,
        mmap_bytes=_limits._DB_PRIMARY_MMAP_BYTES,
    )
    # Database-level setup happens only on the first successful open for
    # this process/path. auto_vacuum must precede WAL negotiation.
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
    db.execute("PRAGMA journal_mode=WAL")
    _load_optional_vector_extension(db)
    initialize_database_schema(db)
    return db


def _lightweight_db_connect(
    database_path: Path,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    path = Path(database_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=timeout,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(
        db,
        timeout=timeout,
        cache_kib=_limits._DB_WORKER_CACHE_KIB,
        mmap_bytes=_limits._DB_WORKER_MMAP_BYTES,
    )
    return db


_DB_CONNECTION_GATE = _DatabaseConnectionGate(
    _open_initialized_database,
    _lightweight_db_connect,
)


def db_connect(database_path: Path | None = None, *, app_settings: AppSettings) -> sqlite3.Connection:
    """Open the canonical connection for a SQLite database path."""
    return _DB_CONNECTION_GATE.connect(_database_path(database_path, app_settings=app_settings))
