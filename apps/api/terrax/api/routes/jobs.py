"""Job inspection and cancellation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...jobs import queue
from ...models import JobEvent, ProcessingJob
from ...schemas import JobEventRead, JobRead
from ..deps import get_db

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
def list_jobs(
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    analysis_id: uuid.UUID | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(ProcessingJob)
    count = select(func.count()).select_from(ProcessingJob)
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
        count = count.where(ProcessingJob.status == status)
    if job_type:
        stmt = stmt.where(ProcessingJob.job_type == job_type)
        count = count.where(ProcessingJob.job_type == job_type)
    if analysis_id:
        stmt = stmt.where(ProcessingJob.analysis_id == analysis_id)
        count = count.where(ProcessingJob.analysis_id == analysis_id)
    total = int(db.scalar(count) or 0)
    rows = db.scalars(
        stmt.order_by(ProcessingJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(ProcessingJob, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    return row


@router.get("/jobs/{job_id}/events", response_model=list[JobEventRead])
def get_job_events(
    job_id: uuid.UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    if db.get(ProcessingJob, job_id) is None:
        raise HTTPException(404, "job not found")
    return db.scalars(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.id > after_id)
        .order_by(JobEvent.id)
        .limit(limit)
    ).all()


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status not in ("queued", "running"):
        raise HTTPException(409, f"job is already {job.status}")
    queue.cancel(db, job)
    db.refresh(job)
    return job
