"""Normalized scientific and operational schema.

Spatial data is stored in PostGIS. Large rasters never enter the database; derived
measurements, provenance, and evidence do. Lunar/Martian coordinates use body-relative
planetographic lon/lat and are always paired with an explicit body_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="Guest researcher")
    role: Mapped[str] = mapped_column(String(40), default="researcher")  # researcher|admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)


class PlanetaryBody(Base, TimestampMixin):
    __tablename__ = "planetary_bodies"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # earth|moon|mars
    name: Mapped[str] = mapped_column(String(80))
    radius_km: Mapped[float] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    accent_color: Mapped[str] = mapped_column(String(20), default="#57d8e6")
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class DatasetCollection(Base, TimestampMixin):
    __tablename__ = "dataset_collections"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body_id: Mapped[str] = mapped_column(ForeignKey("planetary_bodies.id"))
    source_agency: Mapped[str] = mapped_column(String(120))
    mission: Mapped[str | None] = mapped_column(String(120))
    instrument: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    variable_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    access_protocol: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    documentation_url: Mapped[str | None] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(Text)
    spatial_resolution_m: Mapped[float | None] = mapped_column(Float)
    temporal_resolution: Mapped[str | None] = mapped_column(String(120))
    temporal_coverage: Mapped[str | None] = mapped_column(String(200))
    processing_level: Mapped[str | None] = mapped_column(String(80))
    requires_credentials: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_env_var: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="unknown")
    status_detail: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    is_scoring_input: Mapped[bool] = mapped_column(Boolean, default=False)


class DatasetGranule(Base, TimestampMixin):
    __tablename__ = "dataset_granules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset_collections.id", ondelete="CASCADE"))
    granule_id: Mapped[str] = mapped_column(String(300), index=True)
    acquisition_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquisition_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geometry = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))
    source_url: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (UniqueConstraint("dataset_id", "granule_id", name="uq_granule_dataset"),)


class PlanetaryRegion(Base, TimestampMixin):
    __tablename__ = "planetary_regions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    body_id: Mapped[str] = mapped_column(ForeignKey("planetary_bodies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="reference")
    description: Mapped[str | None] = mapped_column(Text)
    center_lon: Mapped[float] = mapped_column(Float)
    center_lat: Mapped[float] = mapped_column(Float)
    size_km: Mapped[float] = mapped_column(Float, default=40.0)
    geometry = mapped_column(Geometry(geometry_type="POLYGON", srid=4326, spatial_index=True))
    source: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    citation: Mapped[str | None] = mapped_column(Text)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class EarthCandidate(Base, TimestampMixin):
    __tablename__ = "earth_candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(240))
    display_name: Mapped[str] = mapped_column(String(240))
    country: Mapped[str | None] = mapped_column(String(120))
    admin_region: Mapped[str | None] = mapped_column(String(200))
    center_lon: Mapped[float] = mapped_column(Float)
    center_lat: Mapped[float] = mapped_column(Float)
    size_km: Mapped[float] = mapped_column(Float, default=40.0)
    geometry = mapped_column(Geometry(geometry_type="POLYGON", srid=4326, spatial_index=True))
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True)
    screen_score: Mapped[float | None] = mapped_column(Float)
    elevation_mean: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (Index("ix_earth_candidates_lon_lat", "center_lon", "center_lat"),)


class AnalysisExecution(Base, TimestampMixin):
    __tablename__ = "analysis_executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300))
    reference_region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("planetary_regions.id"))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(120), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    client_token: Mapped[str | None] = mapped_column(String(120), unique=True)
    algorithm_version: Mapped[str] = mapped_column(String(40), default="analogue-0.2.0")
    feature_schema_version: Mapped[str] = mapped_column(String(40), default="features-0.2.0")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dataset_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeatureMeasurement(Base):
    __tablename__ = "feature_measurements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(20))  # reference|candidate
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    body_id: Mapped[str] = mapped_column(String(20))
    dimension: Mapped[str] = mapped_column(String(60), index=True)
    feature_key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40))
    raw_value: Mapped[float | None] = mapped_column(Float)
    normalized_value: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[float | None] = mapped_column(Float)
    quality_flag: Mapped[str] = mapped_column(String(40), default="valid")
    valid_fraction: Mapped[float | None] = mapped_column(Float)
    native_resolution_m: Mapped[float | None] = mapped_column(Float)
    analysis_resolution_m: Mapped[float | None] = mapped_column(Float)
    dataset_id: Mapped[str | None] = mapped_column(String(120), index=True)
    product_id: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(Text)
    citation: Mapped[str | None] = mapped_column(Text)
    processing_version: Mapped[str | None] = mapped_column(String(80))
    method: Mapped[str | None] = mapped_column(String(200))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_feature_analysis_subject", "analysis_id", "subject_id", "dimension"),
        Index("ix_feature_analysis_key", "analysis_id", "feature_key"),
    )


class SimilarityScore(Base):
    __tablename__ = "similarity_scores"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("earth_candidates.id", ondelete="CASCADE"), index=True
    )
    dimension: Mapped[str] = mapped_column(String(60), index=True)
    method: Mapped[str] = mapped_column(String(60))
    score: Mapped[float] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class CandidateRanking(Base):
    __tablename__ = "candidate_rankings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("earth_candidates.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    overall_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_band: Mapped[str] = mapped_column(String(20))
    evidence_coverage: Mapped[float] = mapped_column(Float)
    dimension_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    method_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    missing_dimensions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    strongest_dimensions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    weakest_dimensions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("analysis_id", "candidate_id", name="uq_ranking_candidate"),
        Index("ix_ranking_analysis_rank", "analysis_id", "rank"),
        Index("ix_ranking_analysis_score", "analysis_id", "overall_score"),
    )


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("earth_candidates.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[str] = mapped_column(String(120), index=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(200))
    processing_level: Mapped[str | None] = mapped_column(String(80))
    spatial_resolution_m: Mapped[float | None] = mapped_column(Float)
    acquisition_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uncertainty: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScientificObservation(Base):
    """Universal observation schema required for cross-mission traceability."""

    __tablename__ = "scientific_observations"

    observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_agency: Mapped[str] = mapped_column(String(120))
    mission: Mapped[str] = mapped_column(String(120))
    instrument: Mapped[str] = mapped_column(String(120))
    dataset_id: Mapped[str] = mapped_column(String(120), index=True)
    product_id: Mapped[str] = mapped_column(String(300), index=True)
    acquisition_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    geometry = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True))
    coordinate_reference_system: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    spatial_resolution_m: Mapped[float | None] = mapped_column(Float)
    temporal_resolution: Mapped[str | None] = mapped_column(String(120))
    variable_name: Mapped[str] = mapped_column(String(120), index=True)
    measured_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(60))
    quality_flag: Mapped[str] = mapped_column(String(40), default="valid")
    uncertainty: Mapped[float | None] = mapped_column(Float)
    processing_level: Mapped[str | None] = mapped_column(String(80))
    processing_version: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(200))
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(160), default="queued")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_claim", "status", "run_after", "priority"),)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    level: Mapped[str] = mapped_column(String(20), default="info")
    stage: Mapped[str] = mapped_column(String(160))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    format: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    file_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Comparison(Base, TimestampMixin):
    __tablename__ = "comparisons"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    reference_region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("planetary_regions.id"))
    candidate_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="ready")


class SavedItem(Base):
    __tablename__ = "saved_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    item_type: Mapped[str] = mapped_column(String(40))
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="anonymous")
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(120), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_executions.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    provider: Mapped[str] = mapped_column(String(40), default="deterministic")
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
