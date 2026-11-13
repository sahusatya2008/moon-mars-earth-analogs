"""Health and readiness."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ... import __version__
from ...config import get_settings
from ...db import get_db
from ...jobs import queue
from ...models import AnalysisExecution, DatasetCollection, PlanetaryRegion
from ...schemas import HealthResponse

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> dict:
    database = "ok"
    queue_stats: dict = {}
    counts: dict = {}
    try:
        db.execute(text("SELECT 1"))
        db.execute(text("SELECT postgis_version()"))
        queue_stats = queue.stats(db)
        counts = {
            "analyses": int(db.scalar(select(func.count()).select_from(AnalysisExecution)) or 0),
            "regions": int(db.scalar(select(func.count()).select_from(PlanetaryRegion)) or 0),
            "datasets": int(db.scalar(select(func.count()).select_from(DatasetCollection)) or 0),
        }
    except Exception as exc:
        database = f"unavailable: {type(exc).__name__}"
        db.rollback()
    return {
        "status": "ok" if database == "ok" else "degraded",
        "version": __version__,
        "environment": settings.terrax_env,
        "database": database,
        "worker_queue": {**queue_stats, "entity_counts": counts},
        "earthdata_configured": settings.earthdata_available,
        "llm_configured": settings.terrax_llm_provider != "none",
        "time": datetime.now(UTC),
    }
