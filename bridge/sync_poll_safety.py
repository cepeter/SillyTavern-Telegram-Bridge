"""Explicit safety policy for realtime Live Sync polling."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3


_UNEXPECTED_SYNC_ERROR = (
    "unexpected Live Sync polling failure"
)


@dataclass(frozen=True)
class SyncPollSafetyAdapter:
    sync_now: Callable[
        [sqlite3.Connection, str, str],
        str,
    ]
    chat_lock: Callable[[str], object]
    disable_realtime: Callable[
        [sqlite3.Connection, str, str, str],
        None,
    ]
    expected_errors: tuple[type[Exception], ...]
    sync_interval: Callable[[], float]
    now: Callable[[], float]
    log_warning: Callable[..., None]
    attempt_limit: int = 32

    def _try_chat_lock(
        self,
        db: sqlite3.Connection,
        chat_id: str,
    ):
        lock = self.chat_lock(str(chat_id))
        if not lock.acquire(blocking=False):
            return None
        try:
            pending = db.execute(
                "SELECT 1 FROM jobs WHERE chat_id=? "
                "AND state IN ('queued','scheduled','running') "
                "LIMIT 1",
                (str(chat_id),),
            ).fetchone()
        except Exception:
            lock.release()
            self.log_warning(
                "Could not inspect durable jobs before sync for chat %s",
                chat_id,
                exc_info=True,
            )
            return None
        if pending:
            lock.release()
            return None
        return lock

    def _delay(self, count: int) -> float:
        return min(
            60.0,
            self.sync_interval()
            * (2 ** min(count, 5)),
        )

    @staticmethod
    def _persist_failure_count(
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        count: int,
    ) -> None:
        db.execute(
            "UPDATE sync_bindings SET realtime_failures=? "
            "WHERE chat_id=? AND session_id=?",
            (count, chat_id, session_id),
        )
        db.commit()

    def _persist_retry(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        count: int,
        error: str,
    ) -> None:
        db.execute(
            "UPDATE sync_bindings SET "
            "realtime_failures=?,"
            "realtime_next_retry_at=?,"
            "last_error=? "
            "WHERE chat_id=? AND session_id=?",
            (
                count,
                self.now() + self._delay(count),
                error[:1000],
                chat_id,
                session_id,
            ),
        )
        db.commit()

    def poll(
        self,
        db: sqlite3.Connection,
    ) -> None:
        rows = db.execute(
            "SELECT chat_id,session_id,realtime_failures "
            "FROM sync_bindings "
            "WHERE realtime_enabled=1 "
            "AND realtime_next_retry_at<=? "
            "ORDER BY last_checked_at ASC,chat_id,session_id",
            (self.now(),),
        ).fetchall()

        attempted = 0
        for chat_id, session_id, failures in rows:
            if attempted >= self.attempt_limit:
                break

            lock = self._try_chat_lock(
                db,
                str(chat_id),
            )
            if lock is None:
                continue

            attempted += 1
            try:
                try:
                    self.sync_now(
                        db,
                        str(chat_id),
                        str(session_id),
                    )
                except self.expected_errors as exc:
                    count = int(failures or 0) + 1
                    if (
                        not getattr(
                            exc,
                            "transient",
                            False,
                        )
                        or count >= 5
                    ):
                        self._persist_failure_count(
                            db,
                            str(chat_id),
                            str(session_id),
                            count,
                        )
                        self.disable_realtime(
                            db,
                            str(chat_id),
                            str(session_id),
                            str(exc),
                        )
                        continue
                    self._persist_retry(
                        db,
                        str(chat_id),
                        str(session_id),
                        count,
                        str(exc),
                    )
                except Exception:
                    self.log_warning(
                        "Live Sync polling failed for session %s",
                        session_id,
                        exc_info=True,
                    )
                    count = int(failures or 0) + 1
                    if count >= 5:
                        self._persist_failure_count(
                            db,
                            str(chat_id),
                            str(session_id),
                            count,
                        )
                        self.disable_realtime(
                            db,
                            str(chat_id),
                            str(session_id),
                            _UNEXPECTED_SYNC_ERROR,
                        )
                        continue
                    self._persist_retry(
                        db,
                        str(chat_id),
                        str(session_id),
                        count,
                        _UNEXPECTED_SYNC_ERROR,
                    )
            finally:
                lock.release()
