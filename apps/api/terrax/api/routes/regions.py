"""Reference regions (Moon/Mars) and preset regions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from shapely.geometry import box
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...connectors.raster import MARS_RADIUS_KM, MOON_RADIUS_KM, footprint_bbox
from ...models import AnalysisExecution, PlanetaryBody, PlanetaryRegion
from ...results import region_dict
from ...schemas import ReferenceRegionCreate, RegionRead
from ..deps import audit, get_db

router = APIRouter(tags=["regions"])
RADII = {"moon": MOON_RADIUS_KM, "mars": MARS_RADIUS_KM}


def _region_geometry(body_id: str, lon: float, lat: float, size_km: float) -> str:
    west, south, east, north, crossing = footprint_bbox(lon, lat, size_km, RADII[body_id])
    if crossing:
        raise HTTPException(
            422,
            "reference footprint crosses the 180° meridian; choose a center farther from the seam",
        )
    geom = box(west, south, east, north)
    return f"SRID=4326;{geom.wkt}"


@router.get("/regions", response_model=list[RegionRead])
def list_regions(
    body_id: str | None = Query(default=None),
    preset: bool | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stmt = select(PlanetaryRegion).order_by(
        PlanetaryRegion.body_id, PlanetaryRegion.is_preset.desc(), PlanetaryRegion.name
    )
    if body_id:
        stmt = stmt.where(PlanetaryRegion.body_id == body_id)
    if preset is not None:
        stmt = stmt.where(PlanetaryRegion.is_preset.is_(preset))
    rows = db.scalars(stmt).all()
    return [region_dict(row) for row in rows]


@router.get("/regions/{region_id}", response_model=RegionRead)
def get_region(region_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(PlanetaryRegion, region_id)
    if row is None:
        raise HTTPException(404, "region not found")
    return region_dict(row)


@router.post("/regions", response_model=RegionRead, status_code=201)
def create_region(payload: ReferenceRegionCreate, db: Session = Depends(get_db)):
    if payload.body_id not in RADII:
        raise HTTPException(422, "reference regions must be on the Moon or Mars")
    if db.get(PlanetaryBody, payload.body_id) is None:
        raise HTTPException(422, f"unknown body {payload.body_id!r}")
    region = PlanetaryRegion(
        body_id=payload.body_id,
        name=payload.name,
        kind="user_reference",
        description=payload.description,
        center_lon=payload.center_lon,
        center_lat=payload.center_lat,
        size_km=payload.size_km,
        geometry=_region_geometry(
            payload.body_id, payload.center_lon, payload.center_lat, payload.size_km
        ),
        source=payload.source,
        source_url=payload.source_url,
        citation=payload.citation,
        is_preset=False,
        metadata_json={"preset": False},
    )
    db.add(region)
    db.flush()
    audit(
        db,
        action="region.create",
        resource_type="planetary_region",
        resource_id=str(region.id),
        details={"body_id": region.body_id, "name": region.name},
    )
    db.commit()
    return region_dict(region)


@router.delete("/regions/{region_id}", status_code=204)
def delete_region(region_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(PlanetaryRegion, region_id)
    if row is None:
        raise HTTPException(404, "region not found")
    if row.is_preset:
        raise HTTPException(409, "preset regions cannot be deleted")
    used = db.scalar(
        select(func.count())
        .select_from(AnalysisExecution)
        .where(AnalysisExecution.reference_region_id == region_id)
    )
    if used:
        raise HTTPException(409, "region is referenced by one or more analyses")
    db.delete(row)
    audit(db, action="region.delete", resource_type="planetary_region", resource_id=str(region_id))
    db.commit()
    return None
