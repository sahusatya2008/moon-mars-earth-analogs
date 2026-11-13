"""Administrative maintenance endpoints (optional key)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...jobs import queue
from ...seed import seed
from ..deps import audit, get_db, require_admin
from .catalog import probe_dataset

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/admin/seed")
def reseed(db: Session = Depends(get_db)):
    counts = seed(db)
    audit(db, action="admin.seed", details=counts)
    db.commit()
    return {"status": "ok", "counts": counts}


@router.post("/admin/probe")
def probe(
    dataset_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    results = []
    for dataset_id in dataset_ids or []:
        result = probe_dataset(dataset_id)
        results.append(result)
        from ...models import DatasetCollection

        row = db.get(DatasetCollection, dataset_id)
        if row is not None:
            row.status = result.status
            row.status_detail = result.detail
            row.last_checked_at = result.checked_at
            if result.status == "available":
                row.last_success_at = result.checked_at
    audit(db, action="admin.probe", details={"dataset_ids": dataset_ids or []})
    db.commit()
    return {"results": results}


@router.post("/admin/recover-jobs")
def recover(db: Session = Depends(get_db)):
    count = queue.recover_stale(db)
    audit(db, action="admin.recover_jobs", details={"count": count})
    db.commit()
    return {"recovered": count}
