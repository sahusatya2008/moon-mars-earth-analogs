"""NASA TREK WMTS imagery for lunar and Martian globes and region previews.

These are real mission mosaics (LRO WAC / Mars Viking MDIM21), not synthetic textures.
Tiles are stitched and cached; source attribution is recorded in the dataset catalog.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from ..config import get_settings
from .http import SourceUnavailable, download

settings = get_settings()

LAYERS = {
    "moon": {
        "url": "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm/{z}/{y}/{x}.jpg",
        "attribution": "NASA/GSFC/Arizona State University, LRO WAC global mosaic (303 ppd), NASA TREK",
        "citation": "LRO WAC global mosaic v02, NASA Lunar Reconnaissance Orbiter Camera, NASA TREK.",
    },
    "mars": {
        "url": "https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/default/default028mm/{z}/{y}/{x}.jpg",
        "attribution": "NASA/JPL/USGS, Mars Viking MDIM21 color mosaic (232 m), NASA TREK",
        "citation": "Mars Viking MDIM21 color mosaic, NASA TREK.",
        "max_zoom": 7,
    },
}
LAYERS["moon"]["max_zoom"] = 8


def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    y = int(math.floor((90.0 - lat) / 180.0 * n))
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _tile_path(body: str, z: int, x: int, y: int) -> Path:
    return settings.cache_dir / "trek" / body / str(z) / str(x) / f"{y}.jpg"


def _fetch_tile(body: str, z: int, x: int, y: int) -> Path:
    path = _tile_path(body, z, x, y)
    if path.exists() and path.stat().st_size > 500:
        return path
    url = LAYERS[body]["url"].format(z=z, x=x, y=y)
    try:
        download(url, path, min_bytes=500)
    except Exception as exc:
        raise SourceUnavailable(f"TREK tile {body} {z}/{x}/{y}: {exc}") from exc
    return path


def texture_path(body: str, width: int = 1024) -> Path:
    if body == "earth":
        from .gibs import texture_path as earth_texture

        return earth_texture(width)
    zoom = 2 if width <= 1024 else 3
    target = settings.cache_dir / "textures" / f"{body}_{width}.jpg"
    if target.exists() and target.stat().st_size > 10_000:
        return target
    n = 2**zoom
    tile_px = 256
    mosaic = Image.new("RGB", (n * tile_px, n * tile_px))
    for x in range(n):
        for y in range(n // 2):
            path = _fetch_tile(body, zoom, x, y)
            tile = Image.open(path).convert("RGB")
            mosaic.paste(tile, (x * tile_px, y * tile_px))
    target.parent.mkdir(parents=True, exist_ok=True)
    mosaic.resize((width, width // 2), Image.Resampling.LANCZOS).save(target, quality=88)
    return target


def region_preview(body: str, west: float, south: float, east: float, north: float, *, width: int = 512) -> Path:
    """Stitch and crop a TREK WMTS preview for a region."""
    if body not in LAYERS:
        raise ValueError(f"TREK imagery is not defined for {body}")
    span = max(east - west, north - south)
    if span <= 0:
        raise ValueError("invalid preview bbox")
    zoom = int(round(math.log2(max(1.0, 720.0 / max(span, 1e-6)))))
    zoom = max(4, min(int(LAYERS[body].get("max_zoom", 8)), zoom))
    target = (
        settings.cache_dir
        / "previews"
        / f"{body}_{west:.4f}_{south:.4f}_{east:.4f}_{north:.4f}_{width}.jpg"
    )
    if target.exists() and target.stat().st_size > 5_000:
        return target

    n = 2**zoom
    px_per_deg = 256 * n / 360.0
    x0 = max(0, int(math.floor((west + 180.0) * px_per_deg / 256)))
    x1 = min(n - 1, int(math.floor((east + 180.0) * px_per_deg / 256)))
    y0 = max(0, int(math.floor((90.0 - north) * px_per_deg / 256)))
    y1 = min(n // 2 - 1, int(math.floor((90.0 - south) * px_per_deg / 256)))
    mosaic = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256))
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            path = _fetch_tile(body, zoom, x, y)
            mosaic.paste(Image.open(path).convert("RGB"), ((x - x0) * 256, (y - y0) * 256))
    left = (west + 180.0) * px_per_deg - x0 * 256
    top = (90.0 - north) * px_per_deg - y0 * 256
    right = (east + 180.0) * px_per_deg - x0 * 256
    bottom = (90.0 - south) * px_per_deg - y0 * 256
    crop = mosaic.crop((int(left), int(top), int(math.ceil(right)), int(math.ceil(bottom))))
    target.parent.mkdir(parents=True, exist_ok=True)
    crop.resize((width, width), Image.Resampling.LANCZOS).save(target, quality=88)
    return target


def texture_attribution(body: str) -> str:
    return LAYERS[body]["attribution"]


__all__ = ["LAYERS", "texture_path", "region_preview", "texture_attribution", "tile_xy"]
