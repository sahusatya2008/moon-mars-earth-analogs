"""API request contracts reject silently invalid science configurations."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from terrax.schemas import (
    AnalysisConfig,
    AnalysisCreate,
    ReferenceRegionCreate,
    ReportCreate,
    SearchConfig,
)


def test_analysis_defaults_are_explicit():
    config = AnalysisConfig()
    assert config.footprint_km == 40.0
    assert config.analysis_resolution_m == 500.0
    assert set(config.dimensions) == {
        "geomorphology",
        "morphometry",
        "texture",
        "operational",
    }
    assert config.missing_data_policy == "penalize"
    assert sum(config.weights.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "bbox",
    [
        [0, 0, 0, 1],  # zero-width
        [-181, 0, 10, 10],
        [10, 0, -10, 10],
        [0, 20, 10, -20],
        [0, 0, 10],
    ],
)
def test_invalid_bbox_rejected(bbox):
    with pytest.raises(ValidationError):
        SearchConfig(bbox=bbox)


def test_unknown_weight_dimension_rejected():
    with pytest.raises(ValidationError):
        AnalysisConfig(weights={"geomorphology": 1.0, "albedo": 0.2})


def test_zero_weight_for_enabled_dimension_rejected():
    with pytest.raises(ValidationError):
        AnalysisConfig(weights={"geomorphology": 1.0, "morphometry": 0.0})


def test_inverted_temporal_window_rejected():
    with pytest.raises(ValidationError):
        AnalysisConfig(temporal_start="2024-01-02", temporal_end="2024-01-01")


def test_analysis_create_forbids_extra_fields():
    with pytest.raises(ValidationError):
        AnalysisCreate(
            title="test analysis",
            reference_region_id=uuid.uuid4(),
            fabricated_confidence=0.99,
        )


def test_report_format_is_a_closed_set():
    with pytest.raises(ValidationError):
        ReportCreate(analysis_id=uuid.uuid4(), format="xlsx")


def test_region_contract_is_limited_to_moon_and_mars():
    with pytest.raises(ValidationError):
        ReferenceRegionCreate(body_id="earth", name="Not allowed", center_lon=0, center_lat=0)
