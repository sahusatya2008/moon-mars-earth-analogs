"""Terrain feature extraction on a common local metric grid.

All features are physically defined. Slope and curvature are computed *after* reprojection
to a local azimuthal-equidistant grid, so Earth, Moon, and Mars windows share metre spacing.
No absolute-elevation feature is used for cross-body similarity; absolute elevation is kept
for display only.

Feature definitions follow docs/science/methodology.md. Feature tolerances used by the
transparent MCDA kernel are declared in ``FEATURE_SPECS`` and recorded in every run manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage, stats

from ..connectors.raster import GriddedWindow

# A feature with weight 0.0 is measured and stored for context/evidence but never scored.
# The ``tolerance`` is the physical 1-sigma scale of the Gaussian similarity kernel:
# s = exp(-0.5 * (delta / tolerance)^2).
FEATURE_SPECS: dict[str, dict[str, Any]] = {
    # --- relief / hypsometry -------------------------------------------------
    "relief_range": {"dimension": "geomorphology", "unit": "m", "tolerance": 250.0, "weight": 0.20, "log": False},
    "elevation_std": {"dimension": "geomorphology", "unit": "m", "tolerance": 120.0, "weight": 0.10, "log": False},
    "elevation_p10_p90": {"dimension": "geomorphology", "unit": "m", "tolerance": 250.0, "weight": 0.10, "log": False},
    "elevation_skew": {"dimension": "geomorphology", "unit": "-", "tolerance": 0.55, "weight": 0.05, "log": False},
    "elevation_kurtosis": {"dimension": "geomorphology", "unit": "-", "tolerance": 1.6, "weight": 0.04, "log": False},
    "hypsometric_integral": {"dimension": "geomorphology", "unit": "-", "tolerance": 0.12, "weight": 0.08, "log": False},
    "elevation_entropy": {"dimension": "geomorphology", "unit": "-", "tolerance": 0.12, "weight": 0.06, "log": False},
    "relief_ratio": {"dimension": "geomorphology", "unit": "-", "tolerance": 0.010, "weight": 0.05, "log": False},
    "elevation_distribution": {"dimension": "geomorphology", "unit": "m", "tolerance": 210.0, "weight": 0.12, "log": False, "distribution": True},
    # --- slope / curvature / landform fractions -----------------------------
    "slope_mean": {"dimension": "morphometry", "unit": "deg", "tolerance": 4.0, "weight": 0.15, "log": False},
    "slope_p95": {"dimension": "morphometry", "unit": "deg", "tolerance": 8.0, "weight": 0.08, "log": False},
    "slope_std": {"dimension": "morphometry", "unit": "deg", "tolerance": 3.0, "weight": 0.07, "log": False},
    "slope_gt_15": {"dimension": "morphometry", "unit": "-", "tolerance": 0.10, "weight": 0.09, "log": False},
    "slope_gt_25": {"dimension": "morphometry", "unit": "-", "tolerance": 0.08, "weight": 0.06, "log": False},
    "curvature_std": {"dimension": "morphometry", "unit": "-", "tolerance": 8.0, "weight": 0.08, "log": False},
    "curvature_mean_abs": {"dimension": "morphometry", "unit": "-", "tolerance": 5.0, "weight": 0.04, "log": False},
    "ridge_fraction": {"dimension": "morphometry", "unit": "-", "tolerance": 0.08, "weight": 0.08, "log": False},
    "depression_fraction": {"dimension": "morphometry", "unit": "-", "tolerance": 0.08, "weight": 0.08, "log": False},
    "slope_distribution": {"dimension": "morphometry", "unit": "deg", "tolerance": 4.0, "weight": 0.12, "log": False, "distribution": True},
    # --- multi-scale texture -------------------------------------------------
    "roughness_rms": {"dimension": "texture", "unit": "m", "tolerance": 8.0, "weight": 0.20, "log": False},
    "roughness_p95": {"dimension": "texture", "unit": "m", "tolerance": 12.0, "weight": 0.10, "log": False},
    "tri_mean": {"dimension": "texture", "unit": "m", "tolerance": 10.0, "weight": 0.14, "log": False},
    "tpi_std": {"dimension": "texture", "unit": "m", "tolerance": 8.0, "weight": 0.09, "log": False},
    "anisotropy": {"dimension": "texture", "unit": "-", "tolerance": 0.15, "weight": 0.14, "log": False},
    "fractal_dimension": {"dimension": "texture", "unit": "-", "tolerance": 0.12, "weight": 0.14, "log": False},
    "roughness_scale_ratio": {"dimension": "texture", "unit": "-", "tolerance": 0.30, "weight": 0.10, "log": False},
    # --- operational / trafficability (derived, research-only) --------------
    "trafficable_fraction": {"dimension": "operational", "unit": "-", "tolerance": 0.15, "weight": 0.40, "log": False},
    "steep_fraction": {"dimension": "operational", "unit": "-", "tolerance": 0.08, "weight": 0.20, "log": False},
    "roughness_p95_ops": {"dimension": "operational", "unit": "m", "tolerance": 12.0, "weight": 0.20, "log": False},
    "relief_range_ops": {"dimension": "operational", "unit": "m", "tolerance": 250.0, "weight": 0.20, "log": False},
    # --- context / evidence-only (never scored in cross-body rankings) ------
    "elevation_mean": {"dimension": "context", "unit": "m", "tolerance": 0.0, "weight": 0.0, "log": False},
    "elevation_min": {"dimension": "context", "unit": "m", "tolerance": 0.0, "weight": 0.0, "log": False},
    "elevation_max": {"dimension": "context", "unit": "m", "tolerance": 0.0, "weight": 0.0, "log": False},
    "valid_fraction": {"dimension": "context", "unit": "-", "tolerance": 0.0, "weight": 0.0, "log": False},
}

SCORED_DIMENSIONS = ("geomorphology", "morphometry", "texture", "operational")
DIMENSION_LABELS = {
    "geomorphology": "Relief & hypsometry",
    "morphometry": "Slopes & landforms",
    "texture": "Multi-scale texture",
    "operational": "Trafficability proxies",
    "surface": "Surface albedo",
    "thermal": "Thermal",
    "water_ice": "Water / ice",
    "aeolian": "Aeolian",
    "environment": "Environmental context",
}

DIMENSION_DESCRIPTIONS = {
    "geomorphology": "Robust relief, hypsometric shape, and elevation-distribution distance from the DEM.",
    "morphometry": "Slope distribution, curvature, ridge/depression fractions on a common 500 m grid.",
    "texture": "Roughness at several scales, TPI, anisotropy, and variogram-derived fractal dimension.",
    "operational": "Research proxies for mobility: trafficable fraction, steep fraction, roughness and relief.",
    "surface": "Normal albedo or reflectance. Scored only when the same physical class and wavelength band exist on both bodies.",
    "thermal": "Surface temperature or thermal inertia. Air temperature is not substituted for surface temperature.",
    "water_ice": "Validated water/ice fractions. Dark pixels are never interpreted as ice.",
    "aeolian": "Dust/aeolian indicators when a comparable product exists on both bodies.",
    "environment": "Earth climate/vegetation context; not automatically an analogue of another body.",
}


@dataclass
class Feature:
    key: str
    dimension: str
    value: float | None
    unit: str
    tolerance: float
    weight: float
    method: str
    uncertainty: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TerrainProfile:
    features: dict[str, Feature]
    distributions: dict[str, dict[str, Any]]
    valid_fraction: float
    resolution_m: float
    native_resolution_m: float
    vertical_uncertainty_m: float
    elevation_mean: float | None
    elevation_min: float | None
    elevation_max: float | None
    warnings: list[str] = field(default_factory=list)


class InsufficientData(RuntimeError):
    pass


def _fill_nearest(values: np.ndarray) -> np.ndarray:
    """Fill NaNs with the nearest valid value for derivative computation only."""
    valid = np.isfinite(values)
    if valid.all():
        return values
    if not valid.any():
        raise InsufficientData("window contains no valid elevation pixels")
    indices = ndimage.distance_transform_edt(
        ~valid, return_distances=False, return_indices=True
    )
    return values[tuple(indices)]


def _safe_std(values: np.ndarray) -> float | None:
    if values.size < 2:
        return None
    return float(np.std(values, ddof=0))


def _histogram(values: np.ndarray, bins: int, value_range: tuple[float, float]) -> dict[str, Any]:
    counts, edges = np.histogram(values, bins=bins, range=value_range)
    counts = counts.astype(np.float64)
    total = counts.sum()
    if total <= 0:
        return {"bins": [], "counts": [], "edges": [], "range": list(value_range)}
    return {
        "bins": (counts / total).round(6).tolist(),
        "counts": counts.astype(int).tolist(),
        "edges": [round(float(e), 4) for e in edges],
        "range": [float(value_range[0]), float(value_range[1])],
        "n": int(total),
    }


def _slope_degrees(z: np.ndarray, spacing_m: float) -> np.ndarray:
    gy, gx = np.gradient(z, spacing_m, spacing_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def _tri(z: np.ndarray) -> np.ndarray:
    """Terrain Ruggedness Index: mean absolute difference to the 8 neighbours."""
    total = np.zeros_like(z)
    count = np.zeros_like(z)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.roll(np.roll(z, dy, axis=0), dx, axis=1)
            total += np.abs(z - shifted)
            count += 1
    return total / count


def _anisotropy(z: np.ndarray, spacing_m: float) -> float:
    gy, gx = np.gradient(z, spacing_m, spacing_m)
    jxx = ndimage.gaussian_filter(gx * gx, sigma=3.0)
    jyy = ndimage.gaussian_filter(gy * gy, sigma=3.0)
    jxy = ndimage.gaussian_filter(gx * gy, sigma=3.0)
    trace = jxx + jyy
    det = jxx * jyy - jxy * jxy
    disc = np.sqrt(np.maximum(0.0, trace * trace - 4.0 * det))
    l1 = (trace + disc) / 2.0
    l2 = (trace - disc) / 2.0
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(l1 > 1e-12, l2 / l1, 0.0)
    return float(np.clip(1.0 - np.sqrt(np.mean(ratio)), 0.0, 1.0))


def _fractal_dimension(z: np.ndarray, spacing_m: float) -> float | None:
    lags = [1, 2, 3, 4, 6, 8]
    gammas: list[float] = []
    used: list[int] = []
    for lag in lags:
        if lag >= z.shape[0] - 2 or lag >= z.shape[1] - 2:
            continue
        dx = z[:, lag:] - z[:, :-lag]
        dy = z[lag:, :] - z[:-lag, :]
        vals = np.concatenate([dx[np.isfinite(dx)], dy[np.isfinite(dy)]])
        if vals.size < 50:
            continue
        gammas.append(float(np.mean(vals * vals)))
        used.append(lag)
    if len(used) < 3 or not all(g > 0 for g in gammas):
        return None
    slope = float(np.polyfit(np.log(np.array(used) * spacing_m), np.log(gammas), 1)[0])
    hurst = float(np.clip(slope / 2.0, 0.0, 1.0))
    return float(np.clip(3.0 - hurst, 2.0, 3.0))


def _entropy(values: np.ndarray) -> float | None:
    if values.size < 10:
        return None
    lo, hi = np.percentile(values, [1, 99])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    counts, _ = np.histogram(values, bins=32, range=(lo, hi))
    p = counts.astype(np.float64)
    p = p[p > 0] / p.sum()
    if p.size < 2:
        return None
    return float(-(p * np.log(p)).sum() / np.log(32))


def extract_terrain_profile(
    window: GriddedWindow,
    *,
    native_resolution_m: float,
    vertical_uncertainty_m: float,
    product_method: str,
) -> TerrainProfile:
    """Compute the versioned terrain feature set from a local metric grid."""
    values = np.asarray(window.values, dtype=np.float64)
    valid = np.isfinite(values)
    valid_fraction = float(valid.mean())
    if valid_fraction < 0.20:
        raise InsufficientData(
            f"valid elevation coverage {valid_fraction:.1%} is below the 20% scientific minimum"
        )
    warnings: list[str] = []
    if valid_fraction < 0.60:
        warnings.append("Elevation coverage below 60%; features are computed on the valid subset.")

    z = _fill_nearest(values)
    res = float(window.resolution_m)
    size_m = res * window.width
    z_valid = z[valid]

    p05, p10, p50, p90, p95 = np.percentile(z_valid, [5, 10, 50, 90, 95])
    relief_range = float(p95 - p05)
    elevation_std = _safe_std(z_valid) or 0.0
    skew = float(stats.skew(z_valid, bias=False)) if z_valid.size > 3 else None
    kurt = float(stats.kurtosis(z_valid, bias=False)) if z_valid.size > 3 else None
    # Hypsometric integral: area under the hypsometric curve, (mean - min) / (max - min).
    z_min, z_max = float(np.min(z_valid)), float(np.max(z_valid))
    hypso = float((np.mean(z_valid) - z_min) / (z_max - z_min)) if z_max - z_min > 1e-9 else 0.5
    entropy = _entropy(z_valid)
    relief_ratio = relief_range / max(1.0, size_m)

    slope = _slope_degrees(z, res)
    slope_valid = slope[valid]
    slope_mean = float(np.mean(slope_valid))
    slope_std = float(np.std(slope_valid))
    slope_p95 = float(np.percentile(slope_valid, 95))
    slope_gt_15 = float(np.mean(slope_valid > 15.0))
    slope_gt_25 = float(np.mean(slope_valid > 25.0))

    gy, gx = np.gradient(z, res, res)
    gyy, gyx = np.gradient(gy, res, res)
    gxy, gxx = np.gradient(gx, res, res)
    curvature = ((gxx + gyy) / 2.0) * res * res  # dimensionless at the analysis cell scale
    curvature_valid = curvature[valid]
    curvature_std = float(np.std(curvature_valid))
    curvature_mean_abs = float(np.mean(np.abs(curvature_valid)))

    sigma_small = max(1.0, round(1000.0 / res))
    residual_small = z - ndimage.gaussian_filter(z, sigma=sigma_small, mode="nearest")
    residual_large = z - ndimage.gaussian_filter(z, sigma=max(sigma_small * 4, 4), mode="nearest")
    rough_small = float(np.std(residual_small[valid]))
    rough_large = float(np.std(residual_large[valid]))
    roughness_rms = rough_small
    roughness_p95 = float(np.percentile(np.abs(residual_small[valid]), 95))
    roughness_scale_ratio = float(np.clip(rough_small / max(rough_large, 1e-6), 0.05, 20.0))

    tri = _tri(z)[valid]
    tri_mean = float(np.mean(tri))

    tpi_window = max(3, int(round(2500.0 / res)) | 1)
    tpi = z - ndimage.uniform_filter(z, size=tpi_window, mode="nearest")
    tpi_std = float(np.std(tpi[valid]))
    ridge_fraction = float(np.mean(tpi[valid] > 25.0))
    depression_fraction = float(np.mean(tpi[valid] < -25.0))

    anisotropy = _anisotropy(z, res)
    fractal = _fractal_dimension(z, res)
    if fractal is None:
        warnings.append("Fractal dimension unavailable: insufficient valid variogram pairs.")
    trafficable = float(np.mean((slope_valid < 15.0) & (np.abs(residual_small[valid]) < 10.0)))

    # Distribution features are persisted as histograms and scored by 1-Wasserstein distance.
    # Elevation is median-centred before histogramming: absolute elevation is not comparable
    # between bodies with different datums, but the shape of the local distribution is.
    elevation_center = float(np.median(z_valid))
    elevation_centered = z_valid - elevation_center
    elevation_hist = _histogram(
        elevation_centered,
        64,
        (float(np.min(elevation_centered)), float(np.max(elevation_centered))),
    )
    slope_hist = _histogram(slope_valid, 60, (0.0, 60.0))
    distributions = {"elevation": elevation_hist, "slope": slope_hist}

    raw: dict[str, tuple[float | None, float | None, str, dict[str, Any]]] = {
        "relief_range": (relief_range, vertical_uncertainty_m * 2.0, "p95 - p05 of valid elevation", {}),
        "elevation_std": (elevation_std, vertical_uncertainty_m, "standard deviation of valid elevation", {}),
        "elevation_p10_p90": (float(p90 - p10), vertical_uncertainty_m * 2.0, "p90 - p10 of valid elevation", {}),
        "elevation_skew": (skew, None, "Fisher-Pearson skewness of valid elevation", {}),
        "elevation_kurtosis": (kurt, None, "excess kurtosis of valid elevation", {}),
        "hypsometric_integral": (hypso, None, "(mean - min) / (max - min) of valid elevation", {}),
        "elevation_entropy": (entropy, None, "Shannon entropy of 32-bin elevation histogram / log(32)", {}),
        "relief_ratio": (relief_ratio, None, "relief_range / window width", {}),
        "elevation_distribution": (
            elevation_center,
            None,
            "1-Wasserstein distance of median-centred elevation quantiles",
            {"distribution": elevation_hist, "center_m": elevation_center, "centered": True},
        ),
        "slope_mean": (slope_mean, float(np.degrees(np.arctan(np.sqrt(2.0) * vertical_uncertainty_m / res))), "mean Horn gradient magnitude, common grid", {}),
        "slope_p95": (slope_p95, None, "95th percentile slope, common grid", {}),
        "slope_std": (slope_std, None, "standard deviation of slope", {}),
        "slope_gt_15": (slope_gt_15, None, "fraction of valid cells with slope > 15 deg", {}),
        "slope_gt_25": (slope_gt_25, None, "fraction of valid cells with slope > 25 deg", {}),
        "curvature_std": (curvature_std, None, "std of dimensionless Laplacian curvature", {}),
        "curvature_mean_abs": (curvature_mean_abs, None, "mean absolute dimensionless curvature", {}),
        "ridge_fraction": (ridge_fraction, None, "fraction with 2.5 km TPI > +25 m", {}),
        "depression_fraction": (depression_fraction, None, "fraction with 2.5 km TPI < -25 m", {}),
        "slope_distribution": (slope_p95, None, "1-Wasserstein distance of slope quantiles", {"distribution": slope_hist}),
        "roughness_rms": (roughness_rms, float(np.sqrt(2.0) * vertical_uncertainty_m), "std of 1 km high-pass residual", {}),
        "roughness_p95": (roughness_p95, None, "95th percentile |1 km high-pass residual|", {}),
        "tri_mean": (tri_mean, None, "mean terrain ruggedness index (8-neighbour)", {}),
        "tpi_std": (tpi_std, None, "std of 2.5 km topographic position index", {}),
        "anisotropy": (anisotropy, None, "1 - sqrt(lambda2/lambda1) of gradient structure tensor", {}),
        "fractal_dimension": (fractal, None, "3 - H from variogram power law", {}),
        "roughness_scale_ratio": (roughness_scale_ratio, None, "roughness(1 km) / roughness(4 km)", {}),
        "trafficable_fraction": (trafficable, None, "fraction slope < 15 deg and |1 km residual| < 10 m", {}),
        "steep_fraction": (slope_gt_25, None, "fraction slope > 25 deg", {}),
        "roughness_p95_ops": (roughness_p95, None, "operational roughness proxy (duplicate)", {}),
        "relief_range_ops": (relief_range, None, "operational relief proxy (duplicate)", {}),
        "elevation_mean": (float(np.mean(z_valid)), None, "mean absolute elevation; context only", {}),
        "elevation_min": (float(np.min(z_valid)), None, "minimum elevation; context only", {}),
        "elevation_max": (float(np.max(z_valid)), None, "maximum elevation; context only", {}),
        "valid_fraction": (valid_fraction, None, "fraction of analysis cells with valid source data", {}),
    }

    features: dict[str, Feature] = {}
    for key, (value, uncertainty, method, metadata) in raw.items():
        spec = FEATURE_SPECS[key]
        features[key] = Feature(
            key=key,
            dimension=spec["dimension"],
            value=None if value is None or not np.isfinite(value) else float(value),
            unit=spec["unit"],
            tolerance=float(spec["tolerance"]),
            weight=float(spec["weight"]),
            method=product_method + " | " + method,
            uncertainty=uncertainty,
            metadata=metadata,
        )

    return TerrainProfile(
        features=features,
        distributions=distributions,
        valid_fraction=valid_fraction,
        resolution_m=res,
        native_resolution_m=native_resolution_m,
        vertical_uncertainty_m=vertical_uncertainty_m,
        elevation_mean=float(np.mean(z_valid)),
        elevation_min=float(np.min(z_valid)),
        elevation_max=float(np.max(z_valid)),
        warnings=warnings,
    )


def dimension_weights() -> dict[str, dict[str, float]]:
    """Declared within-dimension feature weights, persisted in the run manifest."""
    out: dict[str, dict[str, float]] = {d: {} for d in SCORED_DIMENSIONS}
    for key, spec in FEATURE_SPECS.items():
        if spec["dimension"] in out and spec["weight"] > 0:
            out[spec["dimension"]][key] = float(spec["weight"])
    return out


def features_by_dimension(profile: TerrainProfile) -> dict[str, dict[str, Feature]]:
    out: dict[str, dict[str, Feature]] = {}
    for key, feature in profile.features.items():
        if feature.weight > 0 and feature.dimension in SCORED_DIMENSIONS:
            out.setdefault(feature.dimension, {})[key] = feature
    return out
