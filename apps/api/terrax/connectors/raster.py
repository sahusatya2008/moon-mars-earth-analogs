"""Geospatial raster primitives shared by connectors and the scientific engine.

All remote reads are windowed. Elevation is reprojected to a local azimuthal equidistant
grid in metres before slope/roughness are computed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject
from rasterio.windows import Window

from .http import apply_gdal_env

apply_gdal_env()

EARTH_RADIUS_KM = 6371.0088
MOON_RADIUS_KM = 1737.4
MARS_RADIUS_KM = 3389.5


@dataclass(frozen=True)
class Segment:
    array: np.ndarray
    transform: Affine
    crs: str
    nodata: float | None


@dataclass
class GriddedWindow:
    values: np.ndarray
    valid: np.ndarray
    transform: Affine
    crs: str
    width: int
    height: int
    resolution_m: float
    native_resolution_m: float | None
    valid_fraction: float
    source_bounds: tuple[float, float, float, float]
    method: str

    @property
    def coverage_km2(self) -> float:
        return float(self.valid.sum()) * (self.resolution_m / 1000.0) ** 2


def footprint_bbox(lon: float, lat: float, size_km: float, radius_km: float) -> tuple[float, float, float, float, bool]:
    """Return west, south, east, north, antimeridian_crossing for a spherical square.

    Longitude width grows toward the poles; at the cap the window spans all longitudes.
    """
    half = size_km / 2.0
    dlat = math.degrees(half / radius_km)
    south = max(-90.0, lat - dlat)
    north = min(90.0, lat + dlat)
    if north - south >= 179.999 or abs(lat) + dlat >= 89.999:
        return -180.0, south, 180.0, north, False
    coslat = max(abs(math.cos(math.radians(max(-89.9, min(89.9, lat))))), 1e-6)
    dlon = min(180.0, math.degrees(half / (radius_km * coslat)))
    west = lon - dlon
    east = lon + dlon
    crossing = False
    if west < -180.0:
        west += 360.0
        crossing = True
    if east > 180.0:
        east -= 360.0
        crossing = True
    return west, south, east, north, crossing


def _split_bbox(west: float, east: float, crossing: bool) -> list[tuple[float, float]]:
    if crossing:
        # west is east of east in this convention (e.g. 170 -> -170)
        return [(west, 180.0), (-180.0, east)]
    return [(west, east)]


def _body_geographic_crs(radius_km: float):
    from rasterio.crs import CRS

    return CRS.from_proj4(f"+proj=longlat +R={radius_km * 1000.0:.3f} +no_defs")


def _bbox_in_dataset_crs(
    ds: rasterio.DatasetReader,
    west: float,
    south: float,
    east: float,
    north: float,
    radius_km: float | None,
) -> tuple[float, float, float, float]:
    """Map a body lon/lat bbox into the dataset's native CRS.

    Planetary equirectangular products are projected rasters with a body-radius sphere,
    so they must not be interpreted as Earth degrees. Geographic rasters (EPSG:4326 or
    an explicit lunar/Martian geographic CRS) pass through unchanged.
    """
    if ds.crs is None or ds.crs.is_geographic:
        return west, south, east, north
    if radius_km is None:
        raise ValueError(
            f"{ds.name} is projected ({ds.crs}); a body radius is required to map lon/lat"
        )
    from pyproj import Transformer

    transformer = Transformer.from_crs(
        _body_geographic_crs(radius_km), ds.crs, always_xy=True
    )
    xs = []
    ys = []
    for lon in (west, east):
        for lat in (south, north):
            x, y = transformer.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def lonlat_window(
    ds: rasterio.DatasetReader,
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    radius_km: float | None = None,
) -> tuple[Window, Affine]:
    """Window for an equirectangular source without relying on ellipsoid datum shifts.

    Handles both geographic rasters (including 0..360 longitude conventions) and
    body-fixed projected equirectangular products such as USGS LOLA/MOLA mosaics.
    """
    if ds.crs is None or ds.crs.is_geographic:
        w = max(-180.0, min(180.0, west))
        e = max(-180.0, min(180.0, east))
        s = max(-90.0, min(90.0, south))
        n = max(-90.0, min(90.0, north))
        if ds.bounds.left >= 0.0 and w < 0.0:
            w += 360.0
            e += 360.0
        x0, y0 = ~ds.transform * (w, n)
        x1, y1 = ~ds.transform * (e, s)
    else:
        x0, y0, x1, y1 = _bbox_in_dataset_crs(ds, west, south, east, north, radius_km)
        col0, row_n = ~ds.transform * (x0, y1)
        col1, row_s = ~ds.transform * (x1, y0)
        x0, y0, x1, y1 = col0, row_n, col1, row_s
    col0 = max(0, min(ds.width, int(math.floor(min(x0, x1)))))
    col1 = max(0, min(ds.width, int(math.ceil(max(x0, x1)))))
    row0 = max(0, min(ds.height, int(math.floor(min(y0, y1)))))
    row1 = max(0, min(ds.height, int(math.ceil(max(y0, y1)))))
    if col1 <= col0 or row1 <= row0:
        raise ValueError(f"empty raster window for bbox {(west, south, east, north)}")
    window = Window(col0, row0, col1 - col0, row1 - row0)
    return window, ds.window_transform(window)


def read_dataset_bbox(
    path_or_url: str,
    bbox: tuple[float, float, float, float],
    band: int = 1,
    *,
    decimated_max: int = 4096,
    radius_km: float | None = None,
    resampling: Resampling = Resampling.bilinear,
) -> tuple[list[Segment], dict[str, object]]:
    """Read segments of a single equirectangular raster covering a lon/lat bbox.

    Returns segments and product metadata. Handles antimeridian crossing by splitting.
    ``radius_km`` is required when the source is a projected body-fixed
    equirectangular raster (Moon/Mars). Categorical products must pass
    ``Resampling.nearest`` so class codes are never interpolated.
    """
    west, south, east, north = bbox
    crossing = west > east
    chunks = _split_bbox(west, east, crossing)
    segments: list[Segment] = []
    meta: dict[str, object] = {}
    with rasterio.open(path_or_url) as ds:
        meta = {
            "source": path_or_url,
            "crs": str(ds.crs),
            "native_resolution_m": _native_resolution_m(ds, radius_km),
            "width": ds.width,
            "height": ds.height,
            "dtype": ds.dtypes[band - 1],
            "nodata": ds.nodata,
            "bounds": tuple(ds.bounds),
        }
        for (w, e) in chunks:
            if e <= w:
                continue
            window, transform = lonlat_window(ds, w, south, e, north, radius_km=radius_km)
            # Bound the number of pixels read; use decimated reads when necessary.
            scale = max(1, int(math.ceil(max(window.width, window.height) / decimated_max)))
            if scale > 1:
                arr = ds.read(
                    band,
                    window=window,
                    out_shape=(max(1, int(window.height) // scale), max(1, int(window.width) // scale)),
                    resampling=resampling,
                )
                transform = transform * Affine.scale(window.width / arr.shape[1], window.height / arr.shape[0])
            else:
                arr = ds.read(band, window=window)
                transform = transform
            nodata = ds.nodata
            if nodata is None:
                nodata = _default_nodata(arr.dtype)
            arr = arr.astype(np.float32)
            if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
                arr = np.where(arr == np.float32(nodata), np.nan, arr)
            segments.append(Segment(array=arr, transform=transform, crs=str(ds.crs), nodata=nodata))
    return segments, meta


def _default_nodata(dtype: np.dtype) -> float | None:
    if np.issubdtype(dtype, np.floating):
        return float("nan")
    if np.issubdtype(dtype, np.signedinteger):
        return float(np.iinfo(dtype).min)
    return None


def _native_resolution_m(ds: rasterio.DatasetReader, radius_km: float | None = None) -> float | None:
    try:
        res = abs(ds.transform.a)
        if not res:
            return None
        if ds.crs is not None and ds.crs.is_geographic:
            if radius_km is None:
                return None
            return float(math.radians(res) * radius_km * 1000.0)
        return float(res)
    except Exception:
        return None


def local_aeqd_crs(lon: float, lat: float, radius_km: float) -> str:
    return (
        f"+proj=aeqd +lat_0={lat:.8f} +lon_0={lon:.8f} +R={radius_km * 1000.0:.3f} "
        "+units=m +no_defs +type=crs"
    )


def make_local_grid(size_km: float, resolution_m: float) -> tuple[Affine, int, int]:
    n = max(8, int(round(size_km * 1000.0 / resolution_m)))
    half = size_km * 1000.0 / 2.0
    transform = from_origin(-half, half, resolution_m, resolution_m)
    return transform, n, n


def warp_segments_to_local(
    segments: Sequence[Segment],
    center_lon: float,
    center_lat: float,
    size_km: float,
    radius_km: float,
    resolution_m: float,
    *,
    categorical: bool = False,
) -> GriddedWindow:
    dst_crs = local_aeqd_crs(center_lon, center_lat, radius_km)
    dst_transform, width, height = make_local_grid(size_km, resolution_m)
    if categorical:
        dest = np.zeros((height, width), dtype=np.float32)
        mask = np.zeros((height, width), dtype=np.uint8)
        for seg in segments:
            src = seg.array.astype(np.float32)
            src_valid = np.isfinite(src) if np.issubdtype(src.dtype, np.floating) else src != seg.nodata
            src = np.where(src_valid, src, 0.0)
            tmp = np.zeros((height, width), dtype=np.float32)
            tmp_mask = np.zeros((height, width), dtype=np.uint8)
            reproject(
                source=src,
                destination=tmp,
                src_transform=seg.transform,
                src_crs=seg.crs,
                src_nodata=None,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=0,
                resampling=Resampling.nearest,
            )
            reproject(
                source=src_valid.astype(np.uint8),
                destination=tmp_mask,
                src_transform=seg.transform,
                src_crs=seg.crs,
                src_nodata=0,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=0,
                resampling=Resampling.nearest,
            )
            take = (tmp_mask > 0) & (mask == 0)
            dest[take] = tmp[take]
            mask[take] = 1
        valid = mask > 0
        dest = np.where(valid, dest, np.nan)
    else:
        dest = np.full((height, width), np.nan, dtype=np.float32)
        for seg in segments:
            src = seg.array.astype(np.float32)
            src_valid = np.isfinite(src)
            tmp = np.full((height, width), np.nan, dtype=np.float32)
            reproject(
                source=src,
                destination=tmp,
                src_transform=seg.transform,
                src_crs=seg.crs,
                src_nodata=np.nan,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            take = np.isfinite(tmp) & ~np.isfinite(dest)
            dest[take] = tmp[take]
        valid = np.isfinite(dest)

    valid_fraction = float(valid.mean()) if valid.size else 0.0
    native = None
    if segments:
        # Caller can override with product metadata; this is a conservative estimate.
        native = float(abs(segments[0].transform.a)) if segments[0].transform.a else None
    return GriddedWindow(
        values=dest,
        valid=valid,
        transform=dst_transform,
        crs=dst_crs,
        width=width,
        height=height,
        resolution_m=resolution_m,
        native_resolution_m=native,
        valid_fraction=valid_fraction,
        source_bounds=(0, 0, 0, 0),
        method="bilinear" if not categorical else "nearest",
    )


def sample_raster_bbox(
    path_or_url: str,
    bbox: tuple[float, float, float, float],
    band: int = 1,
    *,
    max_pixels: int = 600_000,
    radius_km: float | None = None,
) -> dict[str, float | int | None]:
    """Sample valid pixels from a raster bbox; used for lower-resolution context layers."""
    segments, meta = read_dataset_bbox(path_or_url, bbox, band=band, decimated_max=2048, radius_km=radius_km)
    values = []
    for seg in segments:
        arr = np.asarray(seg.array, dtype=np.float32)
        vals = arr[np.isfinite(arr)]
        if vals.size:
            # Bound memory for massive windows.
            if vals.size > max_pixels:
                idx = np.linspace(0, vals.size - 1, max_pixels).astype(np.int64)
                vals = vals[idx]
            values.append(vals)
    if not values:
        return {"count": 0, "mean": None, "std": None, "median": None, "p10": None, "p90": None, "valid_fraction": 0.0}
    allv = np.concatenate(values)
    return {
        "count": int(allv.size),
        "mean": float(np.mean(allv)),
        "std": float(np.std(allv)),
        "median": float(np.median(allv)),
        "p10": float(np.percentile(allv, 10)),
        "p90": float(np.percentile(allv, 90)),
        "valid_fraction": float(allv.size / max(1, sum(seg.array.size for seg in segments))),
        "native_resolution_m": meta.get("native_resolution_m"),
    }
