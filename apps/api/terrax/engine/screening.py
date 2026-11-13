"""Coarse global screening on real Earth terrain tiles.

Screening compares like with like: the reference high-resolution DEM is block-averaged to
the screening grid, and Earth candidates are measured at that same grid. Coarse rankings
are used only to choose refinement sites; final scores always come from the 500 m common
grid. The limitation is recorded in every run manifest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

from ..connectors.landmask import land_geometry
from ..connectors.raster import EARTH_RADIUS_KM

# Deliberately small, transparent coarse feature set. Tolerances are physical and declared.
COARSE_SPECS: dict[str, dict[str, float]] = {
    "relief_range": {"weight": 0.30, "tolerance": 250.0},
    "elevation_std": {"weight": 0.20, "tolerance": 120.0},
    "slope_mean": {"weight": 0.20, "tolerance": 3.0},
    "slope_std": {"weight": 0.10, "tolerance": 2.5},
    "roughness": {"weight": 0.15, "tolerance": 8.0},
    "hypsometric_integral": {"weight": 0.05, "tolerance": 0.10},
}


@dataclass
class CoarseProfile:
    features: dict[str, float]
    valid_fraction: float


def coarse_profile(z: np.ndarray, resolution_m: float) -> CoarseProfile | None:
    arr = np.asarray(z, dtype=np.float64)
    valid = np.isfinite(arr)
    if valid.sum() < 16 or valid.mean() < 0.4:
        return None
    filled = arr.copy()
    if not valid.all():
        indices = ndimage.distance_transform_edt(
            ~valid, return_distances=False, return_indices=True
        )
        filled = arr[tuple(indices)]
    vals = filled[valid]
    p05, p50, p95 = np.percentile(vals, [5, 50, 95])
    gy, gx = np.gradient(filled, resolution_m, resolution_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))[valid]
    residual = filled - ndimage.gaussian_filter(filled, sigma=1.5, mode="nearest")
    features = {
        "relief_range": float(p95 - p05),
        "elevation_std": float(np.std(vals)),
        "slope_mean": float(np.mean(slope)),
        "slope_std": float(np.std(slope)),
        "roughness": float(np.std(residual[valid])),
        "hypsometric_integral": float(np.mean(vals > p50)),
    }
    return CoarseProfile(features=features, valid_fraction=float(valid.mean()))


def coarse_similarity(reference: CoarseProfile, candidate: CoarseProfile) -> tuple[float, dict[str, Any]]:
    total_w = 0.0
    total = 0.0
    detail = []
    for key, spec in COARSE_SPECS.items():
        rv = reference.features.get(key)
        cv = candidate.features.get(key)
        if rv is None or cv is None or not np.isfinite(rv) or not np.isfinite(cv):
            continue
        delta = float(cv - rv)
        sim = float(math.exp(-0.5 * (delta / spec["tolerance"]) ** 2))
        total += spec["weight"] * sim
        total_w += spec["weight"]
        detail.append(
            {
                "feature": key,
                "reference": round(rv, 4),
                "candidate": round(cv, 4),
                "delta": round(delta, 4),
                "tolerance": spec["tolerance"],
                "similarity": round(sim, 6),
            }
        )
    score = total / total_w if total_w else 0.0
    return float(np.clip(score * math.sqrt(candidate.valid_fraction), 0.0, 1.0)), {
        "features": detail,
        "coverage": round(candidate.valid_fraction, 4),
    }


def block_average(values: np.ndarray, factor: int) -> np.ndarray:
    """NaN-aware block mean; crops to a whole number of blocks."""
    if factor <= 1:
        return values.astype(np.float64)
    h = (values.shape[0] // factor) * factor
    w = (values.shape[1] // factor) * factor
    if h == 0 or w == 0:
        return values.astype(np.float64)
    blocks = values[:h, :w].reshape(h // factor, factor, w // factor, factor)
    valid = np.isfinite(blocks)
    counts = valid.sum(axis=(1, 3))
    sums = np.where(valid, blocks, 0.0).sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(counts > 0, sums / counts, np.nan)
    return out.astype(np.float64)


def reference_coarse_profile(window, screening_resolution_m: float) -> CoarseProfile:
    """Measure the reference at exactly the screening scale by block averaging its DEM."""
    factor = max(1, int(round(screening_resolution_m / window.resolution_m)))
    averaged = block_average(np.asarray(window.values, dtype=np.float64), factor)
    profile = coarse_profile(averaged, window.resolution_m * factor)
    if profile is None:
        # Tiny footprints at very coarse screening resolution still need a triage profile.
        profile = coarse_profile(np.asarray(window.values, dtype=np.float64), window.resolution_m)
    assert profile is not None
    return profile


def _value(features: dict[str, Any], key: str) -> float:
    f = features.get(key)
    v = getattr(f, "value", None) if not isinstance(f, dict) else f.get("value")
    return float(v) if v is not None and np.isfinite(v) else 0.0


def screening_centers(
    west: float,
    south: float,
    east: float,
    north: float,
    cell_deg: float,
    *,
    max_lat: float = 85.0,
    land_points: int = 5,
) -> list[tuple[float, float]]:
    """Grid of candidate centres over land, excluding polar tile gaps."""
    geom = land_geometry()
    from shapely.geometry import Point
    from shapely.prepared import prep

    prepared = prep(geom)
    centers: list[tuple[float, float]] = []
    lat = south + cell_deg / 2.0
    while lat < min(north, max_lat):
        lon = west + cell_deg / 2.0
        while lon < east:
            # Check the centre and four quarter offsets so a 40 km footprint touching a
            # coastline is not discarded because its exact centre is water.
            pts = [(lon, lat)]
            d = cell_deg * 0.25
            pts += [(lon - d, lat), (lon + d, lat), (lon, lat - d), (lon, lat + d)]
            hits = sum(1 for x, y in pts[:land_points] if prepared.contains(Point(x, y)))
            if hits >= 1:
                centers.append((lon, lat))
            lon += cell_deg
        lat += cell_deg
    return centers


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = EARTH_RADIUS_KM
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def greedy_cluster(
    points: list[dict[str, Any]], radius_km: float, limit: int | None = None
) -> list[dict[str, Any]]:
    """Keep the best point in each spatial neighbourhood (deterministic, score-ordered)."""
    ordered = sorted(points, key=lambda p: -float(p["score"]))
    kept: list[dict[str, Any]] = []
    for point in ordered:
        if any(
            haversine_km(point["lon"], point["lat"], k["lon"], k["lat"]) < radius_km
            for k in kept
        ):
            continue
        kept.append(point)
        if limit is not None and len(kept) >= limit:
            break
    return kept


__all__ = [
    "COARSE_SPECS",
    "CoarseProfile",
    "coarse_profile",
    "coarse_similarity",
    "reference_coarse_profile",
    "screening_centers",
    "haversine_km",
    "greedy_cluster",
]
