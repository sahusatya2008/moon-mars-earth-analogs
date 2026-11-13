"""Cached region previews from real NASA imagery services."""

from __future__ import annotations

from ..config import get_settings
from . import gibs, trek
from .raster import EARTH_RADIUS_KM, MARS_RADIUS_KM, MOON_RADIUS_KM, footprint_bbox

settings = get_settings()
RADII = {"earth": EARTH_RADIUS_KM, "moon": MOON_RADIUS_KM, "mars": MARS_RADIUS_KM}


def region_preview(body_id: str, lon: float, lat: float, size_km: float = 40.0, *, width: int = 384) -> dict:
    bbox = footprint_bbox(lon, lat, size_km, RADII.get(body_id, EARTH_RADIUS_KM))
    if body_id == "earth":
        target = (
            settings.cache_dir
            / "previews"
            / f"earth_{lon:.4f}_{lat:.4f}_{size_km:.1f}_{width}.jpg"
        )
        path = gibs.download_preview(bbox[0], bbox[1], bbox[2], bbox[3], target, width=width, height=width)
        attribution = "NASA GIBS / Blue Marble Shaded Relief and Bathymetry"
        source = "NASA GIBS WMS (EPSG:4326)"
    elif body_id in ("moon", "mars"):
        path = trek.region_preview(body_id, bbox[0], bbox[1], bbox[2], bbox[3], width=width)
        attribution = trek.texture_attribution(body_id)
        source = "NASA TREK WMTS"
    else:
        raise ValueError(f"unsupported preview body {body_id}")
    return {
        "path": str(path),
        "url": f"/api/previews/{body_id}/{lon:.4f}/{lat:.4f}/{size_km:.1f}.jpg",
        "bbox": list(bbox[:4]),
        "attribution": attribution,
        "source": source,
    }


__all__ = ["region_preview"]
