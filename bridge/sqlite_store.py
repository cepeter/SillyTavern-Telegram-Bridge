"""SQLite connection lifecycle, transaction serialization, and maintenance."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager as _contextmanager
from contextlib import suppress as _suppress
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self, TypeVar, cast, overload

import bridge.limits as _limits
from bridge.scheduler_safety import DatabaseConnectionGate as _DatabaseConnectionGate
from bridge.schema import initialize_database_schema
from bridge.settings import AppSettings

_DB_WRITE_LOCK = threading.RLock()
_Result = TypeVar("_Result")
_Parameters = Any  # SQLite accepts registered adapters and arbitrary DB-API binding containers.
_CursorT = TypeVar("_CursorT", bound=sqlite3.Cursor)


def _statement_may_write(sql: str) -> bool:
    """Conservative classification, not a SQL parser or extension sandbox.

    WITH and PRAGMA are intentionally serialized even when used for reads.
    Leading comments must not disguise a write or transaction-control statement.
    """
    if not isinstance(sql, str):
        return True
    statement = sql.lstrip()
    while statement:
        if statement.startswith("--"):
            newline = statement.find("\n")
            if newline < 0:
                return False
            statement = statement[newline + 1 :].lstrip()
        elif statement.startswith("/*"):
            end = statement.find("*/", 2)
            if end < 0:
                return True
            statement = statement[end + 2 :].lstrip()
        else:
            break
    if not statement:
        return False
    token = re.match(r"[A-Za-z]+", statement)
    return token is None or token[0].upper() not in {"SELECT", "VALUES", "EXPLAIN"}


class _SerializedSQLiteCursor(sqlite3.Cursor):
    """Keep writing statements serialized until their result is consumed/closed."""

    connection: _SerializedSQLiteConnection

    def _run_statement(
        self,
        sql: str,
        operation: Callable[..., _Result],
        *args: Any,
        script: bool = False,
    ) -> _Result:
        db = self.connection
        epoch = db._bridge_write_epoch
        with db._serialized_call(script or _statement_may_write(sql)):
            result = operation(sql, *args)
            if self.description is not None and db._bridge_write_epoch != epoch:
                db._bridge_pending_cursors.add(id(self))
            else:
                db._bridge_pending_cursors.discard(id(self))
            return result

    def execute(self, sql: str, parameters: _Parameters = ()) -> Self:
        return self._run_statement(sql, super().execute, parameters)

    def executemany(self, sql: str, seq_of_parameters: Iterable[_Parameters]) -> Self:
        return self._run_statement(sql, super().executemany, seq_of_parameters)

    def executescript(self, sql_script: str) -> Self:
        return cast(Self, self._run_statement(sql_script, super().executescript, script=True))

    def _read_result(
        self,
        operation: Callable[..., _Result],
        *args: Any,
        all_rows: bool = False,
        many: bool = False,
        **kwargs: Any,
    ) -> _Result:
        db = self.connection
        epoch = db._bridge_write_epoch
        with db._serialized_call():
            try:
                result = operation(*args, **kwargs)
            except StopIteration:
                db._bridge_pending_cursors.discard(id(self))
                raise
            if all_rows or (many and not result):
                db._bridge_pending_cursors.discard(id(self))
            elif db._bridge_write_epoch != epoch:
                # A SELECT may invoke a Python callback that writes through
                # this connection; protect the enclosing cursor as well.
                db._bridge_pending_cursors.add(id(self))
            return result

    def fetchone(self) -> Any:
        # Row factories may legitimately return None. Only StopIteration from
        # SQLite's cursor iterator proves there are no more result rows.
        try:
            return self._read_result(super().__next__)
        except StopIteration:
            return None

    def fetchmany(self, *args: Any, **kwargs: Any) -> list[Any]:
        return self._read_result(super().fetchmany, *args, many=True, **kwargs)

    def fetchall(self) -> list[Any]:
        return self._read_result(super().fetchall, all_rows=True)

    def __next__(self) -> Any:
        return self._read_result(super().__next__)

    def close(self) -> None:
        db = self.connection
        with db._serialized_call():
            result = super().close()
            db._bridge_pending_cursors.discard(id(self))
            return result

    def __del__(self) -> None:
        # Finalize the underlying SQLite statement BEFORE releasing its gate.
        # Destruction after a connection already closed is harmless.
        with _suppress(AttributeError, sqlite3.Error):
            self.close()


class _SerializedSQLiteConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bridge_write_lock_held = False
        self._bridge_active_calls = 0
        self._bridge_pending_cursors: set[int] = set()
        self._bridge_write_epoch = 0
        self._bridge_closed = False

    def _release_if_idle(self) -> None:
        if not self._bridge_write_lock_held or self._bridge_active_calls:
            return
        if not self._bridge_closed and (self.in_transaction or self._bridge_pending_cursors):
            return
        self._bridge_write_lock_held = False
        _DB_WRITE_LOCK.release()

    @_contextmanager
    def _serialized_call(self, may_write: bool = False) -> Iterator[None]:
        if self._bridge_closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        if may_write:
            if not self._bridge_write_lock_held:
                _DB_WRITE_LOCK.acquire()
                self._bridge_write_lock_held = True
            self._bridge_write_epoch += 1
        # This counts nested Python calls, not RLock acquisitions. One
        # connection transaction owns exactly one latch acquisition.
        self._bridge_active_calls += 1
        try:
            yield
        finally:
            self._bridge_active_calls -= 1
            self._release_if_idle()

    @overload
    def cursor(self, factory: None = None) -> _SerializedSQLiteCursor: ...

    @overload
    def cursor(self, factory: Callable[[sqlite3.Connection], _CursorT]) -> _CursorT: ...

    def cursor(
        self,
        factory: Callable[[sqlite3.Connection], sqlite3.Cursor] | None = None,
    ) -> sqlite3.Cursor:
        factory = _SerializedSQLiteCursor if factory is None else factory
        if not isinstance(factory, type) or not issubclass(factory, _SerializedSQLiteCursor):
            raise TypeError("serialized connections require a serialized cursor factory")
        return super().cursor(factory)

    def execute(self, sql: str, parameters: _Parameters = ()) -> _SerializedSQLiteCursor:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Iterable[_Parameters]) -> _SerializedSQLiteCursor:
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script: str) -> _SerializedSQLiteCursor:
        return self.cursor().executescript(sql_script)

    def commit(self) -> None:
        with self._serialized_call():
            return super().commit()

    def rollback(self) -> None:
        with self._serialized_call():
            return super().rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        # sqlite3's context manager commits/rolls back in C, bypassing the
        # Python methods above. Keep its original behavior and release after it.
        with self._serialized_call():
            return super().__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        if self._bridge_closed:
            return super().close()
        with self._serialized_call():
            result = super().close()
            self._bridge_closed = True
            self._bridge_pending_cursors.clear()
            return result


@_contextmanager
def write_transaction(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Own one short SQLite write transaction unless the caller already does."""
    if db.in_transaction:
        yield db
        return

    with _DB_WRITE_LOCK:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            # Includes commit-time constraint failures and cancellation. Nested
            # transactions still belong entirely to the caller above.
            db.rollback()
            raise


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
