"""AWS Terrain Tiles (Mapzen) screening DEM.

These tiles are used *only* for coarse global candidate screening. They aggregate public
elevation sources and are not a primary measurement. Every refined candidate is re-measured
from Copernicus DEM GLO-30. Attribution and provenance are recorded in the dataset catalog.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

from ..config import get_settings
from .http import SourceUnavailable, download

settings = get_settings()

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ATTRIBUTION = (
    "AWS Terrain Tiles (Mapzen/Tilezen), aggregation of SRTM, GMTED2010, ETOPO1, "
    "USGS NED and other public sources. Screening use only."
)
MAX_LAT = 85.05112878


def _deg2num(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0 * n
    return x, y


def _num2deg(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2**z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


def _tile_path(z: int, x: int, y: int) -> Path:
    return settings.cache_dir / "terrain" / str(z) / str(x) / f"{y}.png"


def _fetch_tile(z: int, x: int, y: int) -> Path:
    path = _tile_path(z, x, y)
    if path.exists() and path.stat().st_size > 500:
        return path
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        download(url, path, min_bytes=500)
    except Exception as exc:
        raise SourceUnavailable(f"terrain tile {z}/{x}/{y}: {exc}") from exc
    return path


def _decode_tile(path: Path) -> np.ndarray:
    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    return rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0 - 32768.0


def _mosaic_for_bbox(west: float, south: float, east: float, north: float, zoom: int) -> tuple[np.ndarray, object]:
    x0f, y0f = _deg2num(west, north, zoom)
    x1f, y1f = _deg2num(east, south, zoom)
    x0, y0 = int(math.floor(x0f)), int(math.floor(y0f))
    x1, y1 = int(math.ceil(x1f)), int(math.ceil(y1f))
    x1 = min(2**zoom - 1, x1)
    y1 = min(2**zoom - 1, y1)
    xs = list(range(x0, x1 + 1))
    ys = list(range(y0, y1 + 1))
    array = np.full((len(ys) * 256, len(xs) * 256), np.nan, dtype=np.float32)
    jobs = [(x, y) for y in ys for x in xs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_tile, zoom, x, y): (x, y) for x, y in jobs}
        for future in as_completed(futures):
            x, y = futures[future]
            try:
                tile = _decode_tile(future.result())
            except Exception:
                continue
            row = (y - y0) * 256
            col = (x - x0) * 256
            array[row : row + 256, col : col + 256] = tile
    res = 156543.03392804097 / (2**zoom)
    transform = from_origin(-20037508.342789244 + x0 * 256 * res, 20037508.342789244 - y0 * 256 * res, res, res)
    return array, transform


def build_screening_dem(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    resolution_m: float = 5000.0,
    zoom: int | None = None,
) -> Path:
    """Build/caches an equirectangular screening DEM for the requested bbox.

    Returns a local GeoTIFF path in EPSG:4326. Real tiles only; gaps remain nodata.
    """
    span = max(east - west, north - south)
    if zoom is None:
        zoom = 5 if span > 40 else 6 if span > 12 else 7
    key = f"screen_{west:.3f}_{south:.3f}_{east:.3f}_{north:.3f}_{resolution_m:.0f}_z{zoom}"
    out = settings.cache_dir / "screening" / f"{key}.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 10_000:
        return out

    mosaic, src_transform = _mosaic_for_bbox(west, south, east, north, zoom)
    res_deg = max(0.005, resolution_m / 111_320.0)
    width = max(1, int(math.ceil((east - west) / res_deg)))
    height = max(1, int(math.ceil((north - south) / res_deg)))
    dst_transform = from_origin(west, north, res_deg, res_deg)
    dest = np.full((height, width), np.nan, dtype=np.float32)
    reproject(
        source=mosaic,
        destination=dest,
        src_transform=src_transform,
        src_crs="EPSG:3857",
        src_nodata=np.nan,
        dst_transform=dst_transform,
        dst_crs="EPSG:4326",
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": dst_transform,
        "nodata": np.nan,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    with rasterio.open(out, "w", **profile) as ds:
        ds.write(dest, 1)
        ds.update_tags(
            source="AWS Terrain Tiles (Mapzen/Tilezen)",
            attribution=ATTRIBUTION,
            zoom=str(zoom),
            screening_only="true",
        )
    return out


def local_path_for(
    west: float, south: float, east: float, north: float, resolution_m: float = 5000.0
) -> Path:
    return build_screening_dem(west, south, east, north, resolution_m=resolution_m)
