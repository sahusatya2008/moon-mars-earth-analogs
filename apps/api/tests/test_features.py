"""Terrain feature extraction invariants."""

from __future__ import annotations

import math

import numpy as np
import pytest

from terrax.engine.features import InsufficientData, extract_terrain_profile


def _profile(window):
    return extract_terrain_profile(
        window,
        native_resolution_m=window.native_resolution_m,
        vertical_uncertainty_m=2.0,
        product_method="test fixture",
    )


def test_constant_surface_has_zero_relief_and_slope(make_window):
    profile = _profile(make_window(np.full((64, 64), 1234.0)))
    assert profile.features["relief_range"].value == pytest.approx(0.0, abs=1e-6)
    assert profile.features["elevation_std"].value == pytest.approx(0.0, abs=1e-6)
    assert profile.features["slope_mean"].value == pytest.approx(0.0, abs=1e-6)
    assert profile.features["roughness_rms"].value == pytest.approx(0.0, abs=1e-6)
    assert profile.features["trafficable_fraction"].value == pytest.approx(1.0, abs=1e-6)
    assert profile.valid_fraction == 1.0


def test_plane_slope_matches_analytic_gradient(make_window):
    # z = 0.5 x, so a 500 m cell rises 250 m: slope = atan(0.5) = 26.565 deg.
    y, x = np.mgrid[0:64, 0:64]
    profile = _profile(make_window(x * 250.0 + y * 0.0))
    expected = math.degrees(math.atan(0.5))
    assert profile.features["slope_mean"].value == pytest.approx(expected, abs=0.2)
    assert profile.features["slope_p95"].value == pytest.approx(expected, abs=0.2)
    # Every cell on a plane exceeds 25 degrees but not 30 degrees.
    assert profile.features["slope_gt_25"].value == pytest.approx(1.0, abs=1e-6)
    assert profile.features["slope_gt_15"].value == pytest.approx(1.0, abs=1e-6)


def test_hypsometric_integral_varies_with_shape(make_window):
    y, x = np.mgrid[0:64, 0:64]
    radius = np.hypot(x - 32, y - 32)
    peaked = np.exp(-radius / 6.0) * 1000.0
    profile = _profile(make_window(peaked))
    integral = profile.features["hypsometric_integral"].value
    assert integral is not None
    assert 0.0 < integral < 0.4


def test_distribution_metadata_is_persisted(make_window):
    y, x = np.mgrid[0:64, 0:64]
    profile = _profile(make_window(x * 10.0 + y * 4.0))
    hist = profile.features["elevation_distribution"].metadata["distribution"]
    assert len(hist["bins"]) == len(hist["edges"]) - 1
    assert sum(hist["bins"]) == pytest.approx(1.0, abs=1e-4)
    assert hist["n"] == 64 * 64
    assert profile.distributions["slope"]["edges"][0] == 0.0


def test_nan_holes_are_filled_and_valid_fraction_recorded(make_window):
    y, x = np.mgrid[0:64, 0:64]
    values = x * 100.0 + y * 50.0
    values[:, :28] = np.nan
    profile = _profile(make_window(values))
    assert profile.valid_fraction == pytest.approx(1.0 - 28 / 64, abs=1e-6)
    assert profile.features["slope_mean"].value is not None
    assert any("coverage" in w.lower() for w in profile.warnings)


def test_insufficient_valid_data_raises(make_window):
    values = np.full((64, 64), np.nan)
    values[:6, :6] = 1.0
    with pytest.raises(InsufficientData):
        _profile(make_window(values))
