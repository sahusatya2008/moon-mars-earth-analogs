"""Report creation, listing, and controlled downloads."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...jobs import queue
from ...models import AnalysisExecution, Report
from ...reports.generator import EXTENSIONS, create_report
from ...schemas import ReportCreate, ReportRead
from ..deps import audit, get_db

router = APIRouter(tags=["reports"])
settings = get_settings()


@router.post("/reports", response_model=ReportRead, status_code=201)
def request_report(payload: ReportCreate, db: Session = Depends(get_db)):
    analysis = db.get(AnalysisExecution, payload.analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    if analysis.status != "succeeded":
        raise HTTPException(409, "analysis must be complete before requesting a report")
    report = create_report(
        db, analysis_id=payload.analysis_id, fmt=payload.format, title=payload.title
    )
    job = queue.enqueue(
        db,
        job_type="report",
        payload={"report_id": str(report.id), "format": report.format},
        analysis_id=analysis.id,
        priority=5,
    )
    audit(
        db,
        action="report.request",
        resource_type="report",
        resource_id=str(report.id),
        details={"format": report.format, "job_id": str(job.id)},
    )
    db.commit()
    db.refresh(report)
    return report


@router.get("/reports", response_model=list[ReportRead])
def list_reports(
    analysis_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stmt = select(Report).order_by(Report.created_at.desc())
    if analysis_id:
        stmt = stmt.where(Report.analysis_id == analysis_id)
    return db.scalars(stmt).all()


@router.get("/reports/{report_id}", response_model=ReportRead)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    return report


@router.get("/reports/{report_id}/download")
def download_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    if report.status != "ready" or not report.file_path:
        raise HTTPException(409, f"report is {report.status}")
    path = Path(report.file_path).resolve()
    root = settings.report_dir.resolve()
    if root not in path.parents or not path.exists():
        raise HTTPException(404, "report file is unavailable")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if report.format == "geojson":
        media_type = "application/geo+json"
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"terrax-{str(report.analysis_id)[:8]}.{EXTENSIONS.get(report.format, 'dat')}",
        headers={
            "X-Checksum-SHA256": report.checksum_sha256 or "",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    if report.file_path:
        path = Path(report.file_path).resolve()
        root = settings.report_dir.resolve()
        if root in path.parents:
            path.unlink(missing_ok=True)
    db.delete(report)
    audit(db, action="report.delete", resource_type="report", resource_id=str(report_id))
    db.commit()
    return None
