"""Natural Earth land mask used for coarse screening (not for final boundaries)."""

from __future__ import annotations

import json
from functools import lru_cache

from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from ..config import get_settings
from .http import download

settings = get_settings()
URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson"


@lru_cache(maxsize=1)
def _land():
    path = settings.cache_dir / "sources" / "ne_110m_land.geojson"
    if not path.exists():
        download(URL, path, min_bytes=10_000)
    data = json.loads(path.read_text())
    geom = unary_union([shape(f["geometry"]) for f in data["features"]])
    return prep(geom), geom


def _bbox_area(west: float, south: float, east: float, north: float) -> float:
    return max(1e-9, (east - west) * (north - south))


def land_fraction(west: float, south: float, east: float, north: float) -> float:
    prepared, geom = _land()
    if west <= east:
        b = box(west, south, east, north)
        return float(b.intersection(geom).area / _bbox_area(west, south, east, north))
    # Antimeridian: split into two boxes.
    a1 = box(west, south, 180.0, north)
    a2 = box(-180.0, south, east, north)
    total = (180.0 - west + east + 180.0) * (north - south)
    return float((a1.intersection(geom).area + a2.intersection(geom).area) / max(1e-9, total))


def is_mostly_land(west: float, south: float, east: float, north: float, threshold: float = 0.6) -> bool:
    return land_fraction(west, south, east, north) >= threshold


def land_geometry():
    return _land()[1]
