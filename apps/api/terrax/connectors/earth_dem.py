"""Refined Earth DEM windows (Copernicus GLO-30, with a real terrain-tile fallback).

Copernicus GLO-30 is the primary measurement. If a tile is unavailable (ocean/void), the
fallback is AWS Terrain Tiles, recorded explicitly in metadata. Refined windows are cached
so repeated analyses and tests do not re-download the same 40 km patch.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rasterio.transform import Affine

from ..config import get_settings
from .copernicus_dem import read_bbox
from .raster import (
    EARTH_RADIUS_KM,
    GriddedWindow,
    footprint_bbox,
    read_dataset_bbox,
    warp_segments_to_local,
)
from .terrain_tiles import build_screening_dem

settings = get_settings()

CACHE_VERSION = "earth-v2"


@dataclass
class EarthDEM:
    window: GriddedWindow
    dataset_id: str
    product_id: str
    source_url: str
    citation: str
    license: str
    native_resolution_m: float
    vertical_uncertainty_m: float
    method: str
    cached: bool = False
    elapsed_s: float = 0.0


def _cache_dir() -> Path:
    path = settings.cache_dir / "earth_dem"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_paths(lon: float, lat: float, size_km: float, resolution_m: float) -> tuple[Path, Path]:
    key = f"{CACHE_VERSION}_{lon:.4f}_{lat:.4f}_{size_km:.1f}_{resolution_m:.0f}"
    base = _cache_dir() / key
    return base.with_suffix(".npz"), base.with_suffix(".json")


def _load_cache(npz: Path, meta_path: Path) -> EarthDEM | None:
    if not npz.exists() or not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    data = np.load(npz)
    transform = Affine(*meta["transform"])
    window = GriddedWindow(
        values=data["values"],
        valid=data["valid"],
        transform=transform,
        crs=meta["crs"],
        width=int(data["values"].shape[1]),
        height=int(data["values"].shape[0]),
        resolution_m=float(meta["resolution_m"]),
        native_resolution_m=float(meta["native_resolution_m"]),
        valid_fraction=float(meta["valid_fraction"]),
        source_bounds=tuple(meta["source_bounds"]),
        method=meta["method"],
    )
    return EarthDEM(
        window=window,
        dataset_id=meta["dataset_id"],
        product_id=meta["product_id"],
        source_url=meta["source_url"],
        citation=meta["citation"],
        license=meta["license"],
        native_resolution_m=float(meta["native_resolution_m"]),
        vertical_uncertainty_m=float(meta["vertical_uncertainty_m"]),
        method=meta["method"],
        cached=True,
    )


def _save_cache(paths: tuple[Path, Path], dem: EarthDEM) -> None:
    npz, meta_path = paths
    np.savez_compressed(
        npz,
        values=dem.window.values.astype(np.float32),
        valid=dem.window.valid,
    )
    meta = {
        "dataset_id": dem.dataset_id,
        "product_id": dem.product_id,
        "source_url": dem.source_url,
        "citation": dem.citation,
        "license": dem.license,
        "native_resolution_m": dem.native_resolution_m,
        "vertical_uncertainty_m": dem.vertical_uncertainty_m,
        "resolution_m": dem.window.resolution_m,
        "valid_fraction": dem.window.valid_fraction,
        "crs": dem.window.crs,
        "transform": list(dem.window.transform[:6]),
        "source_bounds": list(dem.window.source_bounds),
        "method": dem.method,
    }
    meta_path.write_text(json.dumps(meta, indent=2))


def _copernicus(lon: float, lat: float, size_km: float, resolution_m: float, bbox) -> EarthDEM:
    segments = read_bbox(*bbox[:4])
    if not segments:
        raise RuntimeError("Copernicus returned no intersecting tiles")
    window = warp_segments_to_local(
        segments, lon, lat, size_km, EARTH_RADIUS_KM, resolution_m
    )
    if window.valid_fraction < 0.2:
        raise RuntimeError(f"Copernicus coverage too low ({window.valid_fraction:.1%})")
    return EarthDEM(
        window=window,
        dataset_id="copernicus_dem_glo30",
        product_id="Copernicus_DSM_COG_10",
        source_url="https://copernicus-dem-30m.s3.amazonaws.com/",
        citation=(
            "Copernicus DEM GLO-30, © ESA/DLR, produced from WorldDEM and distributed "
            "under the Copernicus DEM open licence on AWS Open Data."
        ),
        license="Copernicus DEM open licence",
        native_resolution_m=30.0,
        vertical_uncertainty_m=2.0,
        method="Copernicus GLO-30 native window, bilinear reprojection to local AEQD",
    )


def _terrain_fallback(lon: float, lat: float, size_km: float, resolution_m: float, bbox) -> EarthDEM:
    path = build_screening_dem(
        bbox[0], bbox[1], bbox[2], bbox[3], resolution_m=resolution_m, zoom=12
    )
    segments, meta = read_dataset_bbox(str(path), (bbox[0], bbox[1], bbox[2], bbox[3]))
    window = warp_segments_to_local(
        segments, lon, lat, size_km, EARTH_RADIUS_KM, resolution_m
    )
    native = float(meta.get("native_resolution_m") or 30.0)
    return EarthDEM(
        window=window,
        dataset_id="aws_terrain_tiles",
        product_id="terrarium-z12",
        source_url="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        citation=(
            "AWS Terrain Tiles (Mapzen/Tilezen), aggregation of SRTM, GMTED2010, ETOPO1 and "
            "other public elevation sources. Used as a fallback where Copernicus is unavailable."
        ),
        license="Open Data Commons Attribution License (ODC-By)",
        native_resolution_m=native,
        vertical_uncertainty_m=8.0,
        method="AWS Terrain Tiles z12 fallback, reprojected to local AEQD",
    )


def is_constant_zero_fallback(dem: EarthDEM) -> bool:
    """True for the degenerate all-zero surface the tile fallback returns over water/voids.

    The fallback can legitimately contain some zero-elevation cells, but a whole window of
    exact zeros is not a measured land surface. Treating it as valid would let the API present
    a flat, fabricated-looking mesh as real terrain.
    """
    if dem.dataset_id != "aws_terrain_tiles":
        return False
    values = np.asarray(dem.window.values, dtype=np.float64)
    valid = np.asarray(dem.window.valid, dtype=bool)
    finite = values[valid & np.isfinite(values)]
    if finite.size == 0:
        return False
    return bool(np.all(finite == 0.0))


def _reject_constant_zero_fallback(dem: EarthDEM) -> None:
    if is_constant_zero_fallback(dem):
        raise ValueError(
            "AWS Terrain Tiles fallback returned a constant 0 m surface; "
            "no real elevation is available at this location. Choose a land area covered "
            "by Copernicus DEM or another measured DEM product."
        )


def load_earth_dem(
    lon: float,
    lat: float,
    size_km: float,
    resolution_m: float = 500.0,
    *,
    allow_fallback: bool = True,
) -> EarthDEM:
    paths = _cache_paths(lon, lat, size_km, resolution_m)
    cached = _load_cache(*paths)
    if cached is not None:
        if is_constant_zero_fallback(cached):
            # Remove a cache entry written before the degenerate-fallback guard existed.
            for path in paths:
                path.unlink(missing_ok=True)
        else:
            return cached
    bbox = footprint_bbox(lon, lat, size_km, EARTH_RADIUS_KM)
    started = time.time()
    try:
        dem = _copernicus(lon, lat, size_km, resolution_m, bbox)
    except Exception:
        if not allow_fallback:
            raise
        dem = _terrain_fallback(lon, lat, size_km, resolution_m, bbox)
    _reject_constant_zero_fallback(dem)
    dem.elapsed_s = round(time.time() - started, 2)
    _save_cache(paths, dem)
    return dem


__all__ = ["EarthDEM", "is_constant_zero_fallback", "load_earth_dem"]
