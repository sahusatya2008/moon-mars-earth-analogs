"""Job type dispatch and retry classification."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..connectors.http import SourceUnavailable
from ..engine.pipeline import JobCancelled, run_analysis
from ..models import ProcessingJob
from . import queue

log = logging.getLogger("terrax.jobs")


def _retryable(exc: Exception) -> bool:
    import httpx
    import rasterio

    return isinstance(
        exc,
        (
            SourceUnavailable,
            httpx.HTTPError,
            rasterio.errors.RasterioIOError,
            ConnectionError,
            TimeoutError,
            OSError,
        ),
    )


def handle_analysis(db: Session, job: ProcessingJob) -> dict[str, Any]:
    if job.analysis_id is None:
        raise ValueError("analysis job is missing analysis_id")
    queue.event(
        db,
        job.id,
        stage="starting",
        progress=0.0,
        message="Analysis worker started",
        data={"analysis_id": str(job.analysis_id)},
    )
    return run_analysis(job.analysis_id, job.id, db)


def handle_report(db: Session, job: ProcessingJob) -> dict[str, Any]:
    from ..reports.generator import generate_report

    report_id = (job.payload or {}).get("report_id")
    if not report_id:
        raise ValueError("report job is missing payload.report_id")
    return generate_report(db, report_id)


HANDLERS = {
    "analysis": handle_analysis,
    "report": handle_report,
}


def execute(db: Session, job: ProcessingJob) -> None:
    handler = HANDLERS.get(job.job_type)
    if handler is None:
        queue.retry_or_fail(db, job, ValueError(f"unknown job type {job.job_type!r}"), retryable=False)
        return
    try:
        result = handler(db, job)
        current = db.get(ProcessingJob, job.id)
        if current is None:
            return
        if current.status in queue.FINAL_STATUSES:
            # The pipeline/report handler already persisted a terminal state.
            return
        queue.succeed(db, current, result if isinstance(result, dict) else {"result": result})
    except JobCancelled:
        current = db.get(ProcessingJob, job.id)
        if current is not None and current.status != "cancelled":
            queue.cancel(db, current, "Cancelled cooperatively by user request.")
    except Exception as exc:
        db.rollback()
        current = db.get(ProcessingJob, job.id)
        if current is None:
            log.exception("job %s disappeared during execution", job.id)
            return
        outcome = queue.retry_or_fail(db, current, exc, retryable=_retryable(exc))
        log.warning("job %s %s: %s", job.id, outcome, exc)


__all__ = ["execute", "HANDLERS"]
