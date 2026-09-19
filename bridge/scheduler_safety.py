"""Late-loaded database/scheduler hardening for durable jobs."""

import functools

_ORIGINAL_DB_CONNECT = db_connect
_DB_SCHEMA_READY = False
_DB_SCHEMA_READY_PATHS: set[Path] = set()
_DB_SCHEMA_LOCK = threading.Lock()


def _database_path(database_path: Path | None = None) -> Path:
    path = Path(database_path) if database_path is not None else DB_FILE
    return path.expanduser().resolve()


def _lightweight_db_connect(
    database_path: Path | None = None,
    timeout: float = 30.0,
):
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=timeout,
        factory=_SerializedSQLiteConnection,
    )
    # Connection-local pragmas only. Worker startup must never negotiate
    # journal mode, auto_vacuum, or extensions per connection: WAL, schema,
    # and optional extension setup happen once in db_connect(), and repeating
    # database-wide negotiation here reintroduces the writer contention this
    # module exists to prevent.
    _apply_connection_pragmas(connection, timeout=timeout)
    return connection


def db_connect(database_path: Path | None = None):
    """Initialize each database path once, then use lightweight handles."""
    global _DB_SCHEMA_READY
    path = _database_path(database_path)
    default_path = _database_path(None)

    def ready() -> bool:
        if path == default_path:
            return bool(_DB_SCHEMA_READY)
        return path in _DB_SCHEMA_READY_PATHS

    if not ready():
        with _DB_SCHEMA_LOCK:
            if not ready():
                connection = _ORIGINAL_DB_CONNECT(path)
                if path == default_path:
                    _DB_SCHEMA_READY = True
                else:
                    _DB_SCHEMA_READY_PATHS.add(path)
                return connection
    return _lightweight_db_connect(path)


def recover_jobs(db, recover_running: bool = True):
    """Recover in bounded batches so startup cannot materialize the full backlog."""
    if recover_running:
        db.execute(
            "UPDATE jobs SET state='queued', updated_at=? WHERE state IN ('running','scheduled')",
            (time.time(),),
        )
    rows = db.execute(
        "SELECT job_id,chat_id,session_id,telegram_message_id,kind,payload_json "
        "FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 128"
    ).fetchall()
    db.commit()
    return rows


def _transient_worker_boot_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).casefold()
    return "locked" in text or "busy" in text


def _connection_database_path(db) -> Path | None:
    try:
        row = db.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    if not row or len(row) < 3 or not row[2]:
        return None
    return Path(str(row[2])).expanduser().resolve()


def _requeue_worker_boot_failure(
    job_id: int | None,
    exc: BaseException,
    database_path: Path | None = None,
) -> None:
    if job_id is None or not _transient_worker_boot_error(exc):
        return
    last_error = f"worker database startup failed: {exc}"[:1000]
    delays = (0.0, 0.25, 1.0)
    for delay in delays:
        if delay:
            time.sleep(delay)
        connection = None
        try:
            connection = _lightweight_db_connect(
                database_path,
                timeout=10.0,
            )
            connection.execute(
                "UPDATE jobs SET state='queued', last_error=?, updated_at=? "
                "WHERE job_id=? AND state IN ('queued','scheduled')",
                (last_error, time.time(), int(job_id)),
            )
            connection.commit()
            logging.warning("Requeued durable job %s after transient DB startup failure", job_id)
            return
        except sqlite3.OperationalError:
            logging.warning("Could not yet requeue durable job %s", job_id, exc_info=True)
        finally:
            if connection is not None:
                connection.close()
    logging.error("Durable job %s remains recoverable on restart after DB startup failure", job_id)


def submit_durable_chat_job(
    db,
    background,
    label,
    chat_id,
    job_id,
    function,
    *args,
):
    """Guard the worker boot window while using injected background dispatch."""
    database_path = _connection_database_path(db)

    @functools.wraps(function)
    def guarded_worker(*worker_args):
        try:
            return function(*worker_args)
        except BaseException as exc:
            _requeue_worker_boot_failure(
                int(job_id),
                exc,
                database_path,
            )
            raise

    queued = background.submit_chat(
        label,
        chat_id,
        guarded_worker,
        *args,
        int(job_id),
    )
    if queued:
        mark_job_scheduled(db, int(job_id))
    return queued
