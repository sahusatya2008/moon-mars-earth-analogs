"""PostgreSQL-backed job queue using FOR UPDATE SKIP LOCKED.

The queue is intentionally small: claim, heartbeat-like progress events, cooperative cancel,
retry with backoff, and crash recovery. Redis is optional and not required.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AnalysisExecution, JobEvent, ProcessingJob

settings = get_settings()

ACTIVE_STATUSES = ("queued", "running")
FINAL_STATUSES = ("succeeded", "failed", "cancelled")


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    analysis_id: uuid.UUID | None = None,
    priority: int = 0,
    max_attempts: int | None = None,
) -> ProcessingJob:
    job = ProcessingJob(
        job_type=job_type,
        payload=payload or {},
        analysis_id=analysis_id,
        priority=priority,
        max_attempts=max_attempts or settings.terrax_job_max_attempts,
        status="queued",
        stage="queued",
    )
    db.add(job)
    db.flush()
    db.add(
        JobEvent(
            job_id=job.id,
            level="info",
            stage="queued",
            progress=0.0,
            message=f"{job_type} job queued",
            data={"payload": payload or {}},
        )
    )
    db.commit()
    return job


def claim(db: Session, worker_id: str) -> ProcessingJob | None:
    job = db.scalars(
        select(ProcessingJob)
        .where(
            ProcessingJob.status == "queued",
            ProcessingJob.run_after <= now(),
        )
        .order_by(ProcessingJob.priority.desc(), ProcessingJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.status = "running"
    job.stage = "claimed"
    job.locked_at = now()
    job.locked_by = worker_id
    job.started_at = job.started_at or now()
    job.attempts += 1
    job.cancel_requested = False
    db.add(
        JobEvent(
            job_id=job.id,
            level="info",
            stage="claimed",
            progress=job.progress,
            message=f"Claimed by {worker_id} (attempt {job.attempts}/{job.max_attempts})",
            data={},
        )
    )
    if job.analysis_id and job.job_type == "analysis":
        analysis = db.get(AnalysisExecution, job.analysis_id)
        if analysis is not None:
            analysis.status = "running"
            analysis.stage = "claimed"
    db.commit()
    return job


def event(
    db: Session,
    job_id: uuid.UUID,
    *,
    level: str = "info",
    stage: str,
    progress: float,
    message: str,
    data: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    db.add(
        JobEvent(
            job_id=job_id,
            level=level,
            stage=stage,
            progress=max(0.0, min(1.0, float(progress))),
            message=message[:8000],
            data=data or {},
        )
    )
    if commit:
        db.commit()


def succeed(db: Session, job: ProcessingJob, result: dict[str, Any] | None = None) -> None:
    job.status = "succeeded"
    job.stage = "complete"
    job.progress = 1.0
    job.result = result or {}
    job.finished_at = now()
    job.locked_at = None
    db.add(
        JobEvent(
            job_id=job.id,
            level="info",
            stage="complete",
            progress=1.0,
            message="Job succeeded",
            data=result or {},
        )
    )
    db.commit()


def retry_or_fail(db: Session, job: ProcessingJob, exc: Exception, *, retryable: bool) -> str:
    message = f"{type(exc).__name__}: {exc}"[:8000]
    analysis = (
        db.get(AnalysisExecution, job.analysis_id)
        if job.analysis_id and job.job_type == "analysis"
        else None
    )
    if retryable and job.attempts < job.max_attempts:
        delay_s = min(300, 5 * (2 ** max(0, job.attempts - 1)))
        job.status = "queued"
        job.stage = "retrying"
        job.error_message = message
        job.run_after = now() + timedelta(seconds=delay_s)
        job.locked_at = None
        job.locked_by = None
        if analysis is not None:
            analysis.status = "queued"
            analysis.stage = "retrying"
            analysis.error_message = message
        db.add(
            JobEvent(
                job_id=job.id,
                level="warning",
                stage="retrying",
                progress=job.progress,
                message=f"Retryable failure; attempt {job.attempts}/{job.max_attempts} in {delay_s}s: {message}",
                data={"retry_in_seconds": delay_s},
            )
        )
        db.commit()
        return "retrying"

    job.status = "failed"
    job.stage = "failed"
    job.error_message = message
    job.finished_at = now()
    job.locked_at = None
    if analysis is not None:
        analysis.status = "failed"
        analysis.stage = "failed"
        analysis.error_message = message
        analysis.completed_at = now()
    db.add(
        JobEvent(
            job_id=job.id,
            level="error",
            stage="failed",
            progress=job.progress,
            message=message,
            data={"attempts": job.attempts},
        )
    )
    db.commit()
    return "failed"


def cancel(db: Session, job: ProcessingJob, reason: str = "Cancelled by user request.") -> None:
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.stage = "cancelled"
        job.finished_at = now()
    if job.analysis_id and job.job_type == "analysis":
        analysis = db.get(AnalysisExecution, job.analysis_id)
        if analysis is not None and analysis.status not in FINAL_STATUSES:
            analysis.status = "cancelled" if job.status == "cancelled" else "cancelling"
            analysis.stage = "cancelled" if job.status == "cancelled" else "cancelling"
            analysis.error_message = reason
            if job.status == "cancelled":
                analysis.completed_at = now()
    db.add(
        JobEvent(
            job_id=job.id,
            level="warning",
            stage="cancelled",
            progress=job.progress,
            message=reason,
            data={},
        )
    )
    db.commit()


def stats(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    oldest = db.scalar(
        select(func.min(ProcessingJob.created_at)).where(ProcessingJob.status == "queued")
    )
    running = db.scalar(
        select(func.count()).where(ProcessingJob.status == "running")
    )
    return {
        "counts": counts,
        "queued": counts.get("queued", 0),
        "running": int(running or 0),
        "oldest_queued_at": oldest.isoformat() if oldest else None,
    }


def recover_stale(db: Session, *, older_than_minutes: int = 45) -> int:
    """Return abandoned running jobs to the queue after a worker crash."""
    cutoff = now() - timedelta(minutes=older_than_minutes)
    stale = db.scalars(
        select(ProcessingJob).where(
            ProcessingJob.status == "running",
            ProcessingJob.locked_at.is_not(None),
            ProcessingJob.locked_at < cutoff,
        )
    ).all()
    for job in stale:
        job.status = "queued"
        job.stage = "recovered"
        job.run_after = now()
        job.locked_at = None
        job.locked_by = None
        if job.analysis_id and job.job_type == "analysis":
            analysis = db.get(AnalysisExecution, job.analysis_id)
            if analysis is not None and analysis.status not in FINAL_STATUSES:
                analysis.status = "queued"
                analysis.stage = "recovered"
        db.add(
            JobEvent(
                job_id=job.id,
                level="warning",
                stage="recovered",
                progress=job.progress,
                message="Worker lock expired; job returned to the queue.",
                data={},
            )
        )
    if stale:
        db.commit()
    return len(stale)


def worker_id() -> str:
    import os
    import socket

    raw = f"{socket.gethostname()}:{os.getpid()}".encode()
    return f"worker-{hashlib.sha256(raw).hexdigest()[:10]}"


__all__ = [
    "enqueue",
    "claim",
    "event",
    "succeed",
    "retry_or_fail",
    "cancel",
    "stats",
    "recover_stale",
    "worker_id",
    "ACTIVE_STATUSES",
    "FINAL_STATUSES",
]
