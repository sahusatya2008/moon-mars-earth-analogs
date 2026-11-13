"""Real NASA imagery: globe textures and region previews."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ...catalog import dataset_map
from ...connectors import gibs, preview, trek

router = APIRouter(tags=["media"])


@router.get("/textures", response_model=list[dict])
def list_textures():
    return [
        {
            "body_id": "earth",
            "dataset_id": "nasa_gibs",
            "url": "/api/v1/textures/earth.jpg",
            "source": "NASA GIBS Blue Marble Shaded Relief and Bathymetry",
            "attribution": "NASA EOSDIS / GIBS",
            "citation": dataset_map()["nasa_gibs"]["citation"],
            "license": "Public domain (NASA)",
        },
        {
            "body_id": "moon",
            "dataset_id": "lro_wac_mosaic",
            "url": "/api/v1/textures/moon.jpg",
            "source": "NASA TREK LRO WAC global mosaic (303 ppd)",
            "attribution": trek.texture_attribution("moon"),
            "citation": dataset_map()["lro_wac_mosaic"]["citation"],
            "license": "Public domain (NASA)",
        },
        {
            "body_id": "mars",
            "dataset_id": "mars_viking_mdim21",
            "url": "/api/v1/textures/mars.jpg",
            "source": "NASA TREK Mars Viking MDIM21 color mosaic",
            "attribution": trek.texture_attribution("mars"),
            "citation": dataset_map()["mars_viking_mdim21"]["citation"],
            "license": "Public domain (NASA)",
        },
    ]


@router.get("/textures/{body_id}.jpg")
def get_texture(
    body_id: str,
    width: int = Query(default=2048, ge=512, le=4096),
):
    if body_id not in ("earth", "moon", "mars"):
        raise HTTPException(404, "unsupported body")
    try:
        path = gibs.texture_path(width) if body_id == "earth" else trek.texture_path(body_id, width)
    except Exception as exc:
        raise HTTPException(503, f"NASA texture service unavailable: {exc}") from exc
    if not path.exists():
        raise HTTPException(503, "texture could not be generated")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400", "X-Image-Source": body_id},
    )


@router.get("/previews/{body_id}/{lon}/{lat}/{size_km}.jpg")
def get_preview(
    body_id: str,
    lon: float,
    lat: float,
    size_km: float,
    width: int = Query(default=384, ge=128, le=1024),
):
    if body_id not in ("earth", "moon", "mars"):
        raise HTTPException(404, "unsupported body")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise HTTPException(422, "invalid coordinates")
    if not (1 <= size_km <= 300):
        raise HTTPException(422, "size_km must be in [1, 300]")
    try:
        result = preview.region_preview(body_id, lon, lat, size_km, width=width)
    except Exception as exc:
        raise HTTPException(503, f"NASA preview service unavailable: {exc}") from exc
    path = result["path"]
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Image-Source": result["source"],
            "X-Image-Attribution": result["attribution"],
        },
    )
