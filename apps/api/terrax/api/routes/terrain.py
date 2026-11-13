"""Real-DEM 3D terrain meshes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ...terrain3d import DEFAULT_RESOLUTION_M, SUPPORTED_BODIES, build_terrain_mesh

router = APIRouter(tags=["terrain"])


@router.get("/terrain/{body_id}/{lon}/{lat}/{size_km}.json")
def get_terrain(
    body_id: str,
    lon: float,
    lat: float,
    size_km: float,
    resolution_m: float = Query(default=DEFAULT_RESOLUTION_M, ge=60.0, le=2000.0),
):
    if body_id not in SUPPORTED_BODIES:
        raise HTTPException(404, "unsupported body")
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise HTTPException(422, "invalid coordinates")
    if not (5.0 <= size_km <= 300.0):
        raise HTTPException(422, "size_km must be in [5, 300]")
    try:
        payload = build_terrain_mesh(body_id, lon, lat, size_km, resolution_m)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"terrain model unavailable: {exc}") from exc

    source = payload.get("source", {})
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Terrain-Source": str(source.get("dataset_id", "unknown")),
            "X-Terrain-Cache": "hit" if payload.get("cache_hit") else "miss",
        },
    )
