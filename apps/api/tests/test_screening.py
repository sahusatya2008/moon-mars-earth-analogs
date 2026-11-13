"""Coarse screening and clustering invariants."""

from __future__ import annotations

import numpy as np
import pytest

from terrax.engine.screening import (
    block_average,
    coarse_profile,
    coarse_similarity,
    greedy_cluster,
    haversine_km,
    screening_centers,
)


def test_block_average_is_nan_aware():
    values = np.array(
        [
            [1.0, 2.0, np.nan, np.nan],
            [3.0, 4.0, 6.0, 8.0],
            [np.nan, np.nan, 10.0, 12.0],
            [np.nan, np.nan, 14.0, 16.0],
        ]
    )
    out = block_average(values, 2)
    assert out.shape == (2, 2)
    assert out[0, 0] == pytest.approx(2.5)
    assert out[0, 1] == pytest.approx(7.0)
    assert np.isnan(out[1, 0])
    assert out[1, 1] == pytest.approx(13.0)

    all_nan = np.full((2, 2), np.nan)
    assert np.isnan(block_average(all_nan, 2)).all()


def test_block_average_identity_for_factor_one():
    values = np.arange(9.0).reshape(3, 3)
    assert np.array_equal(block_average(values, 1), values)


def test_coarse_profile_rejects_empty_data():
    assert coarse_profile(np.full((32, 32), np.nan), 5000.0) is None


def test_coarse_similarity_identical_is_one():
    y, x = np.mgrid[0:128, 0:128]
    profile = coarse_profile(x * 3.0 + y * 1.5, 5000.0)
    assert profile is not None
    score, detail = coarse_similarity(profile, profile)
    assert score == pytest.approx(1.0)
    assert detail["coverage"] == pytest.approx(1.0)
    assert len(detail["features"]) == 6


def test_coarse_similarity_prefers_closer_profile():
    y, x = np.mgrid[0:128, 0:128]
    reference = coarse_profile(x * 8.0, 5000.0)
    near = coarse_profile(x * 8.2, 5000.0)
    far = coarse_profile(x * 1.0, 5000.0)
    assert reference and near and far
    near_score, _ = coarse_similarity(reference, near)
    far_score, _ = coarse_similarity(reference, far)
    assert near_score > far_score


def test_haversine_matches_known_distance():
    # Quito to the equator at the same longitude is roughly 1 degree of latitude.
    assert haversine_km(-78.5, -0.2, -78.5, 0.8) == pytest.approx(111.2, abs=1.0)


def test_greedy_cluster_respects_radius_and_keeps_best_first():
    points = [
        {"lon": -70.0, "lat": -24.0, "score": 0.9},
        {"lon": -70.01, "lat": -24.01, "score": 0.8},
        {"lon": -69.0, "lat": -24.0, "score": 0.7},
        {"lon": -69.01, "lat": -24.01, "score": 0.6},
    ]
    clusters = greedy_cluster(points, radius_km=20.0, limit=10)
    assert len(clusters) == 2
    assert clusters[0]["score"] == pytest.approx(0.9)
    assert clusters[1]["score"] == pytest.approx(0.7)


def test_screening_centers_stay_inside_bbox_and_land_aware():
    centers = screening_centers(-70.5, -25.5, -68.5, -23.5, 1.0, land_points=5)
    assert centers
    for lon, lat in centers:
        assert -70.5 <= lon <= -68.5
        assert -25.5 <= lat <= -23.5
