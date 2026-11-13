"""Terrain mesh payload tests. The endpoint itself is exercised in test_api.py."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from terrax.connectors.earth_dem import is_constant_zero_fallback
from terrax.connectors.raster import GriddedWindow
from terrax.terrain3d import _is_degenerate_payload, _window_to_payload, build_terrain_mesh


def _window() -> GriddedWindow:
    values = np.array([[10.0, 20.0], [np.nan, 40.0]], dtype=np.float32)
    return GriddedWindow(
        values=values,
        valid=np.isfinite(values),
        transform=None,  # type: ignore[arg-type]
        crs="local",
        width=2,
        height=2,
        resolution_m=500.0,
        native_resolution_m=30.0,
        valid_fraction=0.75,
        source_bounds=(0, 0, 1, 1),
        method="test window",
    )


def test_payload_preserves_missing_cells_and_statistics():
    payload = _window_to_payload(
        _window(),
        body_id="moon",
        lon=-47.4,
        lat=25.4,
        size_km=1.0,
        source={"dataset_id": "test", "product_id": "test", "citation": "test"},
    )
    assert payload["heights"] == [[10.0, 20.0], [None, 40.0]]
    assert payload["min_elevation_m"] == 10.0
    assert payload["max_elevation_m"] == 40.0
    assert payload["mean_elevation_m"] == 23.33
    assert payload["valid_fraction"] == 0.75
    # The payload must be strict JSON; null, not NaN, represents missing data.
    assert "NaN" not in json.dumps(payload, allow_nan=False)


def _zero_window() -> GriddedWindow:
    values = np.zeros((2, 2), dtype=np.float32)
    return GriddedWindow(
        values=values,
        valid=np.ones((2, 2), dtype=bool),
        transform=None,  # type: ignore[arg-type]
        crs="local",
        width=2,
        height=2,
        resolution_m=500.0,
        native_resolution_m=30.0,
        valid_fraction=1.0,
        source_bounds=(0, 0, 1, 1),
        method="test window",
    )


def _fake_earth_dem(window: GriddedWindow, dataset_id: str = "aws_terrain_tiles"):
    return SimpleNamespace(
        window=window,
        dataset_id=dataset_id,
        product_id="terrarium-z12",
        source_url="https://example.test/tiles",
        citation="test citation",
        license="ODC-By",
        native_resolution_m=30.0,
        vertical_uncertainty_m=8.0,
        method="test fallback",
    )


def test_constant_zero_fallback_is_detected():
    assert is_constant_zero_fallback(_fake_earth_dem(_zero_window())) is True
    assert is_constant_zero_fallback(_fake_earth_dem(_window())) is False
    assert (
        is_constant_zero_fallback(_fake_earth_dem(_zero_window(), "copernicus_dem_glo30"))
        is False
    )
    assert _is_degenerate_payload(
        {"source": {"dataset_id": "aws_terrain_tiles"}, "heights": [[0.0, 0.0], [0.0, 0.0]]}
    )
    assert not _is_degenerate_payload(
        {"source": {"dataset_id": "aws_terrain_tiles"}, "heights": [[0.0, 1.0]]}
    )


def test_earth_terrain_rejects_constant_zero_fallback(monkeypatch, tmp_path):
    fake = _fake_earth_dem(_zero_window())
    monkeypatch.setattr("terrax.terrain3d.load_earth_dem", lambda *args, **kwargs: fake)
    monkeypatch.setattr("terrax.terrain3d._cache_path", lambda *args: tmp_path / "mesh.json")

    with pytest.raises(ValueError, match="constant 0 m surface"):
        build_terrain_mesh("earth", -25.0, -70.2, 40.0, 500.0)


def test_degenerate_cached_payload_is_discarded(monkeypatch, tmp_path):
    fake = _fake_earth_dem(_window())
    monkeypatch.setattr("terrax.terrain3d.load_earth_dem", lambda *args, **kwargs: fake)
    cache = tmp_path / "mesh.json"
    monkeypatch.setattr("terrax.terrain3d._cache_path", lambda *args: cache)
    cache.write_text(
        json.dumps(
            {
                "source": {"dataset_id": "aws_terrain_tiles"},
                "heights": [[0.0, 0.0], [0.0, 0.0]],
            }
        )
    )

    payload = build_terrain_mesh("earth", -70.2, -25.0, 5.0, 500.0)
    assert payload["cache_hit"] is False
    assert payload["heights"] == [[10.0, 20.0], [None, 40.0]]
    assert cache.exists()


def test_mesh_cache_round_trip(monkeypatch, tmp_path):
    fake = SimpleNamespace(
        window=_window(),
        dataset_id="lro_lola_ldem_128ppd",
        product_id="test-product",
        source_url="https://example.test/source",
        citation="test citation",
        native_resolution_m=237.0,
        vertical_uncertainty_m=3.0,
        method="test method",
    )
    monkeypatch.setattr("terrax.terrain3d.load_reference_dem", lambda *args, **kwargs: fake)
    cache = tmp_path / "mesh.json"
    monkeypatch.setattr("terrax.terrain3d._cache_path", lambda *args: cache)

    first = build_terrain_mesh("moon", 0.0, 0.0, 5.0, 500.0)
    assert first["cache_hit"] is False
    assert cache.exists()
    second = build_terrain_mesh("moon", 0.0, 0.0, 5.0, 500.0)
    assert second["cache_hit"] is True
    assert second["heights"] == first["heights"]
