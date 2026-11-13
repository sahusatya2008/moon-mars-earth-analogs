"""Copernicus DEM GLO-30 access (ESA/DLR, AWS Open Data).

The DEM is distributed as 1° x 1° Cloud Optimized GeoTIFF tiles in EGM2008 vertical
datum. Only intersecting tiles and windows are read.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import rasterio

from .raster import EARTH_RADIUS_KM, Segment, read_dataset_bbox

BASE = "https://copernicus-dem-30m.s3.amazonaws.com"


def _lat_name(lat: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00"


def _lon_name(lon: int) -> str:
    return f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00"


def tile_url(lat_floor: int, lon_floor: int) -> str:
    stem = f"Copernicus_DSM_COG_10_{_lat_name(lat_floor)}_{_lon_name(lon_floor)}_DEM"
    return f"{BASE}/{stem}/{stem}.tif"


def tiles_for_bbox(west: float, south: float, east: float, north: float) -> Iterator[tuple[int, int]]:
    # Clamp to valid Copernicus latitude coverage; global ocean tiles may be absent.
    south = max(-90.0, south)
    north = min(90.0, north)
    west = max(-180.0, west)
    east = min(180.0, east)
    lat0 = int(math.floor(south))
    lat1 = int(math.ceil(north)) - 1
    lon0 = int(math.floor(west))
    lon1 = int(math.ceil(east)) - 1
    for lat in range(lat0, lat1 + 1):
        for lon in range(lon0, lon1 + 1):
            # A 1-degree tile at 90N does not exist.
            if lat >= 90 or lat < -90:
                continue
            yield lat, lon


def _tile_bbox(lat: int, lon: int) -> tuple[float, float, float, float]:
    return (float(lon), float(lat), float(lon + 1), float(lat + 1))


def read_bbox(
    west: float, south: float, east: float, north: float, *, radius_km: float = EARTH_RADIUS_KM
) -> list[Segment]:
    """Read all Copernicus tiles intersecting a bbox as source segments.

    Missing ocean tiles are skipped; the caller's valid-fraction field then exposes gaps.
    """
    segments: list[Segment] = []
    for lat, lon in tiles_for_bbox(west, south, east, north):
        url = tile_url(lat, lon)
        tw, ts, te, tn = _tile_bbox(lat, lon)
        w = max(west, tw)
        s = max(south, ts)
        e = min(east, te)
        n = min(north, tn)
        if e <= w or n <= s:
            continue
        try:
            seg, _meta = read_dataset_bbox(
                url, (w, s, e, n), decimated_max=4096, radius_km=radius_km
            )
            segments.extend(seg)
        except Exception:
            # Ocean/void tiles return 404 from the public bucket; skip but preserve coverage gap.
            continue
    return segments


def tile_exists(lat: int, lon: int) -> bool:
    with rasterio.open(tile_url(lat, lon)) as ds:
        return ds.width > 0
