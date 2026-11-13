"""Transparent multi-method similarity.

Primary ranking: weighted MCDA with a Gaussian kernel on *physical* feature deltas. Tolerances
are declared per feature, stored in the run manifest, and documented. Secondary methods operate
on pooled robust z-scores and are reported separately so method sensitivity is visible.

Similarity is not a probability. All scores are clipped to [0, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .features import (
    DIMENSION_LABELS,
    FEATURE_SPECS,
    SCORED_DIMENSIONS,
    Feature,
    TerrainProfile,
)

EPS = 1e-9


@dataclass
class DimensionResult:
    dimension: str
    score: float | None
    coverage: float
    weight_configured: float
    weight_used: float
    features_used: int
    features_missing: int
    methods: dict[str, float | None]
    detail: list[dict[str, Any]]
    unavailable_reason: str | None = None


@dataclass
class CandidateScore:
    candidate_id: str
    overall_score: float
    confidence: float
    confidence_band: str
    evidence_coverage: float
    dimension_scores: dict[str, Any]
    method_scores: dict[str, float | None]
    missing_dimensions: list[dict[str, Any]]
    strongest_dimensions: list[dict[str, Any]]
    weakest_dimensions: list[dict[str, Any]]
    limitations: list[str]
    feature_deltas: list[dict[str, Any]] = field(default_factory=list)


def _gaussian_kernel(delta: float, tolerance: float) -> float:
    if tolerance <= 0:
        return 1.0 if abs(delta) < EPS else 0.0
    return float(math.exp(-0.5 * (abs(delta) / tolerance) ** 2))


def _quantiles_from_hist(hist: dict[str, Any], n: int = 101) -> np.ndarray | None:
    bins = np.asarray(hist.get("bins", []), dtype=float)
    edges = np.asarray(hist.get("edges", []), dtype=float)
    if bins.size == 0 or edges.size != bins.size + 1 or bins.sum() <= 0:
        return None
    mids = (edges[:-1] + edges[1:]) / 2.0
    cdf = np.cumsum(bins)
    cdf /= cdf[-1]
    p = np.linspace(0.0, 1.0, n)
    return np.interp(p, cdf, mids)


def wasserstein_similarity(
    hist_ref: dict[str, Any] | None,
    hist_cand: dict[str, Any] | None,
    tolerance: float,
) -> float | None:
    if not hist_ref or not hist_cand:
        return None
    q1 = _quantiles_from_hist(hist_ref)
    q2 = _quantiles_from_hist(hist_cand)
    if q1 is None or q2 is None:
        return None
    distance = float(np.mean(np.abs(q1 - q2)))
    return float(math.exp(-distance / max(tolerance, EPS)))


def _percentile_ranks(
    reference: TerrainProfile,
    candidates: list[TerrainProfile],
    keys: list[str],
) -> dict[str, dict[str, float | None]]:
    ranks: dict[str, dict[str, float | None]] = {
        "reference": {},
        **{c.subject_key: {} for c in candidates},  # type: ignore[attr-defined]
    }
    for key in keys:
        pool = []
        for profile in [reference] + candidates:
            f = profile.features.get(key)
            if f is not None and f.value is not None:
                label = (
                    "reference"
                    if profile is reference
                    else getattr(profile, "subject_key", "candidate")
                )
                pool.append((label, f.value))
        if len(pool) < 4:
            continue
        pool.sort(key=lambda item: item[1])
        denom = max(1, len(pool) - 1)
        for rank, (label, _value) in enumerate(pool):
            ranks[label][key] = rank / denom
    return ranks


def _mahalanobis_similarity(delta: np.ndarray, cov_inv: np.ndarray, dof: int) -> float:
    d2 = float(delta @ cov_inv @ delta)
    d2 = max(0.0, d2)
    return float(math.exp(-0.5 * math.sqrt(d2 / max(1, dof))))


def _is_usable(feature: Feature | None) -> bool:
    return feature is not None and feature.value is not None and math.isfinite(float(feature.value))


def score_candidates(
    reference: TerrainProfile,
    candidate_profiles: dict[str, TerrainProfile],
    *,
    dimension_weights: dict[str, float],
    min_feature_coverage: float = 0.5,
    methods: list[str] | None = None,
    missing_data_policy: str = "penalize",
) -> list[CandidateScore]:
    """Score refined candidates against one reference. Candidate IDs are opaque strings."""
    methods = methods or [
        "mcda",
        "weighted_euclidean",
        "standardized_euclidean",
        "cosine",
        "wasserstein",
        "percentile",
    ]
    candidates = list(candidate_profiles.values())
    for cand in candidates:
        cand.subject_key = candidate_profiles_key(candidate_profiles, cand)  # type: ignore[attr-defined]

    all_keys = [
        key
        for key, spec in FEATURE_SPECS.items()
        if spec["weight"] > 0 and spec["dimension"] in SCORED_DIMENSIONS
    ]
    percentiles = _percentile_ranks(reference, candidates, all_keys)

    configured_total = sum(
        max(0.0, dimension_weights.get(d, 0.0)) for d in SCORED_DIMENSIONS
    )
    results: list[CandidateScore] = []

    for cand in candidates:
        dim_results: dict[str, DimensionResult] = {}
        feature_deltas: list[dict[str, Any]] = []

        for dim in SCORED_DIMENSIONS:
            keys = [
                k
                for k, spec in FEATURE_SPECS.items()
                if spec["dimension"] == dim and spec["weight"] > 0
            ]
            weight_total = sum(FEATURE_SPECS[k]["weight"] for k in keys)
            valid_keys = [
                k
                for k in keys
                if _is_usable(reference.features.get(k)) and _is_usable(cand.features.get(k))
            ]
            coverage = (
                sum(FEATURE_SPECS[k]["weight"] for k in valid_keys) / weight_total
                if weight_total > 0
                else 0.0
            )
            configured_weight = max(0.0, float(dimension_weights.get(dim, 0.0)))

            if coverage < min_feature_coverage or configured_weight <= 0:
                reason = (
                    f"Feature coverage {coverage:.0%} is below the configured minimum "
                    f"{min_feature_coverage:.0%}."
                    if coverage < min_feature_coverage
                    else "Dimension weight is zero in this configuration."
                )
                dim_results[dim] = DimensionResult(
                    dimension=dim,
                    score=None,
                    coverage=coverage,
                    weight_configured=configured_weight,
                    weight_used=0.0,
                    features_used=len(valid_keys),
                    features_missing=len(keys) - len(valid_keys),
                    methods={},
                    detail=[],
                    unavailable_reason=reason,
                )
                continue

            if missing_data_policy == "exclude_dimension" and coverage < 1.0 - 1e-9:
                dim_results[dim] = DimensionResult(
                    dimension=dim,
                    score=None,
                    coverage=coverage,
                    weight_configured=configured_weight,
                    weight_used=0.0,
                    features_used=len(valid_keys),
                    features_missing=len(keys) - len(valid_keys),
                    methods={},
                    detail=[],
                    unavailable_reason=(
                        "Missing-data policy 'exclude_dimension' requires complete feature "
                        f"coverage; this dimension has {coverage:.0%}."
                    ),
                )
                continue

            mcda_pairs: list[tuple[float, float, float]] = []
            delta_details: list[dict[str, Any]] = []
            for key in valid_keys:
                ref_f = reference.features[key]
                cand_f = cand.features[key]
                assert ref_f.value is not None and cand_f.value is not None
                if FEATURE_SPECS[key].get("distribution"):
                    hist_ref = ref_f.metadata.get("distribution")
                    hist_cand = cand_f.metadata.get("distribution")
                    sim = wasserstein_similarity(hist_ref, hist_cand, ref_f.tolerance)
                    delta = None
                else:
                    delta = float(cand_f.value - ref_f.value)
                    sim = _gaussian_kernel(delta, ref_f.tolerance)
                if sim is None:
                    continue
                weight = FEATURE_SPECS[key]["weight"]
                mcda_pairs.append((weight, sim, ref_f.tolerance))
                delta_details.append(
                    {
                        "key": key,
                        "reference": ref_f.value,
                        "candidate": cand_f.value,
                        "delta": delta,
                        "unit": ref_f.unit,
                        "tolerance": ref_f.tolerance,
                        "similarity": round(sim, 6),
                        "weight": weight,
                    }
                )
                feature_deltas.append(
                    {
                        "dimension": dim,
                        "key": key,
                        "reference": ref_f.value,
                        "candidate": cand_f.value,
                        "delta": delta,
                        "unit": ref_f.unit,
                        "similarity": round(sim, 6),
                        "tolerance": ref_f.tolerance,
                    }
                )

            weight_sum = sum(w for w, _s, _t in mcda_pairs)
            if weight_sum <= 0:
                dim_results[dim] = DimensionResult(
                    dimension=dim,
                    score=None,
                    coverage=0.0,
                    weight_configured=configured_weight,
                    weight_used=0.0,
                    features_used=0,
                    features_missing=len(keys),
                    methods={},
                    detail=[],
                    unavailable_reason="No comparable feature pair produced a valid similarity.",
                )
                continue

            mcda = sum(w * s for w, s, _t in mcda_pairs) / weight_sum
            mcda *= math.sqrt(coverage)
            dim_methods: dict[str, float | None] = {"mcda": mcda}

            # Secondary methods use the same physically declared tolerances as the primary
            # kernel. Standardizing against a tiny candidate pool would turn millimetre-scale
            # pool variance into an artificial disagreement, so it is deliberately avoided.
            keys_norm = [k for k in valid_keys if not FEATURE_SPECS[k].get("distribution")]
            norm_deltas: list[float] = []
            norm_weights: list[float] = []
            for key in keys_norm:
                ref_f = reference.features[key]
                cand_f = cand.features[key]
                assert ref_f.value is not None and cand_f.value is not None
                tolerance = ref_f.tolerance if ref_f.tolerance > 0 else 1.0
                norm_deltas.append(float(cand_f.value - ref_f.value) / tolerance)
                norm_weights.append(FEATURE_SPECS[key]["weight"])
            if norm_deltas:
                nd = np.asarray(norm_deltas, dtype=float)
                wts = np.asarray(norm_weights, dtype=float)
                weighted_ms = float(np.sum(wts * nd**2) / np.sum(wts))
                dim_methods["weighted_euclidean"] = float(
                    math.exp(-0.5 * weighted_ms) * math.sqrt(coverage)
                )
                dim_methods["standardized_euclidean"] = float(
                    math.exp(-0.5 * float(np.mean(nd**2))) * math.sqrt(coverage)
                )
                # Cosine between the per-feature match profile and the all-match profile.
                # It equals 1 only when every feature matches equally well and penalizes
                # an uneven match even when the mean match is high.
                match_profile = np.exp(-0.5 * nd**2)
                denom = math.sqrt(len(match_profile) * float(np.sum(match_profile**2)))
                cosine = float(np.sum(match_profile) / denom) if denom > 0 else 0.0
                dim_methods["cosine"] = float(np.clip(cosine, 0.0, 1.0) * math.sqrt(coverage))

                mahalanobis = None
                dim_rows = []
                for row_cand in candidates:
                    row = []
                    for key in keys_norm:
                        ref_f = reference.features[key]
                        cand_f = row_cand.features[key]
                        if ref_f.value is None or cand_f.value is None:
                            break
                        tolerance = ref_f.tolerance if ref_f.tolerance > 0 else 1.0
                        row.append(float(cand_f.value - ref_f.value) / tolerance)
                    if len(row) == len(keys_norm):
                        dim_rows.append(row)
                if len(dim_rows) >= max(5, len(keys_norm) + 2):
                    dim_cov = np.atleast_2d(np.cov(np.asarray(dim_rows), rowvar=False))
                    shrink = 0.25
                    dim_cov = (1 - shrink) * dim_cov + shrink * np.eye(dim_cov.shape[0]) * max(
                        np.trace(dim_cov) / max(1, dim_cov.shape[0]), 1e-6
                    )
                    dim_cov_inv = np.linalg.pinv(dim_cov)
                    mahalanobis = float(
                        _mahalanobis_similarity(nd, dim_cov_inv, len(nd)) * math.sqrt(coverage)
                    )
                dim_methods["mahalanobis"] = mahalanobis

                keys_z = keys_norm
                p_diff = [
                    abs(
                        float(percentiles["reference"].get(k, 0.5))
                        - float(percentiles[cand.subject_key].get(k, 0.5))  # type: ignore[attr-defined]
                    )
                    for k in keys_z
                    if percentiles["reference"].get(k) is not None
                    and percentiles[cand.subject_key].get(k) is not None  # type: ignore[attr-defined]
                ]
                dim_methods["percentile"] = (
                    float(max(0.0, 1.0 - np.mean(p_diff)) * math.sqrt(coverage))
                    if p_diff
                    else None
                )
            else:
                for m in ("weighted_euclidean", "standardized_euclidean", "cosine", "mahalanobis", "percentile"):
                    dim_methods[m] = None

            dist_pairs = []
            for key in valid_keys:
                if not FEATURE_SPECS[key].get("distribution"):
                    continue
                ref_f = reference.features[key]
                cand_f = cand.features[key]
                sim = wasserstein_similarity(
                    ref_f.metadata.get("distribution"),
                    cand_f.metadata.get("distribution"),
                    ref_f.tolerance,
                )
                if sim is not None:
                    dist_pairs.append((FEATURE_SPECS[key]["weight"], sim))
            if dist_pairs:
                wsum = sum(w for w, _s in dist_pairs)
                dim_methods["wasserstein"] = (
                    sum(w * s for w, s in dist_pairs) / wsum * math.sqrt(coverage)
                )
            else:
                dim_methods["wasserstein"] = None

            dim_results[dim] = DimensionResult(
                dimension=dim,
                score=float(np.clip(mcda, 0.0, 1.0)),
                coverage=float(coverage),
                weight_configured=configured_weight,
                weight_used=configured_weight if configured_weight > 0 else 0.0,
                features_used=len(valid_keys),
                features_missing=len(keys) - len(valid_keys),
                methods={m: (None if v is None else float(np.clip(v, 0.0, 1.0))) for m, v in dim_methods.items()},
                detail=delta_details,
            )

        available = {d: r for d, r in dim_results.items() if r.score is not None}
        if not available:
            overall = 0.0
            method_scores = {m: None for m in methods}
        else:
            used_total = sum(r.weight_configured for r in available.values())
            overall = (
                sum(r.weight_configured * float(r.score) for r in available.values()) / used_total
                if used_total > 0
                else 0.0
            )
            method_scores = {}
            for method in methods:
                pairs = [
                    (r.weight_configured, r.methods.get(method))
                    for r in available.values()
                    if r.methods.get(method) is not None
                ]
                if pairs:
                    wsum = sum(w for w, _v in pairs)
                    method_scores[method] = (
                        sum(w * float(v) for w, v in pairs if v is not None) / wsum
                        if wsum > 0
                        else None
                    )
                else:
                    method_scores[method] = None

        available_cfg = sum(r.weight_configured for r in available.values())
        dimension_coverage = available_cfg / configured_total if configured_total > 0 else 0.0
        feature_cov = (
            sum(r.coverage * r.weight_configured for r in available.values()) / available_cfg
            if available_cfg > 0
            else 0.0
        )
        valid_fraction = min(reference.valid_fraction, cand.valid_fraction)
        native_max = max(reference.native_resolution_m, cand.native_resolution_m)
        resolution_factor = float(
            np.clip(math.sqrt(reference.resolution_m / max(native_max, 1.0)), 0.2, 1.0)
        )
        method_values = [v for v in method_scores.values() if v is not None]
        if len(method_values) >= 2:
            agreement = float(np.clip(1.0 - np.std(method_values) / 0.25, 0.0, 1.0))
        else:
            agreement = 0.5
        slope_unc = math.degrees(
            math.atan(math.sqrt(2.0) * max(reference.vertical_uncertainty_m, cand.vertical_uncertainty_m)
                      / max(cand.resolution_m, 1.0))
        )
        uncertainty_penalty = float(np.clip(slope_unc / 45.0, 0.0, 0.12))
        confidence = float(
            np.clip(
                0.25 * dimension_coverage
                + 0.20 * feature_cov
                + 0.15 * valid_fraction
                + 0.15 * resolution_factor
                + 0.25 * agreement
                - uncertainty_penalty,
                0.0,
                1.0,
            )
        )
        # Evidence completeness cannot mask method disagreement. If independently specified
        # aggregations disagree strongly, the result cannot be labelled high-confidence.
        if agreement < 0.30:
            confidence = min(confidence, 0.55)
        elif agreement < 0.45:
            confidence = min(confidence, 0.70)
        band = (
            "high" if confidence >= 0.75 else "moderate" if confidence >= 0.55 else "low" if confidence >= 0.35 else "very_low"
        )

        missing = [
            {
                "dimension": dim,
                "label": DIMENSION_LABELS.get(dim, dim),
                "reason": r.unavailable_reason,
                "coverage": r.coverage,
            }
            for dim, r in dim_results.items()
            if r.score is None
        ]
        ranked_dims = sorted(
            available.values(), key=lambda r: r.score if r.score is not None else 0.0, reverse=True
        )
        strongest = [
            {"dimension": r.dimension, "label": DIMENSION_LABELS.get(r.dimension, r.dimension), "score": round(float(r.score or 0), 4)}
            for r in ranked_dims[:2]
        ]
        weakest = [
            {"dimension": r.dimension, "label": DIMENSION_LABELS.get(r.dimension, r.dimension), "score": round(float(r.score or 0), 4)}
            for r in ranked_dims[-2:]
        ]
        limitations = []
        if missing:
            limitations.append(
                f"{len(missing)} configured dimension(s) have no comparable cross-body evidence; "
                "weights were redistributed, not imputed."
            )
        if resolution_factor < 0.6:
            limitations.append(
                "Source resolution is coarse relative to the 500 m analysis grid; 500 m texture is partly interpolated."
            )
        if valid_fraction < 0.8:
            limitations.append("Part of the footprint has no valid source data.")
        if agreement < 0.6:
            limitations.append("Similarity methods disagree; inspect the method breakdown before interpreting the rank.")
        if reference.vertical_uncertainty_m > 5 or cand.vertical_uncertainty_m > 5:
            limitations.append("Vertical uncertainty is non-trivial relative to 500 m slope estimation.")
        limitations.append("Similarity is a heuristic score, not a probability or a landing-safety assessment.")

        results.append(
            CandidateScore(
                candidate_id=cand.subject_key,  # type: ignore[attr-defined]
                overall_score=float(np.clip(overall, 0.0, 1.0)),
                confidence=confidence,
                confidence_band=band,
                evidence_coverage=float(dimension_coverage),
                dimension_scores={
                    d: {
                        "dimension": d,
                        "label": DIMENSION_LABELS.get(d, d),
                        "score": None if r.score is None else round(float(r.score), 6),
                        "coverage": round(r.coverage, 6),
                        "weight_configured": round(r.weight_configured, 6),
                        "weight_used": round(r.weight_used, 6),
                        "features_used": r.features_used,
                        "features_missing": r.features_missing,
                        "methods": {k: None if v is None else round(float(v), 6) for k, v in r.methods.items()},
                        "features": r.detail,
                        "unavailable_reason": r.unavailable_reason,
                    }
                    for d, r in dim_results.items()
                },
                method_scores={
                    k: None if v is None else round(float(v), 6) for k, v in method_scores.items()
                },
                missing_dimensions=missing,
                strongest_dimensions=strongest,
                weakest_dimensions=weakest,
                limitations=limitations,
                feature_deltas=feature_deltas,
            )
        )

    results.sort(key=lambda r: (-r.overall_score, -r.confidence))
    return results


def candidate_profiles_key(profiles: dict[str, TerrainProfile], profile: TerrainProfile) -> str:
    for key, value in profiles.items():
        if value is profile:
            return key
    return "candidate"


def method_summary(results: list[CandidateScore], methods: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {"methods": {}, "agreement": {}}
    for method in methods:
        vals = [r.method_scores.get(method) for r in results if r.method_scores.get(method) is not None]
        summary["methods"][method] = {
            "mean": round(float(np.mean(vals)), 6) if vals else None,
            "std": round(float(np.std(vals)), 6) if vals else None,
            "n": len(vals),
        }
    vals = [
        np.std([v for v in r.method_scores.values() if v is not None])
        for r in results
        if len([v for v in r.method_scores.values() if v is not None]) >= 2
    ]
    summary["agreement"]["mean_method_std"] = round(float(np.mean(vals)), 6) if vals else None
    summary["agreement"]["interpretation"] = (
        "Low spread means independently specified methods broadly agree; a high spread is a warning, not hidden."
    )
    return summary
