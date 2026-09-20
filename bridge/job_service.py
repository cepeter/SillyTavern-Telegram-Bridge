from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import sqlite3


@dataclass(frozen=True)
class DurableJob:
    job_id: int
    chat_id: str
    session_id: str
    telegram_message_id: int
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class JobSubmission:
    label: str
    chat_id: str
    worker: Callable[..., None]
    args: tuple[object, ...]


@dataclass(frozen=True)
class JobService:
    enqueue_backend: Callable[..., int]
    actor_backend: Callable[..., str]
    schedule_backend: Callable[..., bool]
    start_backend: Callable[..., bool]
    finish_backend: Callable[..., bool]
    recover_backend: Callable[..., list[tuple]]
    submit_chat: Callable[..., bool]
    prepare_worker: Callable[..., Callable[..., None]] | None = None

    def enqueue(
        self,
        db: sqlite3.Connection,
        update_id: int,
        chat_id: str,
        session_id: str,
        telegram_message_id: int,
        kind: str,
        payload: dict[str, object],
    ) -> int:
        return int(
            self.enqueue_backend(
                db,
                int(update_id),
                str(chat_id),
                str(session_id),
                int(telegram_message_id),
                str(kind),
                payload,
            )
        )

    def submit(
        self,
        db: sqlite3.Connection,
        job_id: int,
        submission: JobSubmission,
    ) -> bool:
        worker = submission.worker
        if self.prepare_worker is not None:
            worker = self.prepare_worker(
                db,
                int(job_id),
                worker,
            )
        accepted = bool(
            self.submit_chat(
                submission.label,
                submission.chat_id,
                worker,
                *submission.args,
                int(job_id),
            )
        )
        if accepted:
            self.schedule_backend(db, int(job_id))
        return accepted

    def start(self, db, job_id: int) -> bool:
        return bool(self.start_backend(db, int(job_id)))

    def complete(self, db, job_id: int) -> bool:
        return bool(
            self.finish_backend(
                db,
                int(job_id),
                "done",
                "",
            )
        )

    def fail(self, db, job_id: int, error) -> bool:
        return bool(
            self.finish_backend(
                db,
                int(job_id),
                "failed",
                str(error),
            )
        )

    def actor_id(self, db, job_id: int | None) -> str:
        return str(self.actor_backend(db, job_id) or "")

    def recover(
        self,
        db,
        resolver,
        *,
        recover_running: bool = True,
    ) -> None:
        rows = self.recover_backend(
            db,
            recover_running=recover_running,
        )
        for (
            job_id,
            chat_id,
            session_id,
            message_id,
            kind,
            payload_json,
        ) in rows:
            try:
                payload = json.loads(payload_json)
                if not isinstance(payload, dict):
                    raise ValueError("job payload must be an object")
                job = DurableJob(
                    job_id=int(job_id),
                    chat_id=str(chat_id),
                    session_id=str(session_id),
                    telegram_message_id=int(message_id or 0),
                    kind=str(kind),
                    payload=payload,
                )
                submission = resolver(job)
                if submission is None:
                    self.fail(
                        db,
                        job.job_id,
                        "unsupported recovered job kind",
                    )
                    continue
                if not self.submit(db, job.job_id, submission):
                    logging.warning(
                        "Could not dispatch recovered %s job %s",
                        job.kind,
                        job.job_id,
                    )
            except Exception as exc:
                self.fail(db, int(job_id), exc)
                logging.error(
                    "Could not recover job %s",
                    job_id,
                    exc_info=True,
                )
