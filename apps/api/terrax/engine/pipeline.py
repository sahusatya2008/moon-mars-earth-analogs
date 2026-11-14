"""End-to-end analysis pipeline.

The pipeline is deliberately boring and auditable: measure the reference, screen real Earth
terrain, refine with Copernicus, persist every derived measurement with its provenance, score
with the transparent similarity kernel, and write a manifest. It never invents an observation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import box
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..catalog import CATALOG_VERSION, dataset_map
from ..config import get_settings
from ..connectors import albedo, firms, meteomatics, nominatim, preview, stac, worldcover
from ..connectors import planetary_computer as pc
from ..connectors.earth_dem import EarthDEM
from ..connectors.planetary_dem import ReferenceDEM, load_reference_dem
from ..connectors.raster import (
    EARTH_RADIUS_KM,
    MARS_RADIUS_KM,
    MOON_RADIUS_KM,
    footprint_bbox,
)
from ..models import (
    AnalysisExecution,
    CandidateRanking,
    EarthCandidate,
    EvidenceRecord,
    FeatureMeasurement,
    JobEvent,
    PlanetaryRegion,
    ProcessingJob,
    SimilarityScore,
)
from .discovery import DiscoveryResult, discover
from .features import (
    DIMENSION_DESCRIPTIONS,
    FEATURE_SPECS,
    SCORED_DIMENSIONS,
    TerrainProfile,
    dimension_weights,
    extract_terrain_profile,
)
from .similarity import CandidateScore, method_summary

settings = get_settings()

ALGORITHM_VERSION = "analogue-0.2.0"
FEATURE_SCHEMA_VERSION = "features-0.2.0"
RADII = {"earth": EARTH_RADIUS_KM, "moon": MOON_RADIUS_KM, "mars": MARS_RADIUS_KM}


class JobCancelled(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass
    return str(value)


def _sha256(payload: Any) -> str:
    encoded = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _polygon_ewkt(body_id: str, lon: float, lat: float, size_km: float) -> str:
    west, south, east, north, crossing = footprint_bbox(lon, lat, size_km, RADII[body_id])
    if crossing:
        west = -180.0
    # Earth candidates never cross the seam in the current search bbox validation, but the
    # clamp keeps the stored polygon valid if a future search does.
    geom = box(max(-180.0, west), max(-90.0, south), min(180.0, east), min(90.0, north))
    return f"SRID=4326;{geom.wkt}"


def _dem_manifest(dem: ReferenceDEM) -> dict[str, Any]:
    spec = dataset_map().get(dem.dataset_id, {})
    return {
        "dataset_id": dem.dataset_id,
        "title": spec.get("title", dem.dataset_id),
        "role": "reference topography (scoring input)",
        "product_id": dem.product_id,
        "source_url": dem.source_url,
        "citation": dem.citation,
        "license": spec.get("license"),
        "native_resolution_m": dem.native_resolution_m,
        "vertical_uncertainty_m": dem.vertical_uncertainty_m,
        "processing_level": spec.get("processing_level"),
        "method": dem.method,
        "window": {
            "valid_fraction": round(dem.window.valid_fraction, 6),
            "width": dem.window.width,
            "height": dem.window.height,
            "resolution_m": dem.window.resolution_m,
            "crs": dem.window.crs,
        },
    }


def _feature_rows(
    *,
    analysis_id: uuid.UUID,
    subject_type: str,
    subject_id: uuid.UUID,
    body_id: str,
    profile: TerrainProfile,
    dataset_id: str,
    product_id: str,
    source_url: str,
    citation: str,
    analysis_resolution_m: float,
    acquired_at: datetime | None = None,
) -> list[FeatureMeasurement]:
    rows: list[FeatureMeasurement] = []
    for key, feature in profile.features.items():
        spec = FEATURE_SPECS[key]
        rows.append(
            FeatureMeasurement(
                analysis_id=analysis_id,
                subject_type=subject_type,
                subject_id=subject_id,
                body_id=body_id,
                dimension=feature.dimension,
                feature_key=key,
                value=feature.value,
                unit=feature.unit,
                raw_value=feature.value,
                normalized_value=None,
                uncertainty=feature.uncertainty,
                quality_flag="valid" if feature.value is not None else "missing",
                valid_fraction=profile.valid_fraction,
                native_resolution_m=profile.native_resolution_m,
                analysis_resolution_m=analysis_resolution_m,
                dataset_id=dataset_id,
                product_id=product_id,
                source_url=source_url,
                citation=citation,
                processing_version=FEATURE_SCHEMA_VERSION,
                method=feature.method,
                acquired_at=acquired_at,
                metadata_json={
                    "tolerance": spec["tolerance"],
                    "declared_weight": spec["weight"],
                    "scored": spec["weight"] > 0 and spec["dimension"] in SCORED_DIMENSIONS,
                    **({"distribution": feature.metadata["distribution"]} if "distribution" in feature.metadata else {}),
                },
            )
        )
    return rows


def _evidence(
    *,
    analysis_id: uuid.UUID,
    candidate_id: uuid.UUID | None,
    dataset_id: str,
    title: str,
    source_url: str,
    citation: str,
    license: str,
    details: dict[str, Any],
    processing_level: str | None = None,
    spatial_resolution_m: float | None = None,
    uncertainty: float | None = None,
    acquired_at: datetime | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        analysis_id=analysis_id,
        candidate_id=candidate_id,
        dataset_id=dataset_id,
        title=title,
        source_url=source_url,
        citation=citation,
        license=license,
        processing_level=processing_level,
        spatial_resolution_m=spatial_resolution_m,
        acquisition_datetime=acquired_at,
        uncertainty=uncertainty,
        details=_jsonable(details),
    )


def _progress_factory(db: Session, analysis: AnalysisExecution, job: ProcessingJob):
    def progress(value: float, stage: str, message: str) -> None:
        db.refresh(job)
        db.refresh(analysis)
        if job.cancel_requested or analysis.status in {"cancelling", "cancelled"}:
            if analysis.status != "cancelled":
                analysis.status = "cancelled"
                analysis.stage = "cancelled"
                analysis.error_message = "Cancelled by user request."
                job.status = "cancelled"
                job.finished_at = _now()
                db.add(
                    JobEvent(
                        job_id=job.id,
                        level="warning",
                        stage="cancelled",
                        progress=round(float(analysis.progress), 6),
                        message="Job cancelled cooperatively between processing stages.",
                        data={},
                    )
                )
                db.commit()
            raise JobCancelled("cancelled")
        value = max(0.0, min(1.0, float(value)))
        analysis.status = "running"
        analysis.stage = stage
        analysis.progress = value
        job.status = "running"
        job.stage = stage
        job.progress = value
        db.add(
            JobEvent(
                job_id=job.id,
                level="info",
                stage=stage,
                progress=value,
                message=message,
                data={},
            )
        )
        db.commit()

    return progress


def _reference_evidence(
    db: Session,
    analysis_id: uuid.UUID,
    region: PlanetaryRegion,
    dem: ReferenceDEM,
    footprint_km: float,
    preview_url: str | None,
) -> None:
    db.add(
        _evidence(
            analysis_id=analysis_id,
            candidate_id=None,
            dataset_id=dem.dataset_id,
            title=f"{region.name}: reference topography",
            source_url=dem.source_url,
            citation=dem.citation,
            license=dataset_map().get(dem.dataset_id, {}).get("license", "Public domain (NASA)"),
            processing_level=dataset_map().get(dem.dataset_id, {}).get("processing_level"),
            spatial_resolution_m=dem.native_resolution_m,
            uncertainty=dem.vertical_uncertainty_m,
            details={
                "role": "reference",
                "body_id": region.body_id,
                "center": [region.center_lon, region.center_lat],
                "footprint_km": footprint_km,
                "product_id": dem.product_id,
                "native_resolution_m": dem.native_resolution_m,
                "vertical_uncertainty_m": dem.vertical_uncertainty_m,
                "analysis_resolution_m": dem.window.resolution_m,
                "valid_fraction": round(dem.window.valid_fraction, 6),
                "method": dem.method,
            },
        )
    )
    if preview_url:
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=None,
                dataset_id="lro_wac_mosaic" if region.body_id == "moon" else "mars_viking_mdim21",
                title=f"{region.name}: NASA imagery preview",
                source_url=(
                    dataset_map()["lro_wac_mosaic"]["source_url"]
                    if region.body_id == "moon"
                    else dataset_map()["mars_viking_mdim21"]["source_url"]
                ),
                citation=(
                    "LRO WAC global mosaic v02, NASA/GSFC/ASU, NASA TREK."
                    if region.body_id == "moon"
                    else "Mars Viking MDIM21 color mosaic, NASA/JPL/USGS, NASA TREK."
                ),
                license="Public domain (NASA)",
                processing_level="L3 imagery",
                details={"role": "preview", "body_id": region.body_id, "preview_url": preview_url},
            )
        )
    context = albedo.body_albedo(
        region.body_id, region.center_lon, region.center_lat, footprint_km
    )
    if context:
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=None,
                dataset_id=context["dataset_id"],
                title=context["title"],
                source_url=context["source_url"],
                citation=context["citation"],
                license="Public domain (NASA)",
                spatial_resolution_m=(dataset_map().get(context["dataset_id"], {}) or {}).get(
                    "spatial_resolution_m"
                ),
                details={
                    "role": "surface context (not scored)",
                    "unit": context["unit"],
                    "statistics": context["statistics"],
                    "caveat": context["caveat"],
                },
            )
        )


PC_DEM_CROSSCHECKS = (
    ("nasadem", "nasadem_hgt_v001", "NASADEM"),
    ("alos-dem", "alos_world3d_30m", "ALOS World 3D-30m"),
)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _add_optical_evidence(
    db: Session,
    *,
    analysis_id: uuid.UUID,
    candidate_id: uuid.UUID,
    dataset_id: str,
    title: str,
    scenes: dict[str, Any],
    citation: str,
    license: str,
    processing_level: str,
    resolution_m: float,
) -> bool:
    items = scenes.get("items") or []
    if not items:
        return False
    best = items[0]
    db.add(
        _evidence(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            dataset_id=dataset_id,
            title=title,
            source_url=best.get("stac_item_url") or scenes.get("source"),
            citation=citation,
            license=license,
            processing_level=processing_level,
            spatial_resolution_m=resolution_m,
            acquired_at=_parse_iso(best.get("datetime")),
            details={
                "role": "optical context (not scored)",
                "scene_id": best.get("id"),
                "cloud_cover": best.get("cloud_cover"),
                "platform": best.get("platform"),
                "preview_url": best.get("preview_url"),
                "stac_item_url": best.get("stac_item_url"),
                "scene_count": len(items),
                "items": items,
                "query": scenes.get("query"),
                "caveat": (
                    "Scene metadata and unsigned preview only; bulk imagery is not downloaded."
                ),
            },
        )
    )
    return True


def _candidate_context(
    db: Session,
    *,
    analysis_id: uuid.UUID,
    candidate_id: uuid.UUID,
    lon: float,
    lat: float,
    footprint_km: float,
    reference_dem: EarthDEM,
    start_date,
    end_date,
    include_stac: bool,
) -> tuple[dict[str, Any], list[str], set[str]]:
    """Collect optional real evidence for one candidate. Failures are recorded, not faked."""
    context: dict[str, Any] = {}
    errors: list[str] = []
    used: set[str] = set()
    bbox = footprint_bbox(lon, lat, footprint_km, EARTH_RADIUS_KM)[:4]

    try:
        cover = worldcover.class_fractions(*bbox)
        context["worldcover"] = cover
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dataset_id="esa_worldcover_2021",
                title=f"ESA WorldCover context at {cover['dominant']}",
                source_url="https://esa-worldcover.org/en",
                citation="Zanaga et al. (2022), ESA WorldCover 10 m 2021 v200, doi:10.5281/zenodo.7254221.",
                license="CC BY 4.0",
                processing_level="L4",
                spatial_resolution_m=10.0,
                details={"role": "land-cover context (not scored)", **cover},
            )
        )
        used.add("esa_worldcover_2021")
    except Exception as exc:
        context["worldcover"] = {"available": False, "error": str(exc)[:300]}
        errors.append("WorldCover land cover unavailable for this candidate.")

    try:
        lulc = pc.lulc_fractions(
            "io-lulc-annual-v02",
            lon,
            lat,
            footprint_km,
            prefer_year=getattr(end_date, "year", None),
        )
        if context.get("worldcover", {}).get("class_codes"):
            lulc["worldcover_agreement"] = pc.landcover_agreement(context["worldcover"], lulc)
        context["io_lulc"] = lulc
        spec = dataset_map()["io_lulc_annual_v02"]
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dataset_id="io_lulc_annual_v02",
                title=f"Planetary Computer IO LULC {lulc['year']} at {lulc['dominant']}",
                source_url=spec["source_url"],
                citation=spec["citation"],
                license=spec["license"],
                processing_level=spec["processing_level"],
                spatial_resolution_m=spec["spatial_resolution_m"],
                details={"role": "land-cover cross-check (not scored)", **lulc},
            )
        )
        used.add("io_lulc_annual_v02")
    except Exception as exc:
        context["io_lulc"] = {"available": False, "error": str(exc)[:300]}
        errors.append("Planetary Computer IO LULC unavailable for this candidate.")

    for collection, dataset_id, label in PC_DEM_CROSSCHECKS:
        key = f"pc_dem_{dataset_id}"
        try:
            stats = pc.dem_crosscheck(
                collection,
                lon,
                lat,
                footprint_km,
                reference_dem.window,
                reference_dataset_id=reference_dem.dataset_id,
                dataset_id=dataset_id,
            )
            context[key] = stats
            spec = dataset_map()[dataset_id]
            db.add(
                _evidence(
                    analysis_id=analysis_id,
                    candidate_id=candidate_id,
                    dataset_id=dataset_id,
                    title=f"{label}: independent DEM cross-check",
                    source_url=stats["source_url"],
                    citation=spec["citation"],
                    license=spec["license"],
                    processing_level=spec["processing_level"],
                    spatial_resolution_m=spec["spatial_resolution_m"],
                    details={"role": "independent DEM cross-check (not scored)", **stats},
                )
            )
            used.add(dataset_id)
        except Exception as exc:
            context[key] = {"available": False, "error": str(exc)[:300]}
            errors.append(f"{label} DEM cross-check unavailable for this candidate.")

    if include_stac:
        pc_scenes: dict[str, Any] | None = None
        try:
            pc_scenes = pc.optical_context(
                "sentinel-2-l2a",
                lon,
                lat,
                footprint_km,
                start=start_date,
                end=end_date,
                limit=3,
                cloud_lt=45.0,
            )
            context["pc_sentinel2"] = pc_scenes
            if _add_optical_evidence(
                db,
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dataset_id="pc_sentinel2_l2a",
                title="Planetary Computer Sentinel-2 L2A scene context",
                scenes=pc_scenes,
                citation=(
                    "Sentinel-2 L2A, ESA Copernicus; Microsoft Planetary Computer "
                    "STAC and SAS."
                ),
                license="CC BY 4.0",
                processing_level="L2A",
                resolution_m=10.0,
            ):
                used.add("pc_sentinel2_l2a")
        except Exception as exc:
            context["pc_sentinel2"] = {"available": False, "error": str(exc)[:300]}
            errors.append("Planetary Computer Sentinel-2 context unavailable for this candidate.")

        if not context.get("pc_sentinel2", {}).get("items"):
            # Earth Search remains a documented fallback if the Planetary Computer query has
            # no scenes or is unavailable.
            try:
                scenes = stac.search_sentinel2(
                    *bbox,
                    start=start_date,
                    end=end_date,
                    limit=3,
                    max_cloud=30.0,
                )
                context["sentinel2"] = scenes
                if scenes["items"] and _add_optical_evidence(
                    db,
                    analysis_id=analysis_id,
                    candidate_id=candidate_id,
                    dataset_id="sentinel2_l2a",
                    title="Sentinel-2 L2A scene context (Earth Search fallback)",
                    scenes=scenes,
                    citation=(
                        "Sentinel-2 L2A, ESA Copernicus; Earth Search v1 by Element 84 "
                        "on AWS Open Data."
                    ),
                    license="CC BY 4.0",
                    processing_level="L2A",
                    resolution_m=10.0,
                ):
                    used.add("sentinel2_l2a")
            except Exception as exc:
                context["sentinel2"] = {"available": False, "error": str(exc)[:300]}
                errors.append("Sentinel-2 STAC context unavailable for this candidate.")

        try:
            landsat = pc.optical_context(
                "landsat-c2-l2",
                lon,
                lat,
                footprint_km,
                start=start_date,
                end=end_date,
                limit=3,
                cloud_lt=50.0,
            )
            context["pc_landsat"] = landsat
            if _add_optical_evidence(
                db,
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dataset_id="pc_landsat_c2_l2",
                title="Planetary Computer Landsat Collection 2 L2 scene context",
                scenes=landsat,
                citation="Landsat Collection 2 Level-2, NASA/USGS; Microsoft Planetary Computer.",
                license="Public domain (USGS/NASA)",
                processing_level="L2",
                resolution_m=30.0,
            ):
                used.add("pc_landsat_c2_l2")
        except Exception as exc:
            context["pc_landsat"] = {"available": False, "error": str(exc)[:300]}
            errors.append("Planetary Computer Landsat context unavailable for this candidate.")

    try:
        fire = firms.active_fires(*bbox, days=2)
        context["firms"] = fire
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dataset_id="firms_viirs",
                title="NASA FIRMS active-fire context",
                source_url="https://firms.modaps.eosdis.nasa.gov/api/",
                citation="NASA FIRMS VIIRS 375 m active fire product.",
                license="Public domain (NASA)",
                processing_level="L2",
                spatial_resolution_m=375.0,
                details={"role": "thermal-anomaly context (not scored)", "count": fire["count"]},
            )
        )
        used.add("firms_viirs")
    except Exception as exc:
        context["firms"] = {"available": False, "error": str(exc)[:200]}
        # A missing FIRMS key is an expected configuration state, not a data defect.

    if settings.meteomatics_available:
        try:
            weather = meteomatics.forecast(lat, lon)
            context["meteomatics"] = weather
            spec = dataset_map()["meteomatics_weather"]
            db.add(
                _evidence(
                    analysis_id=analysis_id,
                    candidate_id=candidate_id,
                    dataset_id="meteomatics_weather",
                    title="Meteomatics operational weather context",
                    source_url=spec["source_url"],
                    citation=spec["citation"],
                    license=spec["license"],
                    processing_level=spec["processing_level"],
                    spatial_resolution_m=spec["spatial_resolution_m"],
                    acquired_at=_parse_iso(weather["window"]["start"]),
                    details={
                        "role": "operational weather context (not scored)",
                        **weather,
                    },
                )
            )
            used.add("meteomatics_weather")
        except Exception as exc:
            context["meteomatics"] = {"available": False, "error": str(exc)[:300]}
            errors.append("Meteomatics weather context unavailable for this candidate.")
    else:
        # Credentials are optional, like FIRMS. Their absence is a configuration state,
        # not a data defect, so it is recorded without adding a context error.
        context["meteomatics"] = {
            "available": False,
            "reason": "METEOMATICS_USERNAME and METEOMATICS_PASSWORD are not configured",
        }

    try:
        prev = preview.region_preview("earth", lon, lat, footprint_km)
        context["preview"] = {
            "url": prev["url"],
            "source": prev["source"],
            "attribution": prev["attribution"],
        }
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dataset_id="nasa_gibs",
                title="NASA GIBS imagery preview",
                source_url="https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi",
                citation="NASA Global Imagery Browse Services (GIBS), NASA EOSDIS.",
                license="Public domain (NASA)",
                spatial_resolution_m=250.0,
                details={
                    "role": "preview",
                    "preview_url": prev["url"],
                    "bbox": prev["bbox"],
                    "attribution": prev["attribution"],
                },
            )
        )
        used.add("nasa_gibs")
    except Exception as exc:
        context["preview"] = {"available": False, "error": str(exc)[:300]}
        errors.append("GIBS preview unavailable for this candidate.")

    return context, errors, used


def _persist_scores(
    db: Session,
    analysis_id: uuid.UUID,
    candidate_id: uuid.UUID,
    rank: int,
    score: CandidateScore,
) -> CandidateRanking:
    for dim, detail in score.dimension_scores.items():
        methods = detail.get("methods", {})
        for method, value in methods.items():
            if value is None:
                continue
            db.add(
                SimilarityScore(
                    analysis_id=analysis_id,
                    candidate_id=candidate_id,
                    dimension=dim,
                    method=method,
                    score=float(value),
                    coverage=float(detail.get("coverage") or 0.0),
                    weight=float(detail.get("weight_used") or 0.0),
                    details={
                        "label": detail.get("label"),
                        "features_used": detail.get("features_used"),
                        "features_missing": detail.get("features_missing"),
                        "features": detail.get("features") if method == "mcda" else [],
                    },
                )
            )
    for method, value in score.method_scores.items():
        if value is None:
            continue
        db.add(
            SimilarityScore(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                dimension="overall",
                method=method,
                score=float(value),
                coverage=score.evidence_coverage,
                weight=1.0,
                details={"primary": method == "mcda"},
            )
        )
    ranking = CandidateRanking(
        analysis_id=analysis_id,
        candidate_id=candidate_id,
        rank=rank,
        overall_score=score.overall_score,
        confidence=score.confidence,
        confidence_band=score.confidence_band,
        evidence_coverage=score.evidence_coverage,
        dimension_scores=_jsonable(score.dimension_scores),
        method_scores=_jsonable(score.method_scores),
        missing_dimensions=_jsonable(score.missing_dimensions),
        strongest_dimensions=_jsonable(score.strongest_dimensions),
        weakest_dimensions=_jsonable(score.weakest_dimensions),
        limitations=_jsonable(score.limitations),
    )
    db.add(ranking)
    return ranking


def _limitations(body_id: str, reference: TerrainProfile, screening: dict[str, Any]) -> list[str]:
    limitations = [
        "Similarity is a bounded heuristic derived from DEM morphometry, not a probability and not a landing-safety assessment.",
        "Absolute elevation is not compared across bodies. Only relative relief, slope, curvature, texture, and distribution shape are scored.",
        "Surface albedo, thermal, water/ice, aeolian, and environmental dimensions are evidence-only in this run because no cross-calibrated product pair is attached.",
        "The coarse screening stage uses an aggregated Earth terrain mosaic and can miss small analogues; final scores always use refined Copernicus windows.",
        "The operational dimension is a research trafficability proxy, not rover or crew certification.",
    ]
    if reference.valid_fraction < 0.8:
        limitations.append("Part of the reference footprint has no valid DEM data.")
    if body_id == "mars":
        limitations.append("Mars reference topography uses the 463 m MOLA mosaic; 500 m texture is partly interpolated.")
    else:
        limitations.append("Lunar reference topography uses the 128 ppd LOLA grid (~237 m); 500 m texture is partly interpolated.")
    limitations.extend(screening.get("limitation", "").split(". ") if screening.get("limitation") else [])
    return limitations


def run_analysis(analysis_id: uuid.UUID, job_id: uuid.UUID, db: Session) -> dict[str, Any]:
    analysis = db.get(AnalysisExecution, analysis_id)
    job = db.get(ProcessingJob, job_id)
    if analysis is None or job is None:
        raise ValueError("analysis or job record not found")
    region = db.get(PlanetaryRegion, analysis.reference_region_id)
    if region is None:
        raise ValueError("reference region not found")
    if region.body_id not in RADII:
        raise ValueError("reference region must be on the Moon or Mars")

    cfg = analysis.config or {}
    footprint_km = float(cfg.get("footprint_km") or settings.default_footprint_km)
    resolution_m = float(cfg.get("analysis_resolution_m") or settings.default_analysis_resolution_m)
    dimensions = list(cfg.get("dimensions") or SCORED_DIMENSIONS)
    weights = {
        d: float(v)
        for d, v in (cfg.get("weights") or {}).items()
        if d in dimensions
    }
    methods = list(cfg.get("methods") or ["mcda", "weighted_euclidean", "standardized_euclidean", "cosine", "wasserstein"])
    min_coverage = float(cfg.get("min_feature_coverage") or settings.default_min_feature_coverage)
    policy = cfg.get("missing_data_policy") or "penalize"
    search = dict(cfg.get("search") or {})

    analysis.status = "running"
    analysis.stage = "initialising"
    analysis.progress = 0.0
    analysis.started_at = analysis.started_at or _now()
    analysis.error_message = None
    job.stage = "initialising"
    db.commit()
    progress = _progress_factory(db, analysis, job)

    # A retry must not append duplicate candidates or scores.
    for model in (SimilarityScore, CandidateRanking, FeatureMeasurement, EvidenceRecord):
        db.execute(delete(model).where(model.analysis_id == analysis_id))
    db.execute(delete(EarthCandidate).where(EarthCandidate.analysis_id == analysis_id))
    db.commit()

    progress(0.02, "reference_topo", f"Acquiring {region.body_id} reference topography")
    ref_dem = load_reference_dem(region.body_id, region.center_lon, region.center_lat, footprint_km, resolution_m)
    progress(0.10, "reference_features", "Extracting the common-grid reference feature vector")
    ref_profile = extract_terrain_profile(
        ref_dem.window,
        native_resolution_m=ref_dem.native_resolution_m,
        vertical_uncertainty_m=ref_dem.vertical_uncertainty_m,
        product_method=ref_dem.method,
    )
    db.add_all(
        _feature_rows(
            analysis_id=analysis_id,
            subject_type="reference",
            subject_id=region.id,
            body_id=region.body_id,
            profile=ref_profile,
            dataset_id=ref_dem.dataset_id,
            product_id=ref_dem.product_id,
            source_url=ref_dem.source_url,
            citation=ref_dem.citation,
            analysis_resolution_m=resolution_m,
        )
    )
    db.commit()

    try:
        ref_preview = preview.region_preview(
            region.body_id, region.center_lon, region.center_lat, footprint_km
        )
        ref_preview_url = ref_preview["url"]
    except Exception:
        ref_preview_url = None
    _reference_evidence(db, analysis_id, region, ref_dem, footprint_km, ref_preview_url)
    db.commit()

    progress(0.15, "discovery", "Starting real-data Earth screening and refinement")

    def discovery_progress(p: float, stage: str, message: str) -> None:
        progress(0.15 + 0.62 * max(0.0, min(1.0, p)), stage, message)

    result: DiscoveryResult = discover(
        ref_profile,
        ref_dem.window,
        body_id=region.body_id,
        search_config=search,
        footprint_km=footprint_km,
        analysis_resolution_m=resolution_m,
        dimension_weights=weights,
        methods=methods,
        min_feature_coverage=min_coverage,
        missing_data_policy=policy,
        progress=discovery_progress,
    )

    progress(0.79, "persistence", f"Persisting {len(result.scores)} scored candidate(s) and provenance")
    score_by_id = {s.candidate_id: s for s in result.scores}
    candidate_by_id = {c.candidate_id: c for c in result.candidates}
    ordered = [score_by_id[c.candidate_id] for c in result.candidates if c.candidate_id in score_by_id]
    ordered.sort(key=lambda s: (-s.overall_score, -s.confidence))

    include_stac = True
    start_date = cfg.get("temporal_start")
    end_date = cfg.get("temporal_end")
    from datetime import date as _date

    if start_date:
        start_date = _date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    if end_date:
        end_date = _date.fromisoformat(end_date) if isinstance(end_date, str) else end_date

    candidate_summaries: list[dict[str, Any]] = []
    persisted_scores: list[CandidateScore] = []
    context_errors: list[str] = []
    used_context: set[str] = set()
    for index, score in enumerate(ordered):
        candidate = candidate_by_id.get(score.candidate_id)
        if candidate is None:
            continue
        lon, lat = candidate.lon, candidate.lat
        display_name, country, admin = nominatim.describe(lat, lon)
        if country or admin:
            used_context.add("nominatim_osm")
        metadata = {
            "body_id": "earth",
            "coarse_screen_score": candidate.coarse_score,
            "coarse_screen": candidate.score.feature_deltas if candidate.score else [],
            "search_bbox": search.get("bbox"),
            "grid_cell_deg": search.get("cell_size_deg"),
            "refinement_dataset": candidate.dem.dataset_id,
            "refinement_method": candidate.dem.method,
            "gazetteer": "OpenStreetMap Nominatim (cached)",
        }
        row = EarthCandidate(
            analysis_id=analysis_id,
            name=display_name,
            display_name=display_name,
            country=country,
            admin_region=admin,
            center_lon=lon,
            center_lat=lat,
            size_km=footprint_km,
            geometry=_polygon_ewkt("earth", lon, lat, footprint_km),
            cluster_id=None,
            screen_score=candidate.coarse_score,
            elevation_mean=candidate.profile.elevation_mean,
            metadata_json=_jsonable(metadata),
        )
        db.add(row)
        db.flush()

        db.add_all(
            _feature_rows(
                analysis_id=analysis_id,
                subject_type="candidate",
                subject_id=row.id,
                body_id="earth",
                profile=candidate.profile,
                dataset_id=candidate.dem.dataset_id,
                product_id=candidate.dem.product_id,
                source_url=candidate.dem.source_url,
                citation=candidate.dem.citation,
                analysis_resolution_m=resolution_m,
            )
        )
        db.add(
            _evidence(
                analysis_id=analysis_id,
                candidate_id=row.id,
                dataset_id=candidate.dem.dataset_id,
                title=f"{display_name}: refined Earth topography",
                source_url=candidate.dem.source_url,
                citation=candidate.dem.citation,
                license=candidate.dem.license,
                processing_level=dataset_map().get(candidate.dem.dataset_id, {}).get("processing_level"),
                spatial_resolution_m=candidate.dem.native_resolution_m,
                uncertainty=candidate.dem.vertical_uncertainty_m,
                details={
                    "role": "candidate topography (scoring input)",
                    "product_id": candidate.dem.product_id,
                    "center": [lon, lat],
                    "footprint_km": footprint_km,
                    "analysis_resolution_m": resolution_m,
                    "native_resolution_m": candidate.dem.native_resolution_m,
                    "valid_fraction": round(candidate.dem.window.valid_fraction, 6),
                    "method": candidate.dem.method,
                    "coarse_screen_score": candidate.coarse_score,
                },
            )
        )
        ctx, errors, used = _candidate_context(
            db,
            analysis_id=analysis_id,
            candidate_id=row.id,
            lon=lon,
            lat=lat,
            footprint_km=footprint_km,
            reference_dem=candidate.dem,
            start_date=start_date,
            end_date=end_date,
            include_stac=include_stac,
        )
        metadata["context"] = ctx
        row.metadata_json = _jsonable(metadata)
        context_errors.extend(errors)
        used_context.update(used)

        ranking = _persist_scores(db, analysis_id, row.id, index + 1, score)
        persisted_scores.append(score)
        db.commit()

        candidate_summaries.append(
            {
                "rank": ranking.rank,
                "candidate_id": str(row.id),
                "display_name": display_name,
                "lon": round(lon, 5),
                "lat": round(lat, 5),
                "overall_score": round(score.overall_score, 6),
                "confidence": round(score.confidence, 6),
                "confidence_band": score.confidence_band,
                "evidence_coverage": round(score.evidence_coverage, 6),
            }
        )
        progress(
            0.79 + 0.17 * (index + 1) / max(1, len(ordered)),
            "persistence",
            f"Persisted candidate {index + 1}/{len(ordered)} with evidence and scores",
        )

    if not candidate_summaries:
        raise RuntimeError("Discovery completed but no candidate could be persisted")

    manifest = {
        "catalog_version": CATALOG_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "generated_at": _now().isoformat(),
        "reference": {
            "region_id": str(region.id),
            "name": region.name,
            "body_id": region.body_id,
            "center_lon": region.center_lon,
            "center_lat": region.center_lat,
            "footprint_km": footprint_km,
            "analysis_resolution_m": resolution_m,
            "source": region.source,
            "source_url": region.source_url,
            "citation": region.citation,
            "dem": _dem_manifest(ref_dem),
            "preview_url": ref_preview_url,
        },
        "search": {
            "bbox": search.get("bbox"),
            "cell_size_deg": search.get("cell_size_deg"),
            "screening_resolution_km": search.get("screening_resolution_km"),
            "refine_top_n": search.get("refine_top_n"),
            "max_candidates": search.get("max_candidates"),
            "cluster_radius_km": search.get("cluster_radius_km"),
            "exclude_polar_gt_deg": search.get("exclude_polar_gt_deg"),
        },
        "screening": result.screening,
        "refinement": result.refinement,
        "failures": result.failures,
        "dimensions": {
            "scored": dimensions,
            "evidence_only": ["surface", "thermal", "water_ice", "aeolian", "environment"],
            "weights": weights,
            "descriptions": {d: DIMENSION_DESCRIPTIONS[d] for d in dimensions if d in DIMENSION_DESCRIPTIONS},
            "within_dimension_weights": dimension_weights(),
        },
        "similarity": {
            "primary": "mcda",
            "methods": methods,
            "kernel": "exp(-0.5 * (delta/tolerance)^2)",
            "tolerances": {k: v["tolerance"] for k, v in FEATURE_SPECS.items() if v["weight"] > 0},
            "missing_data_policy": policy,
            "min_feature_coverage": min_coverage,
            "note": "Similarity is not a probability. Missing dimensions are excluded, never imputed.",
        },
        "datasets": [
            {
                "dataset_id": ref_dem.dataset_id,
                "role": "reference DEM",
                "product_id": ref_dem.product_id,
                "source_url": ref_dem.source_url,
                "citation": ref_dem.citation,
                "native_resolution_m": ref_dem.native_resolution_m,
            },
            *[
                {
                    "dataset_id": ds,
                    "role": "refined Earth DEM",
                    "source_url": dataset_map().get(ds, {}).get("source_url"),
                    "citation": dataset_map().get(ds, {}).get("citation"),
                    "native_resolution_m": dataset_map().get(ds, {}).get("spatial_resolution_m"),
                }
                for ds in sorted({c.dem.dataset_id for c in result.candidates})
            ],
            *[
                {
                    "dataset_id": ds,
                    "role": "context/evidence only",
                    "source_url": dataset_map().get(ds, {}).get("source_url"),
                    "citation": dataset_map().get(ds, {}).get("citation"),
                }
                for ds in sorted(used_context)
            ],
        ],
        "limitations": _limitations(region.body_id, ref_profile, result.screening),
        "context_errors": sorted(set(context_errors)),
        "reproducibility": {
            "analysis_id": str(analysis_id),
            "job_id": str(job_id),
            "algorithm_version": ALGORITHM_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "config_sha256": _sha256(analysis.config),
            "manifest_sha256": _sha256(
                {
                    "reference": [region.center_lon, region.center_lat, footprint_km, resolution_m],
                    "search": search,
                    "weights": weights,
                    "methods": methods,
                }
            ),
            "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
        },
    }

    agreement = method_summary(persisted_scores, methods)
    bands: dict[str, int] = {}
    for c in candidate_summaries:
        bands[c["confidence_band"]] = bands.get(c["confidence_band"], 0) + 1
    summary = {
        "candidate_count": len(candidate_summaries),
        "top_candidates": candidate_summaries[:5],
        "confidence_bands": bands,
        "mean_confidence": round(
            sum(c["confidence"] for c in candidate_summaries) / len(candidate_summaries), 6
        ),
        "method_agreement": agreement,
        "screening": {
            "coarse_samples": result.screening.get("coarse_samples"),
            "refinement_sites": result.screening.get("refinement_sites"),
            "resolution_m": result.screening.get("resolution_m"),
        },
        "refinement": result.refinement,
        "context_errors": sorted(set(context_errors)),
        "limitations": manifest["limitations"],
    }

    analysis.dataset_manifest = _jsonable(manifest)
    analysis.result_summary = _jsonable(summary)
    analysis.status = "succeeded"
    analysis.stage = "complete"
    analysis.progress = 1.0
    analysis.completed_at = _now()
    job.status = "succeeded"
    job.stage = "complete"
    job.progress = 1.0
    job.result = {
        "analysis_id": str(analysis_id),
        "candidate_count": len(candidate_summaries),
        "top_candidate": candidate_summaries[0] if candidate_summaries else None,
    }
    job.finished_at = _now()
    db.add(
        JobEvent(
            job_id=job.id,
            level="info",
            stage="complete",
            progress=1.0,
            message=f"Analysis complete: {len(candidate_summaries)} ranked candidates",
            data={"summary": summary["top_candidates"]},
        )
    )
    db.commit()
    return summary


__all__ = [
    "ALGORITHM_VERSION",
    "FEATURE_SCHEMA_VERSION",
    "CATALOG_VERSION",
    "JobCancelled",
    "run_analysis",
]
