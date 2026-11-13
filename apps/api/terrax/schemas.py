"""Typed API contracts. All request bodies are validated; unknown fields are rejected."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BODY_IDS = ("earth", "moon", "mars")
DIMENSIONS = ("geomorphology", "morphometry", "texture", "operational")
EVIDENCE_DIMENSIONS = ("surface", "thermal", "water_ice", "aeolian", "environment")
METHODS = (
    "mcda",
    "weighted_euclidean",
    "standardized_euclidean",
    "cosine",
    "mahalanobis",
    "wasserstein",
    "percentile",
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    worker_queue: dict[str, Any]
    earthdata_configured: bool
    llm_configured: bool
    time: datetime


class PlanetRead(ORMModel):
    id: str
    name: str
    radius_km: float
    description: str | None
    accent_color: str
    display_order: int


class DatasetRead(ORMModel):
    id: str
    title: str
    body_id: str
    source_agency: str
    mission: str | None
    instrument: str | None
    description: str
    variable_names: list[str]
    access_protocol: str
    source_url: str
    documentation_url: str | None
    citation: str
    license: str
    license_url: str | None
    spatial_resolution_m: float | None
    temporal_resolution: str | None
    temporal_coverage: str | None
    processing_level: str | None
    requires_credentials: bool
    credential_env_var: str | None
    status: str
    status_detail: str | None
    last_checked_at: datetime | None
    last_success_at: datetime | None
    metadata_json: dict[str, Any]
    is_scoring_input: bool


class DatasetAvailability(BaseModel):
    dataset_id: str
    status: str
    detail: str
    checked_at: datetime
    http_status: int | None = None
    latency_ms: float | None = None
    probe_url: str | None = None


class DatasetListResponse(BaseModel):
    items: list[DatasetRead]
    total: int


class ReferenceRegionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_id: Literal["moon", "mars"]
    name: str = Field(min_length=2, max_length=200)
    center_lon: float = Field(ge=-180, le=180)
    center_lat: float = Field(ge=-90, le=90)
    size_km: float = Field(default=40.0, ge=5, le=300)
    description: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=200)
    source_url: str | None = None
    citation: str | None = None


class RegionRead(ORMModel):
    id: uuid.UUID
    body_id: str
    name: str
    kind: str
    description: str | None
    center_lon: float
    center_lat: float
    size_km: float
    geometry: dict[str, Any] | None = None
    source: str | None
    source_url: str | None
    citation: str | None
    is_preset: bool
    metadata_json: dict[str, Any]
    created_at: datetime


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bbox: list[float] = Field(default_factory=lambda: [-180.0, -60.0, 180.0, 80.0])
    grid_mode: Literal["latlon", "h3"] = "latlon"
    cell_size_deg: float = Field(default=2.0, ge=0.25, le=20.0)
    h3_resolution: int = Field(default=3, ge=1, le=6)
    screening_resolution_km: float = Field(default=5.0, ge=2.0, le=25.0)
    refine_top_n: int = Field(default=24, ge=5, le=300)
    max_candidates: int = Field(default=25, ge=1, le=100)
    cluster_radius_km: float = Field(default=100.0, ge=10.0, le=1000.0)
    random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    exclude_polar_gt_deg: float = Field(default=85.0, ge=60.0, le=90.0)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        w, s, e, n = v
        if not (-180 <= w < e <= 180):
            raise ValueError("bbox longitudes must satisfy -180 <= west < east <= 180")
        if not (-90 <= s < n <= 90):
            raise ValueError("bbox latitudes must satisfy -90 <= south < north <= 90")
        if (e - w) * (n - s) > 360 * 180:
            raise ValueError("invalid bbox area")
        return v


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    footprint_km: float = Field(default=40.0, ge=5, le=300)
    analysis_resolution_m: float = Field(default=500.0, ge=60, le=5000)
    dimensions: list[Literal["geomorphology", "morphometry", "texture", "operational"]] = Field(
        default_factory=lambda: list(DIMENSIONS)
    )
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "geomorphology": 0.35,
            "morphometry": 0.30,
            "texture": 0.20,
            "operational": 0.15,
        }
    )
    methods: list[Literal["mcda", "weighted_euclidean", "standardized_euclidean", "cosine", "mahalanobis", "wasserstein", "percentile"]] = Field(
        default_factory=lambda: ["mcda", "weighted_euclidean", "standardized_euclidean", "cosine", "wasserstein"]
    )
    min_feature_coverage: float = Field(default=0.5, ge=0.25, le=1.0)
    missing_data_policy: Literal["exclude_dimension", "penalize"] = "penalize"
    temporal_start: date | None = None
    temporal_end: date | None = None
    search: SearchConfig = Field(default_factory=SearchConfig)

    @model_validator(mode="after")
    def validate_weights(self) -> AnalysisConfig:
        unknown = set(self.weights) - set(DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown dimension weights: {sorted(unknown)}")
        enabled = set(self.dimensions)
        for dim, w in self.weights.items():
            if w < 0 or w > 1:
                raise ValueError(f"weight for {dim} must be in [0, 1]")
            if dim in enabled and w == 0:
                raise ValueError(f"enabled dimension {dim} has zero weight")
        total = sum(self.weights[d] for d in self.dimensions if d in self.weights)
        if total <= 0:
            raise ValueError("at least one enabled dimension must have positive weight")
        if self.temporal_start and self.temporal_end and self.temporal_start > self.temporal_end:
            raise ValueError("temporal_start must not be after temporal_end")
        return self


class AnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=300)
    reference_region_id: uuid.UUID
    config: AnalysisConfig = Field(default_factory=AnalysisConfig)
    client_token: str | None = Field(default=None, max_length=120)


class AnalysisRead(ORMModel):
    id: uuid.UUID
    title: str
    reference_region_id: uuid.UUID
    status: str
    stage: str
    progress: float
    algorithm_version: str
    feature_schema_version: str
    config: dict[str, Any]
    dataset_manifest: dict[str, Any]
    result_summary: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AnalysisStatus(BaseModel):
    id: uuid.UUID
    status: str
    stage: str
    progress: float
    job_id: uuid.UUID | None = None
    error_message: str | None = None
    updated_at: datetime | None = None


class AnalysisListResponse(BaseModel):
    items: list[AnalysisRead]
    total: int
    page: int
    page_size: int


class DimensionScore(BaseModel):
    dimension: str
    score: float
    confidence: float
    coverage: float
    weight_configured: float
    weight_used: float
    features_used: int
    features_missing: int
    unavailable_reason: str | None = None


class CandidateRankingRead(BaseModel):
    rank: int
    candidate_id: uuid.UUID
    display_name: str
    name: str | None
    country: str | None
    admin_region: str | None
    center_lon: float
    center_lat: float
    size_km: float
    geometry: dict[str, Any] | None = None
    overall_score: float
    confidence: float
    confidence_band: str
    evidence_coverage: float
    dimension_scores: dict[str, Any]
    method_scores: dict[str, Any]
    missing_dimensions: list[dict[str, Any]]
    strongest_dimensions: list[dict[str, Any]]
    weakest_dimensions: list[dict[str, Any]]
    limitations: list[str]
    elevation_mean: float | None = None


class AnalysisResults(BaseModel):
    analysis: AnalysisRead
    reference_region: RegionRead
    rankings: list[CandidateRankingRead]
    method_agreement: dict[str, Any]
    dataset_manifest: dict[str, Any]
    reproducibility: dict[str, Any]
    limitations: list[str]


class FeatureRead(ORMModel):
    subject_type: str
    subject_id: uuid.UUID
    body_id: str
    dimension: str
    feature_key: str
    value: float | None
    unit: str
    raw_value: float | None
    normalized_value: float | None
    uncertainty: float | None
    quality_flag: str
    valid_fraction: float | None
    native_resolution_m: float | None
    analysis_resolution_m: float | None
    dataset_id: str | None
    product_id: str | None
    source_url: str | None
    citation: str | None
    processing_version: str | None
    method: str | None
    acquired_at: datetime | None
    metadata_json: dict[str, Any]


class EvidenceRead(ORMModel):
    id: uuid.UUID
    dataset_id: str
    title: str
    source_url: str
    citation: str
    license: str
    processing_level: str | None
    spatial_resolution_m: float | None
    acquisition_datetime: datetime | None
    uncertainty: float | None
    details: dict[str, Any]


class EvidenceListResponse(BaseModel):
    items: list[EvidenceRead]
    total: int


class CandidateDetail(BaseModel):
    candidate: CandidateRankingRead
    reference_region: RegionRead
    reference_features: list[FeatureRead]
    candidate_features: list[FeatureRead]
    evidence: list[EvidenceRead]
    profile: dict[str, Any]


class CandidateListResponse(BaseModel):
    items: list[CandidateRankingRead]
    total: int
    page: int
    page_size: int


class JobRead(ORMModel):
    id: uuid.UUID
    job_type: str
    status: str
    priority: int
    progress: float
    stage: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_message: str | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    analysis_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobEventRead(ORMModel):
    id: int
    job_id: uuid.UUID
    at: datetime
    level: str
    stage: str
    progress: float
    message: str
    data: dict[str, Any]


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: uuid.UUID
    format: Literal["pdf", "csv", "geojson", "json", "manifest"]
    title: str | None = Field(default=None, max_length=300)


class ReportRead(ORMModel):
    id: uuid.UUID
    analysis_id: uuid.UUID
    format: str
    title: str
    status: str
    size_bytes: int | None
    checksum_sha256: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_region_id: uuid.UUID
    candidate_ids: list[uuid.UUID] = Field(min_length=1, max_length=8)
    analysis_id: uuid.UUID | None = None


class ComparisonRead(ORMModel):
    id: uuid.UUID
    reference_region_id: uuid.UUID
    candidate_ids: list[str]
    config: dict[str, Any]
    results: dict[str, Any] | None
    status: str
    created_at: datetime


class AssistantQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=2000)
    analysis_id: uuid.UUID | None = None
    candidate_id: uuid.UUID | None = None


class AssistantAnswer(BaseModel):
    answer: str
    citations: list[dict[str, Any]]
    provider: str
    grounded: bool
    limitations: list[str]
    analysis_id: uuid.UUID | None = None


class Paginated(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class AvailabilityReport(BaseModel):
    datasets: list[DatasetAvailability]
    checked_at: datetime
    available: int
    requires_credentials: int
    unavailable: int


class CMRSearchResponse(BaseModel):
    source: str
    collections: list[dict[str, Any]]
    took_ms: float
    query: dict[str, Any]


class PlanetaryDiscoveryResponse(BaseModel):
    source: str
    body: str
    items: list[dict[str, Any]]
    total: int
    query: dict[str, Any]
    access_notes: list[str]
