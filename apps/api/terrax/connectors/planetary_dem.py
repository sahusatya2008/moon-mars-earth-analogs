"""Reference DEM access for the Moon and Mars.

Lunar: LOLA 128 ppd ISIS tiled cube (237 m/pixel) with a per-tile disk cache.
Mars: MGS MOLA 463 m global mosaic, read through the fast striped-TIFF path and cached as
a local crop.

Both are reprojected onto the same 500 m local azimuthal-equidistant grid used for Earth.
Absolute elevation is retained for context but never scored across bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio

from ..config import get_settings
from .isis import LOLA_128PPD, isis_profile, read_isis_bbox
from .raster import (
    MARS_RADIUS_KM,
    MOON_RADIUS_KM,
    GriddedWindow,
    Segment,
    footprint_bbox,
    warp_segments_to_local,
)
from .striped_tiff import read_striped_segment

settings = get_settings()

MOLA = {
    "url": "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/Mars_MGS_MOLA_DEM_mosaic_global_463m.tif",
    "radius_km": MARS_RADIUS_KM,
    "native_resolution_m": 463.09,
    "vertical_uncertainty_m": 5.0,
    "product_id": "MGS-M-MOLA-5-MEGDR-L3-V1.0",
    "citation": "Smith et al. (2001), Mars Orbiter Laser Altimeter: Experiment summary after the first year of global mapping of Mars, JGR 106(E10), 23689-23722. PDS MOLA MEGDR, 128 pixels/degree.",
}

BODY_DEMS = {"moon": LOLA_128PPD, "mars": MOLA}


@dataclass
class ReferenceDEM:
    window: GriddedWindow
    body_id: str
    dataset_id: str
    product_id: str
    source_url: str
    citation: str
    native_resolution_m: float
    vertical_uncertainty_m: float
    method: str


def _body_equirectangular_crs(radius_km: float) -> str:
    return (
        f"+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 "
        f"+R={radius_km * 1000.0:.3f} +units=m +no_defs"
    )


def _crop_path(body_id: str, lon: float, lat: float, size_km: float) -> Path:
    return settings.cache_dir / "planetary" / body_id / f"{lon:.3f}_{lat:.3f}_{size_km:.1f}.tif"


def _cached_mars_crop(lon: float, lat: float, size_km: float) -> tuple[np.ndarray, object, bool]:
    """Return a cached native-resolution MOLA crop plus its transform."""
    path = _crop_path("mars", lon, lat, size_km)
    if path.exists() and path.stat().st_size > 10_000:
        with rasterio.open(path) as ds:
            return ds.read(1), ds.transform, True
    bbox = footprint_bbox(lon, lat, size_km, MARS_RADIUS_KM)
    out = read_striped_segment(
        MOLA["url"], bbox[0], bbox[1], bbox[2], bbox[3], radius_km=MARS_RADIUS_KM
    )
    if out is None:
        raise RuntimeError("MOLA mosaic layout is not eligible for the fast striped reader")
    arr, transform = out
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": arr.shape[0],
        "width": arr.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": _body_equirectangular_crs(MARS_RADIUS_KM),
        "transform": transform,
        "nodata": np.nan,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(arr.astype(np.float32), 1)
        ds.update_tags(product_id=MOLA["product_id"], citation=MOLA["citation"])
    return arr, transform, False


def load_reference_dem(
    body_id: str,
    lon: float,
    lat: float,
    size_km: float,
    resolution_m: float = 500.0,
) -> ReferenceDEM:
    if body_id not in BODY_DEMS:
        raise ValueError(f"no reference DEM for body {body_id!r}")
    if body_id == "moon":
        spec = LOLA_128PPD
        bbox = footprint_bbox(lon, lat, size_km, MOON_RADIUS_KM)
        arr, transform = read_isis_bbox(spec, *bbox[:4])
        profile = isis_profile(spec)
        dataset_id = "lro_lola_ldem_128ppd"
        segment = Segment(
            array=arr,
            transform=transform,
            crs=_body_equirectangular_crs(MOON_RADIUS_KM),
            nodata=None,
        )
        native = float(profile["native_resolution_m"])
        vertical = float(profile["vertical_uncertainty_m"])
        product_id = spec["product_id"]
        citation = spec["citation"]
        url = spec["url"]
        method = "LOLA 128 ppd ISIS tiled cube, bilinear reprojection to local AEQD"
    else:
        arr, transform, cached = _cached_mars_crop(lon, lat, size_km)
        dataset_id = "mgs_mola_megdr_463m"
        segment = Segment(
            array=arr.astype(np.float32),
            transform=transform,
            crs=_body_equirectangular_crs(MARS_RADIUS_KM),
            nodata=None,
        )
        native = MOLA["native_resolution_m"]
        vertical = MOLA["vertical_uncertainty_m"]
        product_id = MOLA["product_id"]
        citation = MOLA["citation"]
        url = MOLA["url"]
        method = (
            "MOLA 463 m global mosaic, fast range read"
            + (" (disk cache)" if cached else "")
            + " and bilinear reprojection to local AEQD"
        )

    window = warp_segments_to_local(
        [segment], lon, lat, size_km, BODY_DEMS[body_id]["radius_km"], resolution_m
    )
    return ReferenceDEM(
        window=window,
        body_id=body_id,
        dataset_id=dataset_id,
        product_id=product_id,
        source_url=url,
        citation=citation,
        native_resolution_m=native,
        vertical_uncertainty_m=vertical,
        method=method,
    )


__all__ = ["BODY_DEMS", "MOLA", "ReferenceDEM", "load_reference_dem"]
