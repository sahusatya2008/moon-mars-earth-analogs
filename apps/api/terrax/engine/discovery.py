"""Two-stage analogue discovery: coarse screening, then 500 m refinement.

Stage 1 samples a real global/regional terrain mosaic. Stage 2 re-measures selected sites
with Copernicus GLO-30 and scores every candidate with the common physical feature set.
Coarse scores are never presented as final similarity.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import rasterio

from ..config import get_settings
from ..connectors.earth_dem import EarthDEM, load_earth_dem
from ..connectors.terrain_tiles import build_screening_dem
from .features import TerrainProfile, extract_terrain_profile
from .screening import (
    coarse_profile,
    coarse_similarity,
    greedy_cluster,
    reference_coarse_profile,
    screening_centers,
)
from .similarity import CandidateScore, score_candidates

settings = get_settings()

ProgressFn = Callable[[float, str, str], None]


@dataclass
class RefinedCandidate:
    candidate_id: str
    lon: float
    lat: float
    coarse_score: float
    dem: EarthDEM
    profile: TerrainProfile
    score: CandidateScore | None = None


@dataclass
class DiscoveryResult:
    candidates: list[RefinedCandidate]
    scores: list[CandidateScore]
    screening: dict[str, Any]
    refinement: dict[str, Any]
    failures: list[dict[str, Any]] = field(default_factory=list)


def _noop(_p: float, _s: str, _m: str) -> None:
    return None


def _choose_zoom(span_deg: float) -> int:
    if span_deg > 80:
        return 4
    if span_deg > 30:
        return 5
    if span_deg > 12:
        return 6
    if span_deg > 4:
        return 7
    return 9


def _sample_window(
    array: np.ndarray,
    transform,
    lon: float,
    lat: float,
    half_km: float,
) -> np.ndarray | None:
    res_deg = abs(transform.a)
    res_m = res_deg * 111_320.0 * max(0.2, math.cos(math.radians(lat)))
    px = max(2, int(round(half_km * 1000.0 / max(res_m, 1.0))))
    col = int(round((lon - transform.c) / transform.a))
    row = int(round((transform.f - lat) / abs(transform.e)))
    r0, r1 = row - px, row + px
    c0, c1 = col - px, col + px
    if r0 < 0 or c0 < 0 or r1 > array.shape[0] or c1 > array.shape[1]:
        return None
    return array[r0:r1, c0:c1]


def _coarse_points(
    array: np.ndarray,
    transform,
    centers: list[tuple[float, float]],
    reference,
    half_km: float,
    screening_res_m: float,
    *,
    floor: float = 0.0,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for lon, lat in centers:
        sample = _sample_window(array, transform, lon, lat, half_km)
        if sample is None or sample.size < 16:
            continue
        profile = coarse_profile(sample, screening_res_m)
        if profile is None:
            continue
        score, detail = coarse_similarity(reference, profile)
        if score < floor:
            continue
        points.append(
            {
                "lon": round(float(lon), 5),
                "lat": round(float(lat), 5),
                "score": float(score),
                "coarse": detail,
            }
        )
    return points


def discover(
    reference_profile: TerrainProfile,
    reference_window,
    *,
    body_id: str,
    search_config: dict[str, Any],
    footprint_km: float,
    analysis_resolution_m: float,
    dimension_weights: dict[str, float],
    methods: list[str],
    min_feature_coverage: float,
    missing_data_policy: str,
    progress: ProgressFn = _noop,
) -> DiscoveryResult:
    bbox = list(search_config["bbox"])
    west, south, east, north = [float(v) for v in bbox]
    cell_deg = float(search_config.get("cell_size_deg", 2.0))
    screening_res_km = float(search_config.get("screening_resolution_km", 5.0))
    refine_top_n = int(search_config.get("refine_top_n", 24))
    cluster_radius_km = float(search_config.get("cluster_radius_km", 100.0))
    max_candidates = int(search_config.get("max_candidates", 25))
    max_lat = float(search_config.get("exclude_polar_gt_deg", 85.0))

    span = max(east - west, north - south)
    zoom = _choose_zoom(span)

    progress(0.04, "screening_dem", "Acquiring real Earth terrain for coarse screening")
    dem_path = build_screening_dem(
        west,
        south,
        east,
        north,
        resolution_m=screening_res_km * 1000.0,
        zoom=zoom,
    )
    with rasterio.open(dem_path) as ds:
        screen = ds.read(1)
        transform = ds.transform

    reference_coarse = reference_coarse_profile(reference_window, screening_res_km * 1000.0)
    progress(0.12, "screening_grid", f"Scoring coarse terrain over {span:.0f}° search window")

    centers = screening_centers(
        west, south, east, north, cell_deg, max_lat=max_lat
    )
    half_km = max(footprint_km / 2.0, screening_res_km * 2.0)
    first = _coarse_points(
        screen,
        transform,
        centers,
        reference_coarse,
        half_km,
        screening_res_km * 1000.0,
    )
    if not first:
        raise RuntimeError(
            "No valid land screening cells were found in the search bbox. "
            "Widen the bbox or check terrain source availability."
        )

    # Densify around the best coarse cells (not a global brute-force grid).
    first.sort(key=lambda p: -p["score"])
    top_cells = first[: min(48, max(8, len(first) // 10))]
    step = cell_deg / 5.0
    dense_centers: list[tuple[float, float]] = []
    for point in top_cells:
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                lon = point["lon"] + dx * step
                lat = point["lat"] + dy * step
                if west <= lon <= east and south <= lat <= north and abs(lat) <= max_lat:
                    dense_centers.append((lon, lat))
    second = _coarse_points(
        screen,
        transform,
        dense_centers,
        reference_coarse,
        half_km,
        screening_res_km * 1000.0,
    )
    all_points = first + second
    selected = greedy_cluster(all_points, cluster_radius_km, limit=refine_top_n)
    progress(
        0.24,
        "screening_complete",
        f"{len(all_points):,} coarse samples → {len(selected)} refinement sites",
    )

    # Stage 2: high-resolution refinement.
    refined: list[RefinedCandidate] = []
    failures: list[dict[str, Any]] = []
    total = max(1, len(selected))
    for index, point in enumerate(selected):
        lon, lat = float(point["lon"]), float(point["lat"])
        try:
            dem = load_earth_dem(
                lon, lat, footprint_km, analysis_resolution_m, allow_fallback=True
            )
            if dem.window.valid_fraction < 0.25:
                raise RuntimeError(f"valid coverage {dem.window.valid_fraction:.1%}")
            profile = extract_terrain_profile(
                dem.window,
                native_resolution_m=dem.native_resolution_m,
                vertical_uncertainty_m=dem.vertical_uncertainty_m,
                product_method=dem.method,
            )
            refined.append(
                RefinedCandidate(
                    candidate_id=f"cand-{index:03d}",
                    lon=lon,
                    lat=lat,
                    coarse_score=float(point["score"]),
                    dem=dem,
                    profile=profile,
                )
            )
        except Exception as exc:  # a single inaccessible tile must not fail the run
            failures.append({"lon": lon, "lat": lat, "error": str(exc)[:400]})
        progress(
            0.24 + 0.56 * (index + 1) / total,
            "refinement",
            f"Refined {index + 1}/{total} sites · {len(failures)} unavailable",
        )

    if not refined:
        raise RuntimeError(
            "No refinement site produced a valid 40 km Earth DEM window. "
            f"First error: {failures[0]['error'] if failures else 'unknown'}"
        )

    progress(0.84, "similarity", "Scoring refined candidates across declared dimensions")
    profiles = {c.candidate_id: c.profile for c in refined}
    scores = score_candidates(
        reference_profile,
        profiles,
        dimension_weights=dimension_weights,
        min_feature_coverage=min_feature_coverage,
        methods=methods,
        missing_data_policy=missing_data_policy,
    )
    by_id = {c.candidate_id: c for c in refined}
    for score in scores:
        by_id[score.candidate_id].score = score

    # Final spatial thinning on refined scores.
    scored_points = [
        {
            "candidate_id": c.candidate_id,
            "lon": c.lon,
            "lat": c.lat,
            "score": c.score.overall_score if c.score else 0.0,
        }
        for c in refined
    ]
    keep = greedy_cluster(scored_points, cluster_radius_km, limit=max_candidates)
    keep_ids = [p["candidate_id"] for p in keep]
    final_candidates = [by_id[cid] for cid in keep_ids]
    final_scores = [by_id[cid].score for cid in keep_ids if by_id[cid].score is not None]
    progress(1.0, "discovery_complete", f"{len(final_candidates)} final candidates")

    screening = {
        "source": "AWS Terrain Tiles (Mapzen/Tilezen)",
        "zoom": zoom,
        "resolution_m": screening_res_km * 1000.0,
        "bbox": [west, south, east, north],
        "grid_cell_deg": cell_deg,
        "coarse_samples": len(all_points),
        "coarse_cells": len(first),
        "densified_cells": len(top_cells),
        "refinement_sites": len(selected),
        "reference_profile": {
            "features": reference_coarse.features,
            "valid_fraction": reference_coarse.valid_fraction,
        },
        "feature_specs": {
            k: v for k, v in __import__(
                "terrax.engine.screening", fromlist=["COARSE_SPECS"]
            ).COARSE_SPECS.items()
        },
        "limitation": (
            "Coarse screening uses an aggregated Earth terrain mosaic and is used only to "
            "choose refinement sites. Final similarity uses Copernicus GLO-30 at the common "
            "500 m grid. Sparse 40 km features can be missed by any coarse screen."
        ),
    }
    refinement = {
        "attempted": len(selected),
        "succeeded": len(refined),
        "failed": len(failures),
        "resolution_m": analysis_resolution_m,
        "footprint_km": footprint_km,
        "datasets": sorted({c.dem.dataset_id for c in refined}),
    }
    return DiscoveryResult(
        candidates=final_candidates,
        scores=final_scores,
        screening=screening,
        refinement=refinement,
        failures=failures,
    )


__all__ = ["discover", "DiscoveryResult", "RefinedCandidate"]
