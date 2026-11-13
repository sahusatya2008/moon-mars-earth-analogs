"""Real-DEM terrain meshes for the 3D terrain viewer.

This module turns the same elevation products used by the scientific pipeline into a
compact, renderable height grid. It does not generate synthetic geometry and does not
use imagery: every vertex comes from the DEM window returned by the Moon/Mars reference
loader or the Earth Copernicus loader.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .config import get_settings
from .connectors.earth_dem import is_constant_zero_fallback, load_earth_dem
from .connectors.planetary_dem import load_reference_dem

settings = get_settings()

# v2 invalidates meshes written before the constant-zero fallback guard. A v1 Earth mesh for
# an ocean/void location could contain a flat 0 m surface marked as fully valid.
CACHE_VERSION = "terrain3d-v2"
DEFAULT_RESOLUTION_M = 500.0
SUPPORTED_BODIES = ("earth", "moon", "mars")
MAX_GRID_CELLS = 320


def _cache_path(
    body_id: str,
    lon: float,
    lat: float,
    size_km: float,
    resolution_m: float,
) -> Path:
    name = f"{body_id}_{lon:.4f}_{lat:.4f}_{size_km:.1f}_{resolution_m:.0f}.json"
    return settings.cache_dir / "terrain3d" / CACHE_VERSION / name


def _is_degenerate_payload(payload: dict[str, Any]) -> bool:
    """Reject a cached all-zero fallback mesh instead of serving it as measured terrain."""
    source = payload.get("source") or {}
    if source.get("dataset_id") != "aws_terrain_tiles":
        return False
    heights = payload.get("heights") or []
    values = [float(value) for row in heights for value in row if value is not None]
    return bool(values) and all(value == 0.0 for value in values)


def _source_metadata(dem: Any) -> dict[str, Any]:
    source: dict[str, Any] = {
        "dataset_id": dem.dataset_id,
        "product_id": dem.product_id,
        "source_url": dem.source_url,
        "citation": dem.citation,
        "native_resolution_m": dem.native_resolution_m,
        "vertical_uncertainty_m": dem.vertical_uncertainty_m,
        "method": dem.method,
    }
    license_value = getattr(dem, "license", None)
    if license_value:
        source["license"] = license_value
    return source


def _window_to_payload(
    window: Any,
    *,
    body_id: str,
    lon: float,
    lat: float,
    size_km: float,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Convert a GriddedWindow into JSON-safe heights without inventing missing values."""
    values = np.asarray(window.values, dtype=np.float64)
    valid = np.isfinite(values)
    valid_count = int(valid.sum())
    if valid_count == 0:
        raise ValueError(
            "no valid elevation samples in this window; choose a land or coverage area"
        )

    finite = values[valid]
    minimum = float(finite.min())
    maximum = float(finite.max())
    mean = float(finite.mean())

    heights: list[list[float | None]] = []
    for row in values:
        heights.append(
            [None if not math.isfinite(float(value)) else round(float(value), 2) for value in row]
        )

    return {
        "body_id": body_id,
        "center": {"lon": round(lon, 6), "lat": round(lat, 6)},
        "size_km": round(float(size_km), 3),
        "width": int(window.width),
        "height": int(window.height),
        "resolution_m": round(float(window.resolution_m), 2),
        "row_order": "north_to_south",
        "col_order": "west_to_east",
        "vertical_units": "m",
        "valid_fraction": round(float(window.valid_fraction), 6),
        "min_elevation_m": round(minimum, 2),
        "max_elevation_m": round(maximum, 2),
        "mean_elevation_m": round(mean, 2),
        "heights": heights,
        "source": source,
        "note": (
            "Real elevation samples from the stored DEM product. Geometry only; no imagery "
            "texture is used and missing cells are left as null rather than filled."
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "cache_hit": False,
    }


def build_terrain_mesh(
    body_id: str,
    lon: float,
    lat: float,
    size_km: float,
    resolution_m: float = DEFAULT_RESOLUTION_M,
) -> dict[str, Any]:
    """Build or read a cached 3D terrain payload for one footprint.

    ``resolution_m`` is a display sampling interval, not a new scientific product. The
    loader performs the same windowed read and local-AEQD reprojection used by the
    analysis pipeline.
    """
    if body_id not in SUPPORTED_BODIES:
        raise ValueError(f"unsupported body {body_id!r}")
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError("invalid center coordinates")
    if not (5.0 <= size_km <= 300.0):
        raise ValueError("size_km must be in [5, 300]")
    if not (60.0 <= resolution_m <= 2000.0):
        raise ValueError("resolution_m must be in [60, 2000]")

    cells = int(round(size_km * 1000.0 / resolution_m))
    if cells > MAX_GRID_CELLS:
        raise ValueError(
            f"requested footprint would produce {cells} cells; increase resolution_m or reduce size_km"
        )

    target = _cache_path(body_id, lon, lat, size_km, resolution_m)
    if target.exists() and target.stat().st_size > 100:
        try:
            payload = json.loads(target.read_text())
            if _is_degenerate_payload(payload):
                # A mesh cached before the fallback guard is not real terrain.
                target.unlink(missing_ok=True)
            else:
                payload["cache_hit"] = True
                return payload
        except (OSError, json.JSONDecodeError):
            # A corrupt cache is discarded and rebuilt from the authoritative source.
            target.unlink(missing_ok=True)

    if body_id == "earth":
        dem = load_earth_dem(lon, lat, size_km, resolution_m)
        if is_constant_zero_fallback(dem):
            raise ValueError(
                "AWS Terrain Tiles fallback returned a constant 0 m surface; "
                "no real elevation is available at this location. Choose a land area covered "
                "by Copernicus DEM or another measured DEM product."
            )
    else:
        dem = load_reference_dem(body_id, lon, lat, size_km, resolution_m)

    payload = _window_to_payload(
        dem.window,
        body_id=body_id,
        lon=lon,
        lat=lat,
        size_km=size_km,
        source=_source_metadata(dem),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, allow_nan=False))
    temporary.replace(target)
    return payload


__all__ = [
    "CACHE_VERSION",
    "DEFAULT_RESOLUTION_M",
    "SUPPORTED_BODIES",
    "build_terrain_mesh",
]
