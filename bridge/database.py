import hashlib
import json
import logging
import re
import sqlite3
import time

import bridge.limits as _limits
import bridge.sqlite_store as _sqlite_store
from bridge import config as _config
from bridge.settings import AppSettings


def get_meta(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    def write():
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        db.commit()

    _sqlite_store.run_write_txn(db, write)


def _retryable_model_turn_text(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    parts = stripped.split(None, 1)
    first = parts[0].casefold()
    if stripped.casefold() == "start" or first.startswith("/"):
        return False
    if first.startswith("@") and len(parts) > 1:
        return not parts[1].lstrip().startswith("/")
    return True


def record_failed_turn(
    db: sqlite3.Connection,
    chat_id: str,
    telegram_message_id: int,
    text: str,
    model: str,
    error: str,
    session_id: str = "",
) -> None:
    if not _retryable_model_turn_text(text):
        return

    def write():
        resolved_model = str(model or "")
        if session_id:
            row = db.execute(
                "SELECT model_id FROM sessions WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
            if row and str(row[0] or "").strip():
                resolved_model = str(row[0])
        now = time.time()
        db.execute(
            (
                "INSERT INTO failed_turns(chat_id,telegram_message_id,text,model,session_"
                "id,attempts,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(chat_id,telegram_message_id) DO UPDATE SET "
                "model=excluded.model,session_id=excluded.session_id,attempts=attempts+1,"
                "last_error=excluded.last_error,updated_at=excluded.updated_at"
            ),
            (
                chat_id,
                str(telegram_message_id),
                text[:12000],
                resolved_model[:200],
                session_id[:200],
                1,
                error[:1000],
                now,
                now,
            ),
        )
        db.commit()

    _sqlite_store.run_write_txn(db, write)


def latest_failed_turn(db: sqlite3.Connection, chat_id: str):
    rows = db.execute(
        "SELECT telegram_message_id,text,model,attempts,last_error,session_id "
        "FROM failed_turns WHERE chat_id=? ORDER BY updated_at DESC",
        (chat_id,),
    ).fetchall()
    return next(
        (row for row in rows if _retryable_model_turn_text(row[1])),
        None,
    )


def clear_failed_turn(db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str) -> None:
    def write():
        db.execute(
            "DELETE FROM failed_turns WHERE chat_id=? AND telegram_message_id=?", (chat_id, str(telegram_message_id))
        )
        db.commit()

    _sqlite_store.run_write_txn(db, write)


def committed_assistant_for_message(db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str):
    return db.execute(
        """SELECT assistant.rowid, assistant.content, assistant.telegram_message_ids
        FROM messages AS user_message
        JOIN messages AS assistant
          ON assistant.chat_id=user_message.chat_id
         AND assistant.session_id=user_message.session_id
         AND assistant.role='assistant'
         AND assistant.rowid > user_message.rowid
        WHERE user_message.chat_id=? AND user_message.role='user' AND user_message.telegram_message_id=?
        ORDER BY assistant.rowid LIMIT 1""",
        (chat_id, str(telegram_message_id)),
    ).fetchone()


def bind_panel_session(
    db: sqlite3.Connection, chat_id: str, message_id: int | str, session_id: str, owner_user_id: str = ""
) -> None:
    db.execute(
        (
            "INSERT OR REPLACE INTO panel_sessions(chat_id,message_id,session_id,owne"
            "r_user_id,expires_at) VALUES(?,?,?,?,?)"
        ),
        (str(chat_id), str(message_id), str(session_id), str(owner_user_id or ""), time.time() + 900),
    )
    db.commit()


def panel_session_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str | None:
    row = db.execute(
        "SELECT session_id FROM panel_sessions WHERE chat_id=? AND message_id=? AND expires_at>=?",
        (str(chat_id), str(message_id), time.time()),
    ).fetchone()
    return str(row[0]) if row else None


def panel_owner_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str:
    row = db.execute(
        "SELECT owner_user_id FROM panel_sessions WHERE chat_id=? AND message_id=? AND expires_at>=?",
        (str(chat_id), str(message_id), time.time()),
    ).fetchone()
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
    db.execute(
        "UPDATE operations SET state=?, kind=?, updated_at=? WHERE operation_id=?",
        (phase, kind, now, str(operation_id)),
    )


def begin_operation(db: sqlite3.Connection, operation_id: int | str | None, kind: str) -> bool:
    """Persist the prepared marker immediately without spanning external I/O."""
    if operation_id is None:
        return True

    def write():
        now = time.time()
        cursor = db.execute(
            (
                "INSERT OR IGNORE INTO operations(operation_id,kind,state,created_at,upda"
                "ted_at) VALUES(?,?, 'in_progress',?,?)"
            ),
            (str(operation_id), kind, now, now),
        )
        if cursor.rowcount == 1:
            db.commit()
            return True
        return False

    inserted = _sqlite_store.run_write_txn(db, write)
    if inserted:
        return True
    return not operation_was_applied(db, operation_id)


def operation_was_applied(db: sqlite3.Connection, operation_id: int | str | None) -> bool:
    if operation_id is None:
        return False
    return (
        db.execute("SELECT 1 FROM operations WHERE operation_id=? AND state='applied'", (str(operation_id),)).fetchone()
        is not None
    )


def record_operation(db: sqlite3.Connection, operation_id: int | str | None, kind: str) -> None:
    if operation_id is None:
        return
    now = time.time()
    db.execute(
        "UPDATE operations SET state='applied',kind=?,updated_at=? WHERE operation_id=?", (kind, now, str(operation_id))
    )


def enqueue_job(
    db: sqlite3.Connection,
    update_id: int,
    chat_id: str,
    session_id: str,
    telegram_message_id: int,
    kind: str,
    payload: dict,
) -> int:
    def write():
        now = time.time()
        db.execute(
            (
                "INSERT OR IGNORE INTO jobs(update_id,chat_id,session_id,telegram_message"
                "_id,kind,payload_json,state,attempts,last_error,created_at,updated_at)\n  "
                "          VALUES(?,?,?,?,?,?, 'queued',0,'',?,?)"
            ),
            (
                update_id,
                chat_id,
                session_id,
                str(telegram_message_id),
                kind,
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = db.execute("SELECT job_id FROM jobs WHERE update_id=?", (update_id,)).fetchone()
        if row is None:
            raise RuntimeError("job handoff failed")
        db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)", (update_id, now))
        db.commit()
        return int(row[0])

    return _sqlite_store.run_write_txn(db, write)


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
        cursor = db.execute(
            "UPDATE jobs SET state='scheduled', updated_at=? WHERE job_id=? AND state='queued'", (time.time(), job_id)
        )
        db.commit()
        return cursor.rowcount == 1

    return _sqlite_store.run_write_txn(db, write)


def mark_job_running(db: sqlite3.Connection, job_id: int) -> bool:
    def write():
        cursor = db.execute(
            (
                "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? WHERE "
                "job_id=? AND state IN ('queued','scheduled')"
            ),
            (time.time(), job_id),
        )
        db.commit()
        return cursor.rowcount == 1

    return _sqlite_store.run_write_txn(db, write)


def finish_job(db: sqlite3.Connection, job_id: int, state: str, error: str = "") -> bool:
    try:

        def write():
            db.execute(
                "UPDATE jobs SET state=?, last_error=?, updated_at=? WHERE job_id=?",
                (state, error[:1000], time.time(), job_id),
            )
            db.commit()
            return True

        return _sqlite_store.run_write_txn(db, write)
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
            "UPDATE jobs SET state='queued', updated_at=? WHERE state IN ('running','scheduled')",
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
    db: sqlite3.Connection, chat_id: str, session: dict[str, str], task: str = "utility", *, app_settings: AppSettings
) -> str:
    """Resolve a per-task model with utility -> main-model fallback."""
    session_id = str(session["session_id"])
    task_name = str(task or "utility").casefold()
    model = get_meta(db, task_model_key(chat_id, session_id, task_name), "").strip()
    if not model and task_name != "utility":
        model = get_meta(db, task_model_key(chat_id, session_id, "utility"), "").strip()
    return model or str(session.get("model_id") or app_settings.default_model)


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
    set_meta(
        db,
        model_target_selection_key(chat_id, session_id),
        json.dumps({"target": target, "expires_at": time.time() + _limits.PENDING_SETTINGS_TTL_SECONDS}),
    )


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
        return dict(_config.GENERATION_DEFAULTS)
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
            strict=False,
        )
    )


def update_generation_settings(
    db: sqlite3.Connection, chat_id: str, session_id: str, **values: object
) -> dict[str, object]:
    allowed = set(_config.GENERATION_DEFAULTS)
    values = {key: value for key, value in values.items() if key in allowed}
    with _sqlite_store.write_transaction(db):
        db.execute(
            "INSERT OR IGNORE INTO generation_settings("
            "chat_id,session_id,temperature,max_tokens,top_p,"
            "frequency_penalty,presence_penalty,reasoning_budget,stop_sequences"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                _config.GENERATION_DEFAULTS["temperature"],
                _config.GENERATION_DEFAULTS["max_tokens"],
                _config.GENERATION_DEFAULTS["top_p"],
                _config.GENERATION_DEFAULTS["frequency_penalty"],
                _config.GENERATION_DEFAULTS["presence_penalty"],
                _config.GENERATION_DEFAULTS["reasoning_budget"],
                _config.GENERATION_DEFAULTS["stop_sequences"],
            ),
        )
        if values:
            assignments = ", ".join(f"{key}=?" for key in values)
            db.execute(
                f"UPDATE generation_settings SET {assignments} WHERE chat_id=? AND session_id=?",  # noqa: S608 -- SQL structure uses fixed columns/placeholders; all values are bound
                (*values.values(), chat_id, session_id),
            )
    return get_generation_settings(db, chat_id, session_id)


def preset_names(db: sqlite3.Connection, chat_id: str) -> list[str]:
    rows = db.execute(
        "SELECT preset_name FROM generation_presets WHERE chat_id=? ORDER BY preset_name", (chat_id,)
    ).fetchall()
    return [str(row[0]) for row in rows]


def save_generation_preset(db: sqlite3.Connection, chat_id: str, name: str, settings: dict[str, object]) -> None:
    db.execute(
        "INSERT OR REPLACE INTO generation_presets(chat_id,preset_name,settings_json,created_at) VALUES(?,?,?,?)",
        (chat_id, name, json.dumps(settings, ensure_ascii=False), time.time()),
    )
    db.commit()


def load_generation_preset(db: sqlite3.Connection, chat_id: str, name: str) -> dict[str, object] | None:
    row = db.execute(
        "SELECT settings_json FROM generation_presets WHERE chat_id=? AND preset_name=?", (chat_id, name)
    ).fetchone()
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
    reasoning_level = next(
        (name for name, value in _config.REASONING_LEVELS.items() if value == reasoning_budget), "custom"
    )
    return (
        f"temperature={settings['temperature']}\nmax_tokens={settings['max_tokens']}\n"
        f"top_p={settings['top_p']}\nfrequency_penalty={settings['frequency_penalty']}\n"
        f"presence_penalty={settings['presence_penalty']}\nreasoning={reasoning_level} ({reasoning_budget})\n"
        f"stop={stop}"
    )


def parse_generation_setting(key: str, raw_value: str) -> tuple[str, object]:
    aliases = {
        "temp": "temperature",
        "max": "max_tokens",
        "top-p": "top_p",
        "frequency": "frequency_penalty",
        "presence": "presence_penalty",
        "reasoning": "reasoning_budget",
        "stop": "stop_sequences",
    }
    key = aliases.get(key.casefold(), key.casefold())
    if key not in _config.GENERATION_DEFAULTS:
        raise ValueError("unknown setting")
    if key == "stop_sequences":
        if raw_value.casefold() in {"off", "none", "clear"}:
            return key, ""
        values = [item.strip() for item in raw_value.replace("\\n", "\n").split(",") if item.strip()]
        if len(values) > 4 or any(len(item) > 100 for item in values):
            raise ValueError("stop supports up to 4 sequences of 100 characters")
        return key, "\n".join(values)
    if key == "reasoning_budget" and raw_value.casefold() in _config.REASONING_LEVELS:
        return key, _config.REASONING_LEVELS[raw_value.casefold()]
    try:
        if key in {"max_tokens", "reasoning_budget"}:
            value = int(raw_value)
            limits = {"max_tokens": (1, 16000), "reasoning_budget": (0, 32000)}
        else:
            value = float(raw_value)
            limits = {
                "temperature": (0.0, 2.0),
                "top_p": (0.0, 1.0),
                "frequency_penalty": (-2.0, 2.0),
                "presence_penalty": (-2.0, 2.0),
            }
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
    payload = json.dumps(
        [[str(role), str(content)] for role, content in rows], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_sync_binding(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    """Return or create the stable external-sync identity for a session."""
    row = db.execute(
        "SELECT sync_id,last_hash,last_direction,last_synced_at FROM sync_bindings WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        sync_id = "stb-" + hashlib.sha256(f"{chat_id}:{session_id}:{time.time_ns()}".encode("utf-8")).hexdigest()[:32]
        try:
            db.execute(
                "INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES(?,?,?)", (chat_id, session_id, sync_id)
            )
        except sqlite3.IntegrityError:
            sync_id = (
                "stb-" + hashlib.sha256(f"{chat_id}:{session_id}:{time.time_ns()}".encode("utf-8")).hexdigest()[:32]
            )
            db.execute(
                "INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES(?,?,?)", (chat_id, session_id, sync_id)
            )
        db.commit()
        return {"sync_id": sync_id, "last_hash": "", "last_direction": "", "last_synced_at": 0.0}
    return {
        "sync_id": str(row[0]),
        "last_hash": str(row[1] or ""),
        "last_direction": str(row[2] or ""),
        "last_synced_at": float(row[3] or 0),
    }
