"""Analysis lifecycle: create, inspect, stream progress, results, candidates, comparison."""

from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import ALGORITHM_VERSION, FEATURE_SCHEMA_VERSION
from ...catalog import CATALOG_VERSION
from ...config import get_settings
from ...db import SessionLocal
from ...jobs import queue
from ...models import (
    AnalysisExecution,
    CandidateRanking,
    Comparison,
    EarthCandidate,
    EvidenceRecord,
    FeatureMeasurement,
    JobEvent,
    PlanetaryRegion,
    ProcessingJob,
    Report,
    SimilarityScore,
)
from ...results import (
    analysis_dict,
    analysis_results,
    candidate_detail,
    evidence_dict,
    ranking_dict,
)
from ...schemas import (
    AnalysisCreate,
    AnalysisListResponse,
    AnalysisRead,
    AnalysisResults,
    AnalysisStatus,
    CandidateDetail,
    CandidateListResponse,
    ComparisonCreate,
    ComparisonRead,
    EvidenceListResponse,
    JobRead,
)
from ..deps import audit, get_db

router = APIRouter(tags=["analyses"])
settings = get_settings()


def _latest_job(db: Session, analysis_id: uuid.UUID) -> ProcessingJob | None:
    """Latest analysis job; report jobs for the same analysis must not shadow it."""
    return db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.analysis_id == analysis_id,
            ProcessingJob.job_type == "analysis",
        )
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )


@router.post("/analyses", response_model=AnalysisRead, status_code=201)
def create_analysis(payload: AnalysisCreate, db: Session = Depends(get_db)):
    region = db.get(PlanetaryRegion, payload.reference_region_id)
    if region is None:
        raise HTTPException(404, "reference region not found")
    if region.body_id not in ("moon", "mars"):
        raise HTTPException(422, "reference regions must be on the Moon or Mars")

    if payload.client_token:
        existing = db.scalar(
            select(AnalysisExecution).where(AnalysisExecution.client_token == payload.client_token)
        )
        if existing is not None:
            return existing

    config = payload.config.model_dump(mode="json")
    analysis = AnalysisExecution(
        title=payload.title,
        reference_region_id=region.id,
        status="queued",
        stage="queued",
        progress=0.0,
        client_token=payload.client_token,
        algorithm_version=ALGORITHM_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        config=config,
        dataset_manifest={
            "catalog_version": CATALOG_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "status": "queued",
        },
    )
    db.add(analysis)
    db.flush()
    job = queue.enqueue(
        db,
        job_type="analysis",
        payload={
            "analysis_id": str(analysis.id),
            "reference_region": region.name,
            "body_id": region.body_id,
        },
        analysis_id=analysis.id,
        priority=10,
    )
    audit(
        db,
        action="analysis.create",
        resource_type="analysis",
        resource_id=str(analysis.id),
        details={
            "reference_region_id": str(region.id),
            "body_id": region.body_id,
            "job_id": str(job.id),
            "title": analysis.title,
        },
    )
    db.commit()
    return analysis


@router.get("/analyses", response_model=AnalysisListResponse)
def list_analyses(
    status: str | None = Query(default=None),
    body_id: str | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(AnalysisExecution).join(
        PlanetaryRegion, AnalysisExecution.reference_region_id == PlanetaryRegion.id
    )
    count_stmt = select(func.count()).select_from(AnalysisExecution).join(
        PlanetaryRegion, AnalysisExecution.reference_region_id == PlanetaryRegion.id
    )
    if status:
        stmt = stmt.where(AnalysisExecution.status == status)
        count_stmt = count_stmt.where(AnalysisExecution.status == status)
    if body_id:
        stmt = stmt.where(PlanetaryRegion.body_id == body_id)
        count_stmt = count_stmt.where(PlanetaryRegion.body_id == body_id)
    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(
        stmt.order_by(AnalysisExecution.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [analysis_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/analyses/{analysis_id}", response_model=AnalysisRead)
def get_analysis(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(AnalysisExecution, analysis_id)
    if row is None:
        raise HTTPException(404, "analysis not found")
    return analysis_dict(row)


@router.get("/analyses/{analysis_id}/status", response_model=AnalysisStatus)
def get_status(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(AnalysisExecution, analysis_id)
    if row is None:
        raise HTTPException(404, "analysis not found")
    job = _latest_job(db, analysis_id)
    return {
        "id": row.id,
        "status": row.status,
        "stage": row.stage,
        "progress": row.progress,
        "job_id": job.id if job else None,
        "error_message": row.error_message,
        "updated_at": row.updated_at,
    }


@router.get("/analyses/{analysis_id}/results", response_model=AnalysisResults)
def get_results(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(AnalysisExecution, analysis_id)
    if row is None:
        raise HTTPException(404, "analysis not found")
    if row.status != "succeeded":
        raise HTTPException(
            409,
            detail={
                "message": "analysis is not complete",
                "status": row.status,
                "stage": row.stage,
                "progress": row.progress,
                "error": row.error_message,
            },
        )
    return analysis_results(db, row)


@router.get("/analyses/{analysis_id}/result-summary")
def get_result_summary(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(AnalysisExecution, analysis_id)
    if row is None:
        raise HTTPException(404, "analysis not found")
    return {
        "analysis_id": row.id,
        "status": row.status,
        "result_summary": row.result_summary,
        "error_message": row.error_message,
    }


@router.get("/analyses/{analysis_id}/candidates", response_model=CandidateListResponse)
def list_candidates(
    analysis_id: uuid.UUID,
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    stmt = select(CandidateRanking).where(
        CandidateRanking.analysis_id == analysis_id,
        CandidateRanking.overall_score >= min_score,
        CandidateRanking.confidence >= min_confidence,
    )
    total = int(
        db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )
    rows = db.scalars(
        stmt.order_by(CandidateRanking.rank).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [ranking_dict(db, row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/analyses/{analysis_id}/evidence", response_model=EvidenceListResponse)
def list_analysis_evidence(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    rows = db.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.analysis_id == analysis_id)
        .order_by(EvidenceRecord.candidate_id.nullsfirst(), EvidenceRecord.dataset_id)
    ).all()
    return {"items": [evidence_dict(row) for row in rows], "total": len(rows)}


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def get_candidate(candidate_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        return candidate_detail(db, candidate_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/analyses/{analysis_id}/retry", response_model=AnalysisRead)
def retry_analysis(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    if analysis.status not in ("failed", "cancelled"):
        raise HTTPException(409, "only failed or cancelled analyses can be retried")
    region = db.get(PlanetaryRegion, analysis.reference_region_id)
    if region is None:
        raise HTTPException(409, "reference region is missing")
    analysis.status = "queued"
    analysis.stage = "queued"
    analysis.progress = 0.0
    analysis.error_message = None
    analysis.completed_at = None
    job = queue.enqueue(
        db,
        job_type="analysis",
        payload={
            "analysis_id": str(analysis.id),
            "reference_region": region.name,
            "body_id": region.body_id,
            "retry": True,
        },
        analysis_id=analysis.id,
        priority=10,
    )
    audit(
        db,
        action="analysis.retry",
        resource_type="analysis",
        resource_id=str(analysis.id),
        details={"job_id": str(job.id)},
    )
    db.commit()
    db.refresh(analysis)
    return analysis


@router.post("/analyses/{analysis_id}/cancel", response_model=JobRead)
def cancel_analysis(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    if analysis.status in ("succeeded", "failed", "cancelled"):
        raise HTTPException(409, f"analysis is already {analysis.status}")
    job = _latest_job(db, analysis_id)
    if job is None or job.status not in ("queued", "running"):
        raise HTTPException(409, "no active job to cancel")
    queue.cancel(db, job)
    audit(
        db,
        action="analysis.cancel",
        resource_type="analysis",
        resource_id=str(analysis_id),
        details={"job_id": str(job.id)},
    )
    db.commit()
    db.refresh(job)
    return job


@router.delete("/analyses/{analysis_id}", status_code=204)
def delete_analysis(analysis_id: uuid.UUID, db: Session = Depends(get_db)):
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    job = _latest_job(db, analysis_id)
    if job is not None and job.status in ("queued", "running"):
        raise HTTPException(409, "cancel the active analysis before deleting it")
    reports = db.scalars(select(Report).where(Report.analysis_id == analysis_id)).all()
    for report in reports:
        if report.file_path:
            from pathlib import Path

            try:
                path = Path(report.file_path).resolve()
                if settings.report_dir.resolve() in path.parents:
                    path.unlink(missing_ok=True)
            except Exception:
                pass
    for model in (SimilarityScore, CandidateRanking, FeatureMeasurement, EvidenceRecord, Report):
        db.execute(delete(model).where(model.analysis_id == analysis_id))
    db.execute(delete(EarthCandidate).where(EarthCandidate.analysis_id == analysis_id))
    db.execute(delete(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id))
    db.delete(analysis)
    audit(db, action="analysis.delete", resource_type="analysis", resource_id=str(analysis_id))
    db.commit()
    return None


@router.get("/analyses/{analysis_id}/event-log")
def analysis_event_log(
    analysis_id: uuid.UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Stored event history as JSON; the sibling `/events` route is the SSE stream."""
    if db.get(AnalysisExecution, analysis_id) is None:
        raise HTTPException(404, "analysis not found")
    job = _latest_job(db, analysis_id)
    if job is None:
        return {"items": []}
    rows = db.scalars(
        select(JobEvent)
        .where(JobEvent.job_id == job.id, JobEvent.id > after_id)
        .order_by(JobEvent.id)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "job_id": str(row.job_id),
                "at": row.at,
                "level": row.level,
                "stage": row.stage,
                "progress": row.progress,
                "message": row.message,
                "data": row.data or {},
            }
            for row in rows
        ]
    }


def _sse(event: str, data: dict, event_id: int | str | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str, separators=(',', ':'))}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


@router.get("/analyses/{analysis_id}/events")
def stream_analysis_events(
    analysis_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    job = _latest_job(db, analysis_id)
    if job is None:
        raise HTTPException(404, "analysis has no job")
    try:
        cursor = max(0, int(last_event_id)) if last_event_id else 0
    except ValueError:
        cursor = 0

    def generator():
        last = cursor
        deadline = time.time() + 3600
        while time.time() < deadline:
            with SessionLocal() as session:
                events = session.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job.id, JobEvent.id > last)
                    .order_by(JobEvent.id)
                    .limit(400)
                ).all()
                current_job = session.get(ProcessingJob, job.id)
                current_analysis = session.get(AnalysisExecution, analysis_id)
            for row in events:
                last = row.id
                yield _sse(
                    "job-event",
                    {
                        "id": row.id,
                        "job_id": str(row.job_id),
                        "at": row.at,
                        "level": row.level,
                        "stage": row.stage,
                        "progress": row.progress,
                        "message": row.message,
                        "data": row.data or {},
                    },
                    row.id,
                )
            if current_job is not None and current_job.status in ("succeeded", "failed", "cancelled"):
                # Send one final event; clients can then close or reconnect and read the record.
                yield _sse(
                    "analysis-status",
                    {
                        "analysis_id": str(analysis_id),
                        "job_id": str(job.id),
                        "status": current_analysis.status if current_analysis else "unknown",
                        "stage": current_analysis.stage if current_analysis else "unknown",
                        "progress": current_analysis.progress if current_analysis else 1.0,
                        "error": current_analysis.error_message if current_analysis else None,
                        "terminal": True,
                    },
                )
                break
            # Reconnect-safe keepalive; never fabricate progress.
            yield ": keepalive\n\n"
            time.sleep(1.0)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/comparisons", response_model=ComparisonRead, status_code=201)
def create_comparison(payload: ComparisonCreate, db: Session = Depends(get_db)):
    region = db.get(PlanetaryRegion, payload.reference_region_id)
    if region is None:
        raise HTTPException(404, "reference region not found")
    rankings = db.scalars(
        select(CandidateRanking).where(CandidateRanking.candidate_id.in_(payload.candidate_ids))
    ).all()
    if len({r.candidate_id for r in rankings}) != len(set(payload.candidate_ids)):
        raise HTTPException(422, "every candidate must have a persisted ranking")
    if payload.analysis_id:
        rankings = [r for r in rankings if r.analysis_id == payload.analysis_id]
        if len(rankings) != len(set(payload.candidate_ids)):
            raise HTTPException(422, "candidates do not belong to the requested analysis")
    rows = {str(r.candidate_id): r for r in rankings}
    ordered = [rows[str(cid)] for cid in payload.candidate_ids]
    scores = {
        str(r.candidate_id): {
            "dimension_scores": r.dimension_scores,
            "method_scores": r.method_scores,
            "confidence": r.confidence,
            "evidence_coverage": r.evidence_coverage,
        }
        for r in ordered
    }
    dimensions = ["geomorphology", "morphometry", "texture", "operational"]
    deltas = {}
    for dim in dimensions:
        vals = [
            (str(r.candidate_id), (r.dimension_scores or {}).get(dim, {}).get("score"))
            for r in ordered
        ]
        present = [v for _k, v in vals if v is not None]
        deltas[dim] = {
            "values": {k: v for k, v in vals},
            "spread": round(max(present) - min(present), 6) if len(present) >= 2 else None,
        }
    comparison = Comparison(
        reference_region_id=payload.reference_region_id,
        candidate_ids=[str(cid) for cid in payload.candidate_ids],
        config={"analysis_id": str(payload.analysis_id) if payload.analysis_id else None},
        results={
            "candidates": scores,
            "dimension_deltas": deltas,
            "note": "Candidate-to-candidate comparison of stored scores; no new similarity is invented.",
        },
        status="ready",
    )
    db.add(comparison)
    db.commit()
    return comparison
