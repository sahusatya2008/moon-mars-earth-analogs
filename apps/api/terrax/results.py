"""Read models assembled from persisted analysis records.

The API and report generator both use these functions so an exported number is always the same
number the UI shows, with the same provenance and limitations.
"""

from __future__ import annotations

import uuid
from typing import Any

from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AnalysisExecution,
    CandidateRanking,
    EarthCandidate,
    EvidenceRecord,
    FeatureMeasurement,
    PlanetaryRegion,
)


def geometry_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        return dict(mapping(to_shape(value)))
    except Exception:
        return None


def region_dict(region: PlanetaryRegion) -> dict[str, Any]:
    return {
        "id": region.id,
        "body_id": region.body_id,
        "name": region.name,
        "kind": region.kind,
        "description": region.description,
        "center_lon": region.center_lon,
        "center_lat": region.center_lat,
        "size_km": region.size_km,
        "geometry": geometry_json(region.geometry),
        "source": region.source,
        "source_url": region.source_url,
        "citation": region.citation,
        "is_preset": region.is_preset,
        "metadata_json": region.metadata_json or {},
        "created_at": region.created_at,
    }


def analysis_dict(analysis: AnalysisExecution) -> dict[str, Any]:
    return {
        "id": analysis.id,
        "title": analysis.title,
        "reference_region_id": analysis.reference_region_id,
        "status": analysis.status,
        "stage": analysis.stage,
        "progress": analysis.progress,
        "algorithm_version": analysis.algorithm_version,
        "feature_schema_version": analysis.feature_schema_version,
        "config": analysis.config or {},
        "dataset_manifest": analysis.dataset_manifest or {},
        "result_summary": analysis.result_summary,
        "error_message": analysis.error_message,
        "created_at": analysis.created_at,
        "started_at": analysis.started_at,
        "completed_at": analysis.completed_at,
    }


def feature_dict(feature: FeatureMeasurement, *, include_distribution: bool = False) -> dict[str, Any]:
    metadata = dict(feature.metadata_json or {})
    if not include_distribution:
        metadata.pop("distribution", None)
    return {
        "subject_type": feature.subject_type,
        "subject_id": feature.subject_id,
        "body_id": feature.body_id,
        "dimension": feature.dimension,
        "feature_key": feature.feature_key,
        "value": feature.value,
        "unit": feature.unit,
        "raw_value": feature.raw_value,
        "normalized_value": feature.normalized_value,
        "uncertainty": feature.uncertainty,
        "quality_flag": feature.quality_flag,
        "valid_fraction": feature.valid_fraction,
        "native_resolution_m": feature.native_resolution_m,
        "analysis_resolution_m": feature.analysis_resolution_m,
        "dataset_id": feature.dataset_id,
        "product_id": feature.product_id,
        "source_url": feature.source_url,
        "citation": feature.citation,
        "processing_version": feature.processing_version,
        "method": feature.method,
        "acquired_at": feature.acquired_at,
        "metadata_json": metadata,
    }


def evidence_dict(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "title": record.title,
        "source_url": record.source_url,
        "citation": record.citation,
        "license": record.license,
        "processing_level": record.processing_level,
        "spatial_resolution_m": record.spatial_resolution_m,
        "acquisition_datetime": record.acquisition_datetime,
        "uncertainty": record.uncertainty,
        "details": record.details or {},
    }


def ranking_dict(db: Session, ranking: CandidateRanking) -> dict[str, Any]:
    candidate = db.get(EarthCandidate, ranking.candidate_id)
    if candidate is None:
        raise ValueError(f"candidate {ranking.candidate_id} is missing")
    return {
        "rank": ranking.rank,
        "candidate_id": candidate.id,
        "display_name": candidate.display_name,
        "name": candidate.name,
        "country": candidate.country,
        "admin_region": candidate.admin_region,
        "center_lon": candidate.center_lon,
        "center_lat": candidate.center_lat,
        "size_km": candidate.size_km,
        "geometry": geometry_json(candidate.geometry),
        "overall_score": ranking.overall_score,
        "confidence": ranking.confidence,
        "confidence_band": ranking.confidence_band,
        "evidence_coverage": ranking.evidence_coverage,
        "dimension_scores": ranking.dimension_scores or {},
        "method_scores": ranking.method_scores or {},
        "missing_dimensions": ranking.missing_dimensions or [],
        "strongest_dimensions": ranking.strongest_dimensions or [],
        "weakest_dimensions": ranking.weakest_dimensions or [],
        "limitations": ranking.limitations or [],
        "elevation_mean": candidate.elevation_mean,
    }


def analysis_rankings(db: Session, analysis_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(CandidateRanking)
        .where(CandidateRanking.analysis_id == analysis_id)
        .order_by(CandidateRanking.rank.asc())
    ).all()
    return [ranking_dict(db, row) for row in rows]


def method_agreement(analysis: AnalysisExecution) -> dict[str, Any]:
    summary = analysis.result_summary or {}
    return summary.get("method_agreement") or {}


def analysis_results(db: Session, analysis: AnalysisExecution) -> dict[str, Any]:
    region = db.get(PlanetaryRegion, analysis.reference_region_id)
    if region is None:
        raise ValueError("reference region is missing")
    manifest = analysis.dataset_manifest or {}
    return {
        "analysis": analysis_dict(analysis),
        "reference_region": region_dict(region),
        "rankings": analysis_rankings(db, analysis.id),
        "method_agreement": method_agreement(analysis),
        "dataset_manifest": manifest,
        "reproducibility": manifest.get("reproducibility") or {},
        "limitations": manifest.get("limitations") or [],
    }


def candidate_detail(db: Session, candidate_id: uuid.UUID) -> dict[str, Any]:
    candidate = db.get(EarthCandidate, candidate_id)
    if candidate is None:
        raise ValueError("candidate not found")
    ranking = db.scalar(
        select(CandidateRanking).where(CandidateRanking.candidate_id == candidate_id)
    )
    if ranking is None:
        raise ValueError("candidate has no ranking in any analysis")
    analysis = db.get(AnalysisExecution, ranking.analysis_id)
    if analysis is None:
        raise ValueError("analysis for candidate is missing")
    region = db.get(PlanetaryRegion, analysis.reference_region_id)
    if region is None:
        raise ValueError("reference region is missing")

    reference_features = db.scalars(
        select(FeatureMeasurement)
        .where(
            FeatureMeasurement.analysis_id == analysis.id,
            FeatureMeasurement.subject_type == "reference",
        )
        .order_by(FeatureMeasurement.dimension, FeatureMeasurement.feature_key)
    ).all()
    candidate_features = db.scalars(
        select(FeatureMeasurement)
        .where(
            FeatureMeasurement.analysis_id == analysis.id,
            FeatureMeasurement.subject_type == "candidate",
            FeatureMeasurement.subject_id == candidate_id,
        )
        .order_by(FeatureMeasurement.dimension, FeatureMeasurement.feature_key)
    ).all()
    evidence = db.scalars(
        select(EvidenceRecord)
        .where(
            EvidenceRecord.analysis_id == analysis.id,
            EvidenceRecord.candidate_id == candidate_id,
        )
        .order_by(EvidenceRecord.dataset_id, EvidenceRecord.title)
    ).all()
    metadata = candidate.metadata_json or {}
    return {
        "candidate": ranking_dict(db, ranking),
        "reference_region": region_dict(region),
        "reference_features": [feature_dict(f) for f in reference_features],
        "candidate_features": [feature_dict(f) for f in candidate_features],
        "evidence": [evidence_dict(e) for e in evidence],
        "profile": {
            "analysis_id": str(analysis.id),
            "analysis_title": analysis.title,
            "body_id": "earth",
            "center_lon": candidate.center_lon,
            "center_lat": candidate.center_lat,
            "size_km": candidate.size_km,
            "elevation_mean_m": candidate.elevation_mean,
            "coarse_screen_score": candidate.screen_score,
            "context": metadata.get("context") or {},
            "gazetteer": metadata.get("gazetteer"),
            "refinement_dataset": metadata.get("refinement_dataset"),
            "refinement_method": metadata.get("refinement_method"),
            "analysis_status": analysis.status,
            "algorithm_version": analysis.algorithm_version,
            "feature_schema_version": analysis.feature_schema_version,
        },
    }


def reference_evidence(db: Session, analysis_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.analysis_id == analysis_id, EvidenceRecord.candidate_id.is_(None))
        .order_by(EvidenceRecord.dataset_id)
    ).all()
    return [evidence_dict(e) for e in rows]


__all__ = [
    "geometry_json",
    "region_dict",
    "analysis_dict",
    "feature_dict",
    "evidence_dict",
    "ranking_dict",
    "analysis_rankings",
    "analysis_results",
    "candidate_detail",
    "reference_evidence",
]
