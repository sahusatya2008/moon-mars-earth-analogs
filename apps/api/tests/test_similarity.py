"""Similarity kernel, missing-data policy, and method reporting."""

from __future__ import annotations

import math

import numpy as np
import pytest

from terrax.engine.features import SCORED_DIMENSIONS, extract_terrain_profile
from terrax.engine.similarity import (
    _gaussian_kernel,
    method_summary,
    score_candidates,
    wasserstein_similarity,
)

WEIGHTS = {"geomorphology": 0.35, "morphometry": 0.30, "texture": 0.20, "operational": 0.15}


def _profile(values, make_window):
    window = make_window(values)
    return extract_terrain_profile(
        window,
        native_resolution_m=500.0,
        vertical_uncertainty_m=2.0,
        product_method="fixture",
    )


def test_identical_profile_scores_one(make_window):
    y, x = np.mgrid[0:64, 0:64]
    values = 100.0 * np.sin(x / 6.0) + 40.0 * np.cos(y / 9.0)
    reference = _profile(values, make_window)
    candidate = _profile(values.copy(), make_window)

    result = score_candidates(
        reference, {"candidate-a": candidate}, dimension_weights=WEIGHTS
    )[0]
    assert result.overall_score == pytest.approx(1.0, abs=1e-6)
    for dim in SCORED_DIMENSIONS:
        assert result.dimension_scores[dim]["score"] == pytest.approx(1.0, abs=1e-6)
    assert result.evidence_coverage == pytest.approx(1.0)
    assert result.confidence > 0.5
    assert result.missing_dimensions == []


def test_absolute_elevation_offset_does_not_affect_similarity(make_window):
    y, x = np.mgrid[0:64, 0:64]
    shape = 80.0 * np.sin(x / 7.0) + 30.0 * np.cos(y / 5.0)
    reference = _profile(shape + 1000.0, make_window)
    candidate = _profile(shape - 2500.0, make_window)

    result = score_candidates(
        reference, {"candidate-a": candidate}, dimension_weights=WEIGHTS
    )[0]
    assert result.overall_score == pytest.approx(1.0, abs=1e-6)
    assert result.dimension_scores["geomorphology"]["methods"]["wasserstein"] == pytest.approx(
        1.0, abs=1e-6
    )


def test_rougher_candidate_scores_below_identical(make_window):
    y, x = np.mgrid[0:64, 0:64]
    smooth = 100.0 * np.sin(x / 12.0)
    rough = smooth + 25.0 * np.sin(x * 1.7) * np.cos(y * 1.3)
    reference = _profile(smooth, make_window)
    identical = _profile(smooth.copy(), make_window)
    disturbed = _profile(rough, make_window)

    scores = score_candidates(
        reference,
        {"identical": identical, "disturbed": disturbed},
        dimension_weights=WEIGHTS,
    )
    by_id = {s.candidate_id: s for s in scores}
    assert by_id["disturbed"].overall_score < by_id["identical"].overall_score
    assert by_id["disturbed"].dimension_scores["texture"]["score"] < 0.9


def test_missing_dimension_is_excluded_not_imputed(make_window):
    y, x = np.mgrid[0:64, 0:64]
    reference = _profile(x * 20.0 + y * 5.0, make_window)
    candidate = _profile(x * 20.0 + y * 5.0, make_window)

    # Drop all but one geomorphology feature from the candidate.
    kept = 0
    for _key, feature in candidate.features.items():
        if feature.dimension == "geomorphology":
            kept += 1
            if kept > 1:
                feature.value = None

    result = score_candidates(
        reference,
        {"candidate-a": candidate},
        dimension_weights=WEIGHTS,
        min_feature_coverage=0.5,
    )[0]
    assert result.dimension_scores["geomorphology"]["score"] is None
    assert result.dimension_scores["geomorphology"]["coverage"] < 0.5
    assert any(m["dimension"] == "geomorphology" for m in result.missing_dimensions)
    assert result.evidence_coverage < 1.0
    # Available dimensions still score independently.
    assert result.dimension_scores["morphometry"]["score"] is not None
    assert result.evidence_coverage == pytest.approx(0.65, abs=1e-6)


def test_exclude_dimension_policy_requires_complete_coverage(make_window):
    y, x = np.mgrid[0:64, 0:64]
    reference = _profile(x * 20.0 + y * 5.0, make_window)
    candidate = _profile(x * 20.0 + y * 5.0, make_window)
    candidate.features["elevation_skew"].value = None

    penalized = score_candidates(
        reference, {"candidate-a": candidate}, dimension_weights=WEIGHTS
    )[0]
    excluded = score_candidates(
        reference,
        {"candidate-a": candidate},
        dimension_weights=WEIGHTS,
        missing_data_policy="exclude_dimension",
    )[0]

    assert penalized.dimension_scores["geomorphology"]["score"] is not None
    assert penalized.dimension_scores["geomorphology"]["coverage"] < 1.0
    assert excluded.dimension_scores["geomorphology"]["score"] is None
    assert "exclude_dimension" in (
        excluded.dimension_scores["geomorphology"]["unavailable_reason"] or ""
    )
    assert excluded.evidence_coverage < penalized.evidence_coverage


def test_gaussian_kernel_matches_declared_scale():
    assert _gaussian_kernel(0.0, 4.0) == pytest.approx(1.0)
    assert _gaussian_kernel(4.0, 4.0) == pytest.approx(math.exp(-0.5))
    assert _gaussian_kernel(8.0, 4.0) == pytest.approx(math.exp(-2.0))
    assert _gaussian_kernel(0.5, 0.0) == 0.0


def test_wasserstein_identical_and_shifted_histograms():
    hist = {"bins": [0.2, 0.4, 0.4], "edges": [0.0, 1.0, 2.0, 3.0]}
    shifted = {"bins": [0.0, 0.2, 0.8], "edges": [0.0, 1.0, 2.0, 3.0]}
    assert wasserstein_similarity(hist, hist, 1.0) == pytest.approx(1.0)
    assert wasserstein_similarity(hist, shifted, 1.0) < 0.8


def test_method_summary_reports_each_method(make_window):
    y, x = np.mgrid[0:64, 0:64]
    reference = _profile(np.sin(x / 5.0) + np.cos(y / 7.0), make_window)
    candidates = {
        "a": _profile(np.sin(x / 5.0) + np.cos(y / 7.0), make_window),
        "b": _profile(1.4 * np.sin(x / 5.0) + 0.6 * np.cos(y / 7.0), make_window),
    }
    methods = ["mcda", "weighted_euclidean", "standardized_euclidean", "cosine", "wasserstein"]
    results = score_candidates(
        reference, candidates, dimension_weights=WEIGHTS, methods=methods
    )
    summary = method_summary(results, methods)
    for method in methods:
        assert summary["methods"][method]["n"] == 2
        assert summary["methods"][method]["mean"] is not None
    assert summary["agreement"]["mean_method_std"] is not None


def test_scores_are_bounded_and_deterministic(make_window):
    y, x = np.mgrid[0:64, 0:64]
    reference = _profile(x * 30.0 + np.sin(y / 3.0) * 20.0, make_window)
    candidates = {
        "a": _profile(x * 30.0 + np.sin(y / 3.0) * 20.0 + 5.0, make_window),
        "b": _profile(x * 10.0 + 60.0 * np.cos(y / 2.0), make_window),
    }
    first = score_candidates(reference, candidates, dimension_weights=WEIGHTS)
    second = score_candidates(reference, candidates, dimension_weights=WEIGHTS)
    assert [s.candidate_id for s in first] == [s.candidate_id for s in second]
    assert [s.overall_score for s in first] == [s.overall_score for s in second]
    for score in first:
        assert 0.0 <= score.overall_score <= 1.0
        assert 0.0 <= score.confidence <= 1.0
