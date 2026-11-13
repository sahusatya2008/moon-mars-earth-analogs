"""Database-backed job queue semantics."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from terrax.db import SessionLocal
from terrax.jobs import queue
from terrax.models import AnalysisExecution, JobEvent, PlanetaryRegion, ProcessingJob

pytestmark = pytest.mark.integration


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def region(db):
    return db.scalar(select(PlanetaryRegion).where(PlanetaryRegion.body_id == "moon").limit(1))


@pytest.fixture
def made(db, region):
    created: list[uuid.UUID] = []

    def _make(*, status: str = "queued", title: str = "queue test") -> AnalysisExecution:
        analysis = AnalysisExecution(
            title=title,
            reference_region_id=region.id,
            status=status,
            stage=status,
            progress=0.0,
            config={},
        )
        db.add(analysis)
        db.commit()
        created.append(analysis.id)
        return analysis

    yield _make

    for analysis_id in created:
        jobs = db.scalars(
            select(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id)
        ).all()
        for job in jobs:
            db.execute(delete(JobEvent).where(JobEvent.job_id == job.id))
        db.execute(delete(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id))
        db.execute(delete(AnalysisExecution).where(AnalysisExecution.id == analysis_id))
    db.commit()


def _claim_specific(db, job_id):
    job = db.get(ProcessingJob, job_id)
    job.status = "running"
    job.attempts += 1
    job.stage = "claimed"
    db.commit()
    return job


def test_report_job_never_mutates_analysis_status(db, made):
    analysis = made(status="succeeded")
    job = queue.enqueue(db, job_type="report", analysis_id=analysis.id, payload={})
    db.refresh(analysis)
    assert analysis.status == "succeeded"

    claimed = _claim_specific(db, job.id)
    db.refresh(analysis)
    assert analysis.status == "succeeded"
    assert claimed.status == "running"

    outcome = queue.retry_or_fail(db, claimed, ValueError("render failed"), retryable=False)
    db.refresh(analysis)
    assert outcome == "failed"
    assert analysis.status == "succeeded"
    assert claimed.status == "failed"


def test_analysis_job_failure_marks_analysis(db, made):
    analysis = made()
    job = queue.enqueue(db, job_type="analysis", analysis_id=analysis.id, payload={})
    claimed = queue.claim(db, "test-worker")
    assert claimed is not None
    assert claimed.id == job.id
    db.refresh(analysis)
    assert analysis.status == "running"

    outcome = queue.retry_or_fail(db, claimed, ConnectionError("upstream reset"), retryable=True)
    db.refresh(analysis)
    db.refresh(claimed)
    assert outcome == "retrying"
    assert analysis.status == "queued"
    assert claimed.status == "queued"
    assert claimed.attempts == 1

    # Exhaust the remaining attempts; the final failure is terminal and visible on the analysis.
    claimed.status = "running"
    claimed.attempts = claimed.max_attempts
    db.commit()
    outcome = queue.retry_or_fail(db, claimed, ConnectionError("still down"), retryable=True)
    db.refresh(analysis)
    assert outcome == "failed"
    assert analysis.status == "failed"
    assert "ConnectionError" in (analysis.error_message or "")


def test_cancelled_report_does_not_cancel_analysis(db, made):
    analysis = made(status="succeeded")
    job = queue.enqueue(db, job_type="report", analysis_id=analysis.id, payload={})
    queue.cancel(db, job, "user cancelled export")
    db.refresh(analysis)
    assert job.status == "cancelled"
    assert analysis.status == "succeeded"
