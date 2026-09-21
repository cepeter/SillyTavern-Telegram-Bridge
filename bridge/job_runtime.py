"""Canonical compatibility construction for durable job services."""
from __future__ import annotations

from bridge import database as _database
from bridge.job_service import JobService
from bridge.scheduler_safety import DurableWorkerGuard


DURABLE_WORKER_GUARD = DurableWorkerGuard(
    _database._lightweight_db_connect
)


def compatibility_job_service(background) -> JobService:
    """Build a JobService over the supplied background runtime."""
    return JobService(
        enqueue_backend=_database.enqueue_job,
        actor_backend=_database.job_actor_id,
        schedule_backend=_database.mark_job_scheduled,
        start_backend=_database.mark_job_running,
        finish_backend=_database.finish_job,
        recover_backend=_database.recover_jobs,
        submit_chat=background.submit_chat,
        prepare_worker=DURABLE_WORKER_GUARD.prepare,
    )


def jobs_for_services(services) -> JobService:
    """Return the injected job service or the compatibility adapter."""
    if services.jobs is not None:
        return services.jobs
    return compatibility_job_service(services.background)
