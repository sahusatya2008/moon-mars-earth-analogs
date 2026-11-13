"""Report-format tests for spatial metadata and standards compliance."""

from __future__ import annotations

import json

from terrax.reports.generator import _geojson_bytes


def _payload() -> dict:
    return {
        "analysis": {
            "algorithm_version": "analogue-0.2.0",
            "feature_schema_version": "features-0.2.0",
        },
        "reference_region": {
            "body_id": "moon",
            "name": "Aristarchus Plateau",
            "center_lon": -47.4,
            "center_lat": 25.4,
            "size_km": 40.0,
            "geometry": {"type": "Point", "coordinates": [-47.4, 25.4]},
            "source": "LRO LOLA",
            "citation": "Smith et al. (2010)",
        },
        "rankings": [
            {
                "candidate_id": "00000000-0000-0000-0000-000000000001",
                "rank": 1,
                "display_name": "Atacama candidate",
                "center_lon": -70.2,
                "center_lat": -25.0,
                "overall_score": 0.789,
                "confidence": 0.904,
                "confidence_band": "high",
                "evidence_coverage": 1.0,
                "geometry": {"type": "Point", "coordinates": [-70.2, -25.0]},
            }
        ],
    }


def test_geojson_does_not_apply_earth_crs_to_planetary_features():
    collection = json.loads(_geojson_bytes(_payload()))

    # RFC 7946 removed the crs member; more importantly, one Earth CRS cannot describe a
    # collection containing lunar and martian coordinates.
    assert "crs" not in collection
    assert "terrax:spatial_reference" in collection

    reference, candidate = collection["features"]
    assert reference["properties"]["body_id"] == "moon"
    assert reference["properties"]["spatial_reference"]["body_fixed"] is True
    assert reference["properties"]["spatial_reference"]["longitude_direction"] == "east"

    assert candidate["properties"]["body_id"] == "earth"
    assert candidate["properties"]["spatial_reference"]["body_fixed"] is False
    assert "CRS84" in candidate["properties"]["spatial_reference"]["crs"]
