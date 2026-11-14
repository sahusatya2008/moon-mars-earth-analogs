"""Planetary bodies and the authoritative dataset catalog with live probes."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...catalog import dataset_map
from ...connectors import (
    copernicus_dem,
    gibs,
    meteomatics,
    planetary_computer,
    trek,
    worldcover,
)
from ...connectors.http import head
from ...connectors.stac import SEARCH
from ...models import DatasetCollection, PlanetaryBody
from ...schemas import (
    AvailabilityReport,
    DatasetAvailability,
    DatasetListResponse,
    DatasetRead,
    PlanetRead,
)
from ..deps import get_db

router = APIRouter(tags=["catalog"])

PLANETARY_COMPUTER_DATASETS = {
    "nasadem_hgt_v001": "nasadem",
    "alos_world3d_30m": "alos-dem",
    "io_lulc_annual_v02": "io-lulc-annual-v02",
    "pc_sentinel2_l2a": "sentinel-2-l2a",
    "pc_landsat_c2_l2": "landsat-c2-l2",
}


@router.get("/planets", response_model=list[PlanetRead])
def list_planets(db: Session = Depends(get_db)):
    return db.scalars(select(PlanetaryBody).order_by(PlanetaryBody.display_order)).all()


@router.get("/planets/{body_id}", response_model=PlanetRead)
def get_planet(body_id: str, db: Session = Depends(get_db)):
    row = db.get(PlanetaryBody, body_id)
    if row is None:
        raise HTTPException(404, f"unknown body {body_id!r}")
    return row


@router.get("/datasets", response_model=DatasetListResponse)
def list_datasets(
    body_id: str | None = Query(default=None),
    scoring_only: bool = Query(default=False),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stmt = select(DatasetCollection).order_by(DatasetCollection.body_id, DatasetCollection.id)
    if body_id:
        stmt = stmt.where(DatasetCollection.body_id == body_id)
    if scoring_only:
        stmt = stmt.where(DatasetCollection.is_scoring_input.is_(True))
    if status:
        stmt = stmt.where(DatasetCollection.status == status)
    rows = db.scalars(stmt).all()
    return {"items": rows, "total": len(rows)}


def _probe_head(url: str, *, timeout: float = 10.0) -> tuple[str, str, int | None]:
    try:
        response = head(url, timeout=timeout)
    except Exception as exc:
        return "unavailable", str(exc)[:300], None
    if response.status_code < 400:
        return "available", f"HTTP {response.status_code}", response.status_code
    if response.status_code in (401, 403):
        return "requires_credentials", f"HTTP {response.status_code}", response.status_code
    return "degraded", f"HTTP {response.status_code}", response.status_code


def _probe_range(url: str) -> tuple[str, str, int | None]:
    try:
        r = httpx.get(url, headers={"Range": "bytes=0-0"}, timeout=12.0, follow_redirects=True)
    except Exception as exc:
        return "unavailable", str(exc)[:300], None
    if r.status_code in (200, 206):
        return "available", f"HTTP {r.status_code}; range read returned {len(r.content)} byte(s)", r.status_code
    if r.status_code in (401, 403):
        return "requires_credentials", f"HTTP {r.status_code}", r.status_code
    return "degraded", f"HTTP {r.status_code}", r.status_code


def probe_dataset(dataset_id: str) -> DatasetAvailability:
    now = datetime.now(UTC)
    checked_at = now
    spec = dataset_map().get(dataset_id)
    if spec is None:
        return DatasetAvailability(
            dataset_id=dataset_id,
            status="unknown",
            detail="dataset is not in the authoritative catalog",
            checked_at=checked_at,
        )
    started = time.perf_counter()
    http_status: int | None = None
    probe_url = spec["source_url"]

    if dataset_id == "nasa_gibs":
        status, detail, http_status = gibs.probe()
        probe_url = gibs.BASE
    elif dataset_id in PLANETARY_COMPUTER_DATASETS:
        collection = PLANETARY_COMPUTER_DATASETS[dataset_id]
        status, detail, http_status = planetary_computer.probe(collection)
        probe_url = planetary_computer.collection_url(collection)
    elif dataset_id == "copernicus_dem_glo30":
        ok = copernicus_dem.tile_exists(36, 138)
        status, detail = (
            ("available", "Copernicus GLO-30 tile N36E138 is reachable")
            if ok
            else ("unavailable", "Copernicus GLO-30 probe tile could not be read")
        )
        http_status = 200 if ok else None
        probe_url = copernicus_dem.tile_url(36, 138)
    elif dataset_id == "aws_terrain_tiles":
        status, detail, http_status = _probe_head(
            "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/4/8/5.png"
        )
        probe_url = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/4/8/5.png"
    elif dataset_id == "esa_worldcover_2021":
        status, detail, http_status = _probe_head(worldcover._tile_url(36, 138))
        probe_url = worldcover._tile_url(36, 138)
    elif dataset_id in ("lro_wac_mosaic", "mars_viking_mdim21"):
        body = "moon" if dataset_id == "lro_wac_mosaic" else "mars"
        z, x, y = 0, 0, 0
        probe_url = trek.LAYERS[body]["url"].format(z=z, x=x, y=y)
        status, detail, http_status = _probe_head(probe_url)
    elif dataset_id == "sentinel2_l2a":
        try:
            r = httpx.get(f"{SEARCH.rsplit('/search', 1)[0]}/collections/sentinel-2-l2a", timeout=12.0)
            http_status = r.status_code
            status = "available" if r.status_code < 400 else "degraded"
            detail = f"Earth Search collection metadata HTTP {r.status_code}"
        except Exception as exc:
            status, detail = "unavailable", str(exc)[:300]
        probe_url = f"{SEARCH.rsplit('/search', 1)[0]}/collections/sentinel-2-l2a"
    elif dataset_id == "nasa_cmr":
        probe_url = "https://cmr.earthdata.nasa.gov/search/collections.json?page_size=1"
        status, detail, http_status = _probe_head(probe_url)
    elif dataset_id == "meteomatics_weather":
        status, detail, http_status = meteomatics.probe()
        probe_url = meteomatics.BASE
    elif dataset_id == "firms_viirs":
        from ...config import get_settings

        if not get_settings().nasa_firms_map_key:
            status = "requires_credentials"
            detail = "NASA_FIRMS_MAP_KEY is not configured; the service is not reported as available."
            http_status = None
        else:
            status, detail, http_status = _probe_head(spec["source_url"])
    elif spec.get("requires_credentials"):
        from ...config import get_settings

        if getattr(get_settings(), spec.get("credential_env_var", "").lower(), None):
            status, detail, http_status = _probe_head(spec["source_url"])
        else:
            status = "requires_credentials"
            detail = f"{spec.get('credential_env_var')} is not configured."
    elif dataset_id == "etopo_2022":
        status, detail, http_status = _probe_range(spec["source_url"])
    else:
        status, detail, http_status = _probe_head(spec["source_url"])

    latency = round((time.perf_counter() - started) * 1000.0, 1)
    return DatasetAvailability(
        dataset_id=dataset_id,
        status=status,
        detail=detail,
        checked_at=checked_at,
        http_status=http_status,
        latency_ms=latency,
        probe_url=probe_url,
    )


@router.post("/datasets/probe", response_model=AvailabilityReport)
def probe_datasets(
    body_id: str | None = Query(default=None),
    dataset_id: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(DatasetCollection)
    if body_id:
        stmt = stmt.where(DatasetCollection.body_id == body_id)
    if dataset_id:
        stmt = stmt.where(DatasetCollection.id.in_(dataset_id))
    rows = db.scalars(stmt.order_by(DatasetCollection.body_id, DatasetCollection.id).limit(limit)).all()
    if not rows:
        raise HTTPException(404, "no matching datasets")
    results: list[DatasetAvailability] = []
    for row in rows:
        availability = probe_dataset(row.id)
        results.append(availability)
        row.last_checked_at = availability.checked_at
        row.status = availability.status
        row.status_detail = availability.detail
        if availability.status == "available":
            row.last_success_at = availability.checked_at
    db.commit()
    available = sum(1 for r in results if r.status == "available")
    credentials = sum(1 for r in results if r.status == "requires_credentials")
    unavailable = sum(1 for r in results if r.status in ("unavailable", "degraded"))
    return {
        "datasets": results,
        "checked_at": datetime.now(UTC),
        "available": available,
        "requires_credentials": credentials,
        "unavailable": unavailable,
    }


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    row = db.get(DatasetCollection, dataset_id)
    if row is None:
        raise HTTPException(404, f"unknown dataset {dataset_id!r}")
    return row
