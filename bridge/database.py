from contextlib import contextmanager as _contextmanager

from bridge.scheduler_safety import (
    DatabaseConnectionGate as _DatabaseConnectionGate,
)

_DB_WRITE_LOCK = globals().get("_DB_WRITE_LOCK") or threading.RLock()
_WRITE_SQL_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")
_DB_PRIMARY_CACHE_KIB = 64000
_DB_WORKER_CACHE_KIB = 16000
_DB_PRIMARY_MMAP_BYTES = 268435456
_DB_WORKER_MMAP_BYTES = 67108864


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
    cache_kib: int = _DB_PRIMARY_CACHE_KIB,
    mmap_bytes: int = _DB_PRIMARY_MMAP_BYTES,
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


def run_database_maintenance(vacuum_freelist_threshold: int = 500, timeout: float = 5.0) -> bool:
    """Reclaim disk space on a dedicated autocommit connection.

    VACUUM is a database-wide writer operation that can starve durable job
    transitions, so it only ever runs here: on its own connection, outside all
    request transactions, guarded by a short busy timeout so maintenance yields
    to live traffic instead of blocking it, and only when freelist slack
    actually justifies the rewrite.
    """
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_FILE, timeout=timeout, isolation_level=None)
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


def _database_path(database_path: Path | None = None) -> Path:
    path = Path(database_path) if database_path is not None else DB_FILE
    return path.expanduser().resolve()


def _open_initialized_database(
    database_path: Path,
) -> sqlite3.Connection:
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=30,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(
        db,
        timeout=30.0,
        cache_kib=_DB_PRIMARY_CACHE_KIB,
        mmap_bytes=_DB_PRIMARY_MMAP_BYTES,
    )
    # Database-level setup happens only on the first successful open for
    # this process/path. auto_vacuum must precede WAL negotiation.
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
    db.execute("PRAGMA journal_mode=WAL")
    _load_optional_vector_extension(db)
    initialize_database_schema(db)
    return db


def _lightweight_db_connect(
    database_path: Path | None = None,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=timeout,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(
        db,
        timeout=timeout,
        cache_kib=_DB_WORKER_CACHE_KIB,
        mmap_bytes=_DB_WORKER_MMAP_BYTES,
    )
    return db


_DB_CONNECTION_GATE = _DatabaseConnectionGate(
    _open_initialized_database,
    _lightweight_db_connect,
)

def db_connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open the canonical connection for a SQLite database path."""
    return _DB_CONNECTION_GATE.connect(
        _database_path(database_path)
    )


def get_meta(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    def write():
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        db.commit()
    run_write_txn(db, write)


def record_failed_turn(db: sqlite3.Connection, chat_id: str, telegram_message_id: int, text: str, model: str, error: str, session_id: str = "") -> None:
    def write():
        now = time.time()
        db.execute("INSERT INTO failed_turns(chat_id,telegram_message_id,text,model,session_id,attempts,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,telegram_message_id) DO UPDATE SET session_id=excluded.session_id,attempts=attempts+1,last_error=excluded.last_error,updated_at=excluded.updated_at", (chat_id, str(telegram_message_id), text[:12000], model[:200], session_id[:200], 1, error[:1000], now, now))
        db.commit()
    run_write_txn(db, write)


def latest_failed_turn(db: sqlite3.Connection, chat_id: str):
    return db.execute("SELECT telegram_message_id,text,model,attempts,last_error,session_id FROM failed_turns WHERE chat_id=? ORDER BY updated_at DESC LIMIT 1", (chat_id,)).fetchone()


def clear_failed_turn(db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str) -> None:
    def write():
        db.execute("DELETE FROM failed_turns WHERE chat_id=? AND telegram_message_id=?", (chat_id, str(telegram_message_id)))
        db.commit()
    run_write_txn(db, write)


def committed_assistant_for_message(db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str):
    return db.execute("""SELECT assistant.rowid, assistant.content, assistant.telegram_message_ids
        FROM messages AS user_message
        JOIN messages AS assistant
          ON assistant.chat_id=user_message.chat_id
         AND assistant.session_id=user_message.session_id
         AND assistant.role='assistant'
         AND assistant.rowid > user_message.rowid
        WHERE user_message.chat_id=? AND user_message.role='user' AND user_message.telegram_message_id=?
        ORDER BY assistant.rowid LIMIT 1""", (chat_id, str(telegram_message_id))).fetchone()


def bind_panel_session(db: sqlite3.Connection, chat_id: str, message_id: int | str, session_id: str, owner_user_id: str = "") -> None:
    db.execute("INSERT OR REPLACE INTO panel_sessions(chat_id,message_id,session_id,owner_user_id,expires_at) VALUES(?,?,?,?,?)", (str(chat_id), str(message_id), str(session_id), str(owner_user_id or ""), time.time() + 900))
    db.commit()


def panel_session_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str | None:
    row = db.execute("SELECT session_id FROM panel_sessions WHERE chat_id=? AND message_id=? AND expires_at>=?", (str(chat_id), str(message_id), time.time())).fetchone()
    return str(row[0]) if row else None


def panel_owner_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str:
    row = db.execute("SELECT owner_user_id FROM panel_sessions WHERE chat_id=? AND message_id=? AND expires_at>=?", (str(chat_id), str(message_id), time.time())).fetchone()
    return str(row[0] or "") if row else ""


def operation_phase(db: sqlite3.Connection, operation_id: int | str | None) -> str:
    if operation_id is None:
        return ""
    row = db.execute("SELECT state FROM operations WHERE operation_id=?", (str(operation_id),)).fetchone()
    return str(row[0]) if row else ""


def set_operation_phase(db: sqlite3.Connection, operation_id: int | str | None, kind: str, phase: str) -> None:
    if operation_id is None:
        return
    now = time.time()
    db.execute("UPDATE operations SET state=?, kind=?, updated_at=? WHERE operation_id=?", (phase, kind, now, str(operation_id)))


def begin_operation(db: sqlite3.Connection, operation_id: int | str | None, kind: str) -> bool:
    if operation_id is None:
        return True
    now = time.time()
    cursor = db.execute("INSERT OR IGNORE INTO operations(operation_id,kind,state,created_at,updated_at) VALUES(?,?, 'in_progress',?,?)", (str(operation_id), kind, now, now))
    db.commit()
    if cursor.rowcount == 1:
        return True
    return not operation_was_applied(db, operation_id)


def operation_was_applied(db: sqlite3.Connection, operation_id: int | str | None) -> bool:
    if operation_id is None:
        return False
    return db.execute("SELECT 1 FROM operations WHERE operation_id=? AND state='applied'", (str(operation_id),)).fetchone() is not None


def record_operation(db: sqlite3.Connection, operation_id: int | str | None, kind: str) -> None:
    if operation_id is None:
        return
    now = time.time()
    db.execute("UPDATE operations SET state='applied',kind=?,updated_at=? WHERE operation_id=?", (kind, now, str(operation_id)))


def enqueue_job(db: sqlite3.Connection, update_id: int, chat_id: str, session_id: str, telegram_message_id: int, kind: str, payload: dict) -> int:
    def write():
        now = time.time()
        db.execute("""INSERT OR IGNORE INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,attempts,last_error,created_at,updated_at)
            VALUES(?,?,?,?,?,?, 'queued',0,'',?,?)""", (update_id, chat_id, session_id, str(telegram_message_id), kind, json.dumps(payload, ensure_ascii=False), now, now))
        row = db.execute("SELECT job_id FROM jobs WHERE update_id=?", (update_id,)).fetchone()
        if row is None:
            raise RuntimeError("job handoff failed")
        db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)", (update_id, now))
        db.commit()
        return int(row[0])
    return run_write_txn(db, write)


def job_actor_id(db: sqlite3.Connection, job_id: int | None) -> str:
    if job_id is None:
        return ""
    row = db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (int(job_id),)).fetchone()
    if not row:
        return ""
    try:
        payload = json.loads(row[0] or "{}")
    except (TypeError, json.JSONDecodeError):
        return ""
    return str(payload.get("actor_id") or "") if isinstance(payload, dict) else ""


def mark_job_scheduled(db: sqlite3.Connection, job_id: int) -> bool:
    def write():
        cursor = db.execute("UPDATE jobs SET state='scheduled', updated_at=? WHERE job_id=? AND state='queued'", (time.time(), job_id))
        db.commit()
        return cursor.rowcount == 1
    return run_write_txn(db, write)


def mark_job_running(db: sqlite3.Connection, job_id: int) -> bool:
    def write():
        cursor = db.execute("UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? WHERE job_id=? AND state IN ('queued','scheduled')", (time.time(), job_id))
        db.commit()
        return cursor.rowcount == 1
    return run_write_txn(db, write)


def finish_job(db: sqlite3.Connection, job_id: int, state: str, error: str = "") -> bool:
    try:
        def write():
            db.execute("UPDATE jobs SET state=?, last_error=?, updated_at=? WHERE job_id=?", (state, error[:1000], time.time(), job_id))
            db.commit()
            return True
        return run_write_txn(db, write)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).casefold() and "busy" not in str(exc).casefold():
            raise
        try:
            db.rollback()
        except sqlite3.Error:
            logging.debug("Could not rollback locked job transition", exc_info=True)
        logging.warning("Could not persist job %s transition to %s: %s", job_id, state, exc)
        return False


def recover_jobs(db: sqlite3.Connection, recover_running: bool = True) -> list[tuple]:
    if recover_running:
        db.execute(
            "UPDATE jobs SET state='queued', updated_at=? "
            "WHERE state IN ('running','scheduled')",
            (time.time(),),
        )
    rows = db.execute(
        "SELECT job_id,chat_id,session_id,telegram_message_id,kind,payload_json "
        "FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 128"
    ).fetchall()
    db.commit()
    return rows



def task_model_key(chat_id: str, session_id: str, task: str = "utility") -> str:
    task_name = re.sub(r"[^a-z0-9_-]+", "-", str(task or "utility").casefold()).strip("-") or "utility"
    return f"task_model:{task_name}:{chat_id}:{session_id}"


def task_model_for_session(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    task: str = "utility",
) -> str:
    """Resolve a per-task model with utility -> main-model fallback."""
    session_id = str(session["session_id"])
    task_name = str(task or "utility").casefold()
    model = get_meta(db, task_model_key(chat_id, session_id, task_name), "").strip()
    if not model and task_name != "utility":
        model = get_meta(db, task_model_key(chat_id, session_id, "utility"), "").strip()
    return model or str(session.get("model_id") or DEFAULT_MODEL)


def set_task_model(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    model: str,
    task: str = "utility",
) -> str:
    value = str(model or "").strip()
    if value.casefold() in {"main", "default", "off", "inherit"}:
        value = ""
    if value and (len(value) > 200 or any(ch.isspace() for ch in value)):
        raise ValueError("model id must be at most 200 characters and contain no whitespace")
    set_meta(db, task_model_key(chat_id, session_id, task), value)
    return value



def model_target_selection_key(chat_id: str, session_id: str) -> str:
    return f"model_target_selection:{chat_id}:{session_id}"


def set_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str, target: str) -> None:
    if target not in {"story", "utility"}:
        raise ValueError("invalid model target")
    set_meta(db, model_target_selection_key(chat_id, session_id), json.dumps({"target": target, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}))


def get_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    raw = get_meta(db, model_target_selection_key(chat_id, session_id), "")
    try:
        state = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    if float(state.get("expires_at", 0)) < time.time():
        set_meta(db, model_target_selection_key(chat_id, session_id), "")
        return ""
    target = str(state.get("target") or "")
    return target if target in {"story", "utility"} else ""


def clear_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    set_meta(db, model_target_selection_key(chat_id, session_id), "")


def get_generation_settings(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    row = db.execute(
        "SELECT temperature,max_tokens,top_p,frequency_penalty,"
        "presence_penalty,reasoning_budget,stop_sequences "
        "FROM generation_settings WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        return dict(GENERATION_DEFAULTS)
    return dict(
        zip(
            (
                "temperature",
                "max_tokens",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "reasoning_budget",
                "stop_sequences",
            ),
            row,
        )
    )


def update_generation_settings(db: sqlite3.Connection, chat_id: str, session_id: str, **values: object) -> dict[str, object]:
    allowed = set(GENERATION_DEFAULTS)
    values = {key: value for key, value in values.items() if key in allowed}
    with write_transaction(db):
        db.execute(
            "INSERT OR IGNORE INTO generation_settings("
            "chat_id,session_id,temperature,max_tokens,top_p,"
            "frequency_penalty,presence_penalty,reasoning_budget,stop_sequences"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                GENERATION_DEFAULTS["temperature"],
                GENERATION_DEFAULTS["max_tokens"],
                GENERATION_DEFAULTS["top_p"],
                GENERATION_DEFAULTS["frequency_penalty"],
                GENERATION_DEFAULTS["presence_penalty"],
                GENERATION_DEFAULTS["reasoning_budget"],
                GENERATION_DEFAULTS["stop_sequences"],
            ),
        )
        if values:
            assignments = ", ".join(f"{key}=?" for key in values)
            db.execute(
                f"UPDATE generation_settings SET {assignments} "  # nosec B608 - assignments are filtered against GENERATION_DEFAULTS
                "WHERE chat_id=? AND session_id=?",
                (*values.values(), chat_id, session_id),
            )
    return get_generation_settings(db, chat_id, session_id)


def preset_names(db: sqlite3.Connection, chat_id: str) -> list[str]:
    rows = db.execute("SELECT preset_name FROM generation_presets WHERE chat_id=? ORDER BY preset_name", (chat_id,)).fetchall()
    return [str(row[0]) for row in rows]


def save_generation_preset(db: sqlite3.Connection, chat_id: str, name: str, settings: dict[str, object]) -> None:
    db.execute("INSERT OR REPLACE INTO generation_presets(chat_id,preset_name,settings_json,created_at) VALUES(?,?,?,?)", (chat_id, name, json.dumps(settings, ensure_ascii=False), time.time()))
    db.commit()


def load_generation_preset(db: sqlite3.Connection, chat_id: str, name: str) -> dict[str, object] | None:
    row = db.execute("SELECT settings_json FROM generation_presets WHERE chat_id=? AND preset_name=?", (chat_id, name)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def delete_generation_preset(db: sqlite3.Connection, chat_id: str, name: str) -> bool:
    cursor = db.execute("DELETE FROM generation_presets WHERE chat_id=? AND preset_name=?", (chat_id, name))
    db.commit()
    return cursor.rowcount > 0



def format_generation_settings(settings: dict[str, object]) -> str:
    stop = str(settings.get("stop_sequences") or "") or "off"
    reasoning_budget = int(settings.get("reasoning_budget") or 0)
    reasoning_level = next((name for name, value in REASONING_LEVELS.items() if value == reasoning_budget), "custom")
    return (f"temperature={settings['temperature']}\nmax_tokens={settings['max_tokens']}\n"
            f"top_p={settings['top_p']}\nfrequency_penalty={settings['frequency_penalty']}\n"
            f"presence_penalty={settings['presence_penalty']}\nreasoning={reasoning_level} ({reasoning_budget})\n"
            f"stop={stop}")


def parse_generation_setting(key: str, raw_value: str) -> tuple[str, object]:
    aliases = {"temp": "temperature", "max": "max_tokens", "top-p": "top_p", "frequency": "frequency_penalty", "presence": "presence_penalty", "reasoning": "reasoning_budget", "stop": "stop_sequences"}
    key = aliases.get(key.casefold(), key.casefold())
    if key not in GENERATION_DEFAULTS:
        raise ValueError("unknown setting")
    if key == "stop_sequences":
        if raw_value.casefold() in {"off", "none", "clear"}:
            return key, ""
        values = [item.strip() for item in raw_value.replace("\\n", "\n").split(",") if item.strip()]
        if len(values) > 4 or any(len(item) > 100 for item in values):
            raise ValueError("stop supports up to 4 sequences of 100 characters")
        return key, "\n".join(values)
    if key == "reasoning_budget" and raw_value.casefold() in REASONING_LEVELS:
        return key, REASONING_LEVELS[raw_value.casefold()]
    try:
        if key in {"max_tokens", "reasoning_budget"}:
            value = int(raw_value)
            limits = {"max_tokens": (1, 16000), "reasoning_budget": (0, 32000)}
        else:
            value = float(raw_value)
            limits = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "frequency_penalty": (-2.0, 2.0), "presence_penalty": (-2.0, 2.0)}
        low, high = limits[key]
        if not low <= value <= high:
            raise ValueError(f"value must be between {low} and {high}")
        return key, value
    except ValueError as exc:
        if "between" in str(exc) or "supports" in str(exc):
            raise
        raise ValueError("value has the wrong format") from exc


def sync_transcript_hash(rows: list[tuple[str, str]]) -> str:
    """Create a stable hash for an ordered user/assistant transcript."""
    payload = json.dumps([[str(role), str(content)] for role, content in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_sync_binding(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    """Return or create the stable external-sync identity for a session."""
    row = db.execute("SELECT sync_id,last_hash,last_direction,last_synced_at FROM sync_bindings WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    if row is None:
        sync_id = "stb-" + hashlib.sha256(f"{chat_id}:{session_id}:{time.time_ns()}".encode("utf-8")).hexdigest()[:32]
        try:
            db.execute("INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES(?,?,?)", (chat_id, session_id, sync_id))
        except sqlite3.IntegrityError:
            sync_id = "stb-" + hashlib.sha256(f"{chat_id}:{session_id}:{time.time_ns()}".encode("utf-8")).hexdigest()[:32]
            db.execute("INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES(?,?,?)", (chat_id, session_id, sync_id))
        db.commit()
        return {"sync_id": sync_id, "last_hash": "", "last_direction": "", "last_synced_at": 0.0}
    return {"sync_id": str(row[0]), "last_hash": str(row[1] or ""), "last_direction": str(row[2] or ""), "last_synced_at": float(row[3] or 0)}
