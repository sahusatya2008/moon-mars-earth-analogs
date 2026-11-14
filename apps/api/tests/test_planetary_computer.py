"""Unit tests for the Microsoft Planetary Computer connector.

These tests use synthetic fixtures only for HTTP/raster boundaries; the connector itself was
exercised against live Planetary Computer collections during integration verification.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import numpy as np
import pytest
from affine import Affine

from terrax.catalog import CATALOG_VERSION, dataset_map
from terrax.connectors import planetary_computer as pc
from terrax.connectors.raster import Segment


@pytest.fixture(autouse=True)
def clear_caches():
    pc._tokens.clear()
    pc._search_cache.clear()
    pc._collection_cache.clear()
    yield
    pc._tokens.clear()
    pc._search_cache.clear()
    pc._collection_cache.clear()


def test_sign_href_only_signs_azure_blob_urls(monkeypatch):
    monkeypatch.setattr(pc, "token", lambda collection: "st=1&se=2&sig=secret")
    blob = "https://nasademeuwest.blob.core.windows.net/nasadem-cog/v001/tile.tif"
    assert pc.sign_href(blob, "nasadem") == f"{blob}?st=1&se=2&sig=secret"
    assert pc.sign_href(f"{blob}?foo=bar", "nasadem") == f"{blob}?foo=bar&st=1&se=2&sig=secret"

    preview = "https://planetarycomputer.microsoft.com/api/data/v1/item/preview.png?item=x"
    assert pc.sign_href(preview, "nasadem") == preview
    other = "https://example.com/tile.tif"
    assert pc.sign_href(other, "nasadem") == other


def test_redaction_removes_sas_values_and_signed_queries():
    token = "st=2026-11-13T05%3A53%3A27Z&se=2026-11-14T06%3A08%3A28Z&sig=abc"
    message = f"read failed for https://x.blob.core.windows.net/a.tif?{token}"
    redacted = pc._redact(message, token)
    assert token not in redacted
    assert "sig=abc" not in redacted
    assert "<sas>" in redacted


def test_token_is_cached_and_refreshed_before_expiry(monkeypatch):
    calls = []
    future = (date.today() + timedelta(days=1)).isoformat()
    token_value = f"st=now&se={future}T00%3A00%3A00Z&sig=x"

    def fake_fetch(collection):
        calls.append(collection)
        return token_value

    monkeypatch.setattr(pc, "_fetch_token", fake_fetch)
    assert pc.token("unit-test") == token_value
    assert pc.token("unit-test") == token_value
    assert calls == ["unit-test"]

    pc._tokens["unit-test"] = ("expired", time.time() - 10)
    assert pc.token("unit-test") == token_value
    assert calls == ["unit-test", "unit-test"]


def test_search_footprint_dedupes_and_sorts_across_antimeridian(monkeypatch):
    calls = []

    def fake_search(collection, bbox, **kwargs):
        calls.append(tuple(bbox))
        if bbox[0] > 0:
            return {
                "features": [
                    {
                        "id": "old",
                        "properties": {"start_datetime": "2020-01-01T00:00:00Z"},
                    },
                    {
                        "id": "shared",
                        "properties": {"start_datetime": "2022-01-01T00:00:00Z"},
                    },
                ]
            }
        return {
            "features": [
                {
                    "id": "shared",
                    "properties": {"start_datetime": "2022-01-01T00:00:00Z"},
                },
                {
                    "id": "new",
                    "properties": {"start_datetime": "2024-01-01T00:00:00Z"},
                },
            ]
        }

    monkeypatch.setattr(pc, "search", fake_search)
    result = pc.search_footprint("unit", (179.5, -1.0, -179.5, 1.0), limit=2)
    assert [feature["id"] for feature in result["features"]] == ["new", "shared", "old"]
    assert len(calls) == 2


def test_grid_stats_reports_bias_mae_rmse_and_missing_cells(make_window):
    y, x = np.mgrid[0:64, 0:64]
    primary_values = 100.0 + x * 0.5 + y * 0.25
    secondary_values = primary_values + 2.0
    secondary_values[:8, :8] = np.nan

    primary = make_window(primary_values)
    secondary = make_window(secondary_values)
    stats = pc._grid_stats(primary, secondary, reference_dataset_id="copernicus_dem_glo30")

    assert stats["reference_dataset_id"] == "copernicus_dem_glo30"
    assert stats["overlap_pixels"] == 64 * 64 - 8 * 8
    assert stats["overlap_fraction_of_primary"] == pytest.approx((64 * 64 - 64) / (64 * 64), abs=1e-6)
    assert stats["bias_m"] == pytest.approx(2.0, abs=1e-6)
    assert stats["mae_m"] == pytest.approx(2.0, abs=1e-6)
    assert stats["rmse_m"] == pytest.approx(2.0, abs=1e-6)
    assert stats["comparable"] is True


def test_grid_stats_rejects_mismatched_grids(make_window):
    first = make_window(np.zeros((8, 8)))
    second = make_window(np.zeros((9, 9)))
    with pytest.raises(ValueError, match="cross-check grids differ"):
        pc._grid_stats(first, second, reference_dataset_id="unit")


def _lulc_feature(item_id: str, year: int, href: str) -> dict:
    return {
        "id": item_id,
        "bbox": [-71.0, -26.0, -70.0, -25.0],
        "properties": {"start_datetime": f"{year}-01-01T00:00:00Z"},
        "assets": {"data": {"href": href}},
    }


def _class_segment(values: np.ndarray) -> Segment:
    return Segment(
        array=np.asarray(values, dtype=np.float32),
        transform=Affine(0.01, 0.0, -71.0, 0.0, -0.01, -25.0),
        crs="EPSG:4326",
        nodata=0.0,
    )


def test_lulc_selects_latest_available_year(monkeypatch):
    features = [
        _lulc_feature("2023", 2023, "https://x.blob.core.windows.net/2023.tif"),
        _lulc_feature("2022", 2022, "https://x.blob.core.windows.net/2022.tif"),
    ]
    monkeypatch.setattr(
        pc,
        "search_footprint",
        lambda *args, **kwargs: {"features": features, "query": {}},
    )

    def fake_read(collection, href, bbox, **kwargs):
        values = [[1, 8], [8, 11]] if "2023" in href else [[5, 5], [5, 5]]
        return [_class_segment(np.asarray(values))], {"native_resolution_m": 10.0}

    monkeypatch.setattr(pc, "read_signed_segments", fake_read)
    result = pc.lulc_fractions("io-lulc-annual-v02", -70.2, -24.95, 40.0)

    assert result["year"] == 2023
    assert result["item_ids"] == ["2023"]
    assert result["classes"]["Bare ground"] == pytest.approx(0.5)
    assert result["classes"]["Water"] == pytest.approx(0.25)
    assert result["classes"]["Rangeland"] == pytest.approx(0.25)
    assert result["available_years"] == [2022, 2023]


def test_lulc_honours_preferred_year(monkeypatch):
    features = [
        _lulc_feature("2023", 2023, "https://x.blob.core.windows.net/2023.tif"),
        _lulc_feature("2022", 2022, "https://x.blob.core.windows.net/2022.tif"),
    ]
    monkeypatch.setattr(
        pc,
        "search_footprint",
        lambda *args, **kwargs: {"features": features, "query": {}},
    )
    monkeypatch.setattr(
        pc,
        "read_signed_segments",
        lambda *args, **kwargs: (
            [_class_segment(np.asarray([[5, 5], [5, 5]]))],
            {"native_resolution_m": 10.0},
        ),
    )
    result = pc.lulc_fractions(
        "io-lulc-annual-v02", -70.2, -24.95, 40.0, prefer_year=2022
    )
    assert result["year"] == 2022
    assert result["item_ids"] == ["2022"]
    assert result["classes"]["Crops"] == pytest.approx(1.0)


def test_landcover_agreement_identical_and_disjoint_products():
    worldcover = {"class_codes": {"60": 1.0}, "dominant": "Bare / sparse vegetation"}
    lulc = {"class_codes": {"8": 1.0}, "dominant": "Bare ground", "year": 2023}
    same = pc.landcover_agreement(worldcover, lulc)
    assert same["available"] is True
    assert same["agreement"] == pytest.approx(1.0)
    assert same["buckets"]["lulc"]["bare"] == pytest.approx(1.0)

    water = {"class_codes": {"80": 1.0}, "dominant": "Permanent water bodies"}
    different = pc.landcover_agreement(water, lulc)
    assert different["agreement"] == pytest.approx(0.0)
    assert "not a validation" in different["caveat"]


def test_optical_context_retries_without_cloud_filter(monkeypatch):
    calls = []

    def fake_search(collection, footprint, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"features": [], "query": {}}
        return {
            "features": [
                {
                    "id": "scene-1",
                    "properties": {
                        "datetime": "2026-01-02T00:00:00Z",
                        "eo:cloud_cover": 12.5,
                        "platform": "sentinel-2b",
                    },
                    "assets": {
                        "rendered_preview": {
                            "href": "https://planetarycomputer.microsoft.com/preview.png"
                        }
                    },
                }
            ],
            "query": {},
        }

    monkeypatch.setattr(pc, "search_footprint", fake_search)
    result = pc.optical_context(
        "sentinel-2-l2a", -70.2, -24.95, 40.0, limit=2, cloud_lt=45.0
    )
    assert calls[0]["query"] == {"eo:cloud_cover": {"lt": 45.0}}
    assert calls[1]["query"] is None
    assert result["items"][0]["id"] == "scene-1"
    assert result["items"][0]["preview_url"].endswith("preview.png")
    assert "collections/sentinel-2-l2a/items/scene-1" in result["items"][0]["stac_item_url"]


def test_probe_reports_sas_access_and_missing_metadata(monkeypatch):
    class Response:
        status_code = 200

    class FakeClient:
        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(pc, "client", lambda: FakeClient())
    monkeypatch.setattr(
        pc,
        "token",
        lambda collection: "st=now&se=2099-01-01T00%3A00%3A00Z&sig=x",
    )
    status, detail, http_status = pc.probe("nasadem")
    assert status == "available"
    assert http_status == 200
    assert "SAS token issued" in detail

    class NotFound:
        status_code = 404

    class MissingClient:
        def get(self, *args, **kwargs):
            return NotFound()

    monkeypatch.setattr(pc, "client", lambda: MissingClient())
    status, detail, http_status = pc.probe("missing")
    assert status == "degraded"
    assert http_status == 404


def test_planetary_computer_collections_are_registered_and_context_only():
    expected = {
        "nasadem_hgt_v001": "nasadem",
        "alos_world3d_30m": "alos-dem",
        "io_lulc_annual_v02": "io-lulc-annual-v02",
        "pc_sentinel2_l2a": "sentinel-2-l2a",
        "pc_landsat_c2_l2": "landsat-c2-l2",
    }
    catalog = dataset_map()
    for dataset_id, collection in expected.items():
        spec = catalog[dataset_id]
        assert spec["body_id"] == "earth"
        assert spec["is_scoring_input"] is False
        assert spec["source_url"].endswith(f"/collections/{collection}")
        assert spec["citation"]
        assert spec["license"]
    assert CATALOG_VERSION == "0.4.0"
