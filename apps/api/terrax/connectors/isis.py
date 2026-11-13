"""Windowed reads from tiled ISIS3 cubes (USGS/PDS planetary mosaics).

The LOLA 128 ppd global DEM is distributed as an ISIS3 cube with 128x128 tiles of
signed 16-bit samples. A 40 km lunar window intersects only a handful of tiles, so a
single reference patch costs ~100-200 KB instead of a multi-minute GDAL strip crawl.
Tiles are cached on disk after first access.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from rasterio.transform import Affine

from ..config import get_settings
from .http import SourceUnavailable
from .raster import MARS_RADIUS_KM, MOON_RADIUS_KM
from .striped_tiff import _fetch

settings = get_settings()

LOLA_128PPD = {
    "url": "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/Lunar_LRO_LOLA_DEM_Global_128ppd_v04.cub",
    "samples": 46080,
    "lines": 23040,
    "tile": 128,
    "start_byte": 65536,
    "dtype": "<i2",
    "scale": 0.5,
    "offset": 0.0,
    "nodata_raw": -32768,
    "radius_km": MOON_RADIUS_KM,
    "min_lon": 0.0,
    "max_lon": 360.0,
    "min_lat": -90.0,
    "max_lat": 90.0,
    "product_id": "LDEM_128 (LRO-L-LOLA-4-GDR-V1.0)",
    "citation": "Smith et al. (2010), The Lunar Orbiter Laser Altimeter on the Lunar Reconnaissance Orbiter Mission, Space Science Reviews 150, 209-241. PDS LOLA GDR, 128 pixels/degree.",
    "vertical_uncertainty_m": 3.0,
}


def _tile_path(product_id: str, row: int, col: int) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in product_id)[:60]
    return settings.cache_dir / "isis" / safe / f"{row}_{col}.bin"


def _fetch_tile(spec: dict, row: int, col: int) -> np.ndarray:
    tile = int(spec["tile"])
    path = _tile_path(spec["product_id"], row, col)
    if path.exists() and path.stat().st_size == tile * tile * 2:
        raw = path.read_bytes()
    else:
        tiles_across = math.ceil(spec["samples"] / tile)
        offset = int(spec["start_byte"]) + (row * tiles_across + col) * tile * tile * 2
        raw = _fetch(spec["url"], offset, offset + tile * tile * 2 - 1)
        if len(raw) != tile * tile * 2:
            raise SourceUnavailable(f"short ISIS tile {row},{col}: {len(raw)} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(raw)
        tmp.replace(path)
    return np.frombuffer(raw, dtype=spec["dtype"]).reshape(tile, tile)


def read_isis_bbox(
    spec: dict,
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    max_tiles: int = 64,
) -> tuple[np.ndarray, Affine]:
    """Read a lon/lat bbox from a tiled ISIS cube, with a local disk tile cache."""
    tile = int(spec["tile"])
    samples = int(spec["samples"])
    lines = int(spec["lines"])
    min_lon = float(spec["min_lon"])
    span_lon = float(spec["max_lon"]) - min_lon

    def to_domain(lon: float) -> float:
        while lon < min_lon:
            lon += 360.0
        while lon >= min_lon + 360.0:
            lon -= 360.0
        return lon

    w = to_domain(west)
    e = to_domain(east)
    if e < w:  # crosses the 0/360 seam
        e += 360.0
    # Pixel coordinates (samples increase east, lines increase south).
    col0 = int(math.floor((w - min_lon) / span_lon * samples))
    col1 = int(math.ceil((e - min_lon) / span_lon * samples))
    row0 = int(math.floor((float(spec["max_lat"]) - north) / (float(spec["max_lat"]) - float(spec["min_lat"])) * lines))
    row1 = int(math.ceil((float(spec["max_lat"]) - south) / (float(spec["max_lat"]) - float(spec["min_lat"])) * lines))
    col0 = max(0, col0)
    col1 = min(samples, col1)
    row0 = max(0, row0)
    row1 = min(lines, row1)
    if col1 <= col0 or row1 <= row0:
        raise ValueError("empty ISIS cube window")
    if w < 180.0 < e:
        raise ValueError("ISIS window crosses the 180-degree seam; split the request")

    tiles_across = math.ceil(samples / tile)
    tr0, tr1 = row0 // tile, (row1 - 1) // tile
    tc0, tc1 = col0 // tile, (col1 - 1) // tile
    jobs = [(r, c) for r in range(tr0, tr1 + 1) for c in range(tc0, min(tc1, tiles_across - 1) + 1)]
    if len(jobs) > max_tiles:
        raise ValueError(f"window intersects {len(jobs)} ISIS tiles; reduce the footprint")
    out = np.full(((tr1 - tr0 + 1) * tile, (tc1 - tc0 + 1) * tile), np.nan, dtype=np.float32)
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        futures = {pool.submit(_fetch_tile, spec, r, c): (r, c) for r, c in jobs}
        for future in as_completed(futures):
            r, c = futures[future]
            data = future.result().astype(np.float32)
            yy = (r - tr0) * tile
            xx = (c - tc0) * tile
            out[yy : yy + tile, xx : xx + tile] = data
    out *= float(spec["scale"])
    out += float(spec["offset"])
    out[out == np.float32(float(spec["nodata_raw"]) * float(spec["scale"]))] = np.nan

    # Crop to the requested pixel window.
    crop = out[row0 - tr0 * tile : row1 - tr0 * tile, col0 - tc0 * tile : col1 - tc0 * tile]
    px = 2.0 * math.pi * float(spec["radius_km"]) * 1000.0 / samples
    # The ISIS cube's longitude domain is 0..360 with east positive. Convert to a
    # body-centred equirectangular x axis so local AEQD reprojection aligns.
    left = (col0 / samples) * (2.0 * math.pi * float(spec["radius_km"]) * 1000.0)
    if col0 >= samples // 2:
        left -= 2.0 * math.pi * float(spec["radius_km"]) * 1000.0
    top = (0.5 - row0 / lines) * (math.pi * float(spec["radius_km"]) * 1000.0)
    return crop, Affine(px, 0.0, left, 0.0, -px, top)


def isis_profile(spec: dict) -> dict:
    return {
        "product_id": spec["product_id"],
        "citation": spec["citation"],
        "native_resolution_m": 2.0 * math.pi * float(spec["radius_km"]) * 1000.0 / int(spec["samples"]),
        "vertical_uncertainty_m": spec["vertical_uncertainty_m"],
        "url": spec["url"],
    }


__all__ = ["LOLA_128PPD", "read_isis_bbox", "isis_profile", "MARS_RADIUS_KM"]
