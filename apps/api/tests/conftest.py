"""Shared fixtures for TERRA-X tests."""

from __future__ import annotations

import numpy as np
import pytest
from affine import Affine

from terrax.connectors.raster import GriddedWindow


@pytest.fixture
def make_window():
    def _make(
        values: np.ndarray,
        *,
        resolution_m: float = 500.0,
        native_resolution_m: float = 500.0,
        method: str = "synthetic fixture",
    ) -> GriddedWindow:
        values = np.asarray(values, dtype=np.float64)
        height, width = values.shape
        valid = np.isfinite(values)
        return GriddedWindow(
            values=values,
            valid=valid,
            transform=Affine(resolution_m, 0.0, 0.0, 0.0, -resolution_m, 0.0),
            crs="local",
            width=width,
            height=height,
            resolution_m=resolution_m,
            native_resolution_m=native_resolution_m,
            valid_fraction=float(valid.mean()),
            source_bounds=(0.0, 0.0, float(width), float(height)),
            method=method,
        )

    return _make


@pytest.fixture
def ramp_window(make_window):
    """A 45 degree plane on a 500 m grid."""
    y, x = np.mgrid[0:64, 0:64]
    return make_window(x * 500.0 + y * 500.0)
