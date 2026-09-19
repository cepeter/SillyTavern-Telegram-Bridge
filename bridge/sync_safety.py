"""Late-loaded synchronization and lifecycle hardening.

Live API sync runs outside the durable Telegram per-chat queues. Keep it from
mutating a chat while one of those jobs owns its chat lock, rotate bounded
polling fairly, and keep sync/import lifecycle state
consistent with session lifecycle.
"""

_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA = initialize_database_schema
_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = phase3_sync_now
_SYNC_POLL_ATTEMPT_LIMIT = 32


def initialize_database_schema(db: sqlite3.Connection) -> None:
    """Run normal migrations, then remove orphaned sync bindings."""
    _ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA(db)
    db.execute(
        "DELETE FROM sync_bindings "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM sessions "
        "WHERE sessions.chat_id=sync_bindings.chat_id "
        "AND sessions.session_id=sync_bindings.session_id)"
    )
    db.commit()


def _try_sync_chat_lock(db: sqlite3.Connection, chat_id: str):
    """Return the chat lock only when no durable Telegram work is pending."""
    lock = chat_job_lock(str(chat_id))
    if not lock.acquire(blocking=False):
        return None
    try:
        pending = db.execute(
            "SELECT 1 FROM jobs WHERE chat_id=? "
            "AND state IN ('queued','scheduled','running') LIMIT 1",
            (str(chat_id),),
        ).fetchone()
    except Exception:
        lock.release()
        logging.warning(
            "Could not inspect durable jobs before sync for chat %s",
            chat_id,
            exc_info=True,
        )
        return None
    if pending:
        lock.release()
        return None
    return lock


def phase3_sync_poll(db: sqlite3.Connection) -> None:
    """Poll realtime API sync fairly and outside active Telegram chat jobs."""
    now = time.time()
    rows = db.execute(
        "SELECT chat_id,session_id,realtime_failures FROM sync_bindings "
        "WHERE realtime_enabled=1 AND realtime_next_retry_at<=? "
        "ORDER BY last_checked_at ASC,chat_id,session_id",
        (now,),
    ).fetchall()
    attempted = 0
    for chat_id, session_id, failures in rows:
        if attempted >= _SYNC_POLL_ATTEMPT_LIMIT:
            break
        lock = _try_sync_chat_lock(db, str(chat_id))
        if lock is None:
            continue
        attempted += 1
        try:
            try:
                _ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL(db, str(chat_id), str(session_id))
            except (SillyTavernApiError, ValueError) as exc:
                count = int(failures or 0) + 1
                if not getattr(exc, "transient", False) or count >= 5:
                    db.execute(
                        "UPDATE sync_bindings SET realtime_failures=? "
                        "WHERE chat_id=? AND session_id=?",
                        (count, chat_id, session_id),
                    )
                    db.commit()
                    _phase3_disable(db, str(chat_id), str(session_id), str(exc))
                    continue
                delay = min(60.0, PHASE3_SYNC_INTERVAL_SECONDS * (2 ** min(count, 5)))
                db.execute(
                    "UPDATE sync_bindings SET realtime_failures=?,realtime_next_retry_at=?,last_error=? "
                    "WHERE chat_id=? AND session_id=?",
                    (count, time.time() + delay, str(exc)[:1000], chat_id, session_id),
                )
                db.commit()
            except Exception:
                logging.warning("Phase 3 binding failed for session %s", session_id, exc_info=True)
                count = int(failures or 0) + 1
                if count >= 5:
                    db.execute(
                        "UPDATE sync_bindings SET realtime_failures=? "
                        "WHERE chat_id=? AND session_id=?",
                        (count, chat_id, session_id),
                    )
                    db.commit()
                    _phase3_disable(
                        db,
                        str(chat_id),
                        str(session_id),
                        "unexpected Phase 3 binding failure",
                    )
                    continue
                delay = min(60.0, PHASE3_SYNC_INTERVAL_SECONDS * (2 ** min(count, 5)))
                db.execute(
                    "UPDATE sync_bindings SET realtime_failures=?,realtime_next_retry_at=?,last_error=? "
                    "WHERE chat_id=? AND session_id=?",
                    (
                        count,
                        time.time() + delay,
                        "unexpected Phase 3 binding failure",
                        chat_id,
                        session_id,
                    ),
                )
                db.commit()
        finally:
            lock.release()
