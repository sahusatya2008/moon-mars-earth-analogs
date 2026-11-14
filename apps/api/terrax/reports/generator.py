"""Report generation from persisted analysis records.

Formats: PDF (human), CSV (rankings), GeoJSON (spatial), JSON (complete machine record), and
manifest (dataset/provenance record). Files are written under a controlled report directory and
hashed with SHA-256.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AnalysisExecution,
    EvidenceRecord,
    Report,
)
from ..results import analysis_results, evidence_dict

settings = get_settings()

EXTENSIONS = {
    "pdf": "pdf",
    "csv": "csv",
    "geojson": "geojson",
    "json": "json",
    "manifest": "manifest.json",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(analysis_id: uuid.UUID, report_id: uuid.UUID, fmt: str) -> str:
    return f"terrax-{str(analysis_id)[:8]}-{str(report_id)[:8]}.{EXTENSIONS[fmt]}"


def _ascii(value: Any) -> str:
    text = (
        str(value)
        .replace("–", "-")
        .replace("—", "-")
        .replace("°", " deg ")
        .replace("×", "x")
    )
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _plain(value: Any) -> str:
    return str(value).replace("–", "-").replace("—", "-").replace("°", " deg ").replace("×", "x")


def _json_payload(db: Session, analysis: AnalysisExecution) -> dict[str, Any]:
    payload = analysis_results(db, analysis)
    evidence = db.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.analysis_id == analysis.id)
        .order_by(EvidenceRecord.dataset_id, EvidenceRecord.title)
    ).all()
    payload["evidence"] = [evidence_dict(row) for row in evidence]
    payload["generated_at"] = _now().isoformat()
    payload["report_format_version"] = "report-0.2.0"
    return payload


def _csv_bytes(payload: dict[str, Any]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    dims = ["geomorphology", "morphometry", "texture", "operational"]
    methods = ["mcda", "weighted_euclidean", "standardized_euclidean", "cosine", "mahalanobis", "wasserstein", "percentile"]
    writer.writerow(
        [
            "rank",
            "candidate_id",
            "display_name",
            "country",
            "admin_region",
            "center_lon",
            "center_lat",
            "size_km",
            "overall_score",
            "confidence",
            "confidence_band",
            "evidence_coverage",
            *[f"dim_{d}_score" for d in dims],
            *[f"dim_{d}_coverage" for d in dims],
            *[f"method_{m}" for m in methods],
            "missing_dimensions",
            "limitations",
        ]
    )
    for row in payload["rankings"]:
        dimensions = row.get("dimension_scores") or {}
        scores = row.get("method_scores") or {}
        missing = "; ".join(
            f"{m.get('dimension')}: {m.get('reason', 'unavailable')}"
            for m in (row.get("missing_dimensions") or [])
        )
        writer.writerow(
            [
                row["rank"],
                row["candidate_id"],
                row["display_name"],
                row.get("country") or "",
                row.get("admin_region") or "",
                row["center_lon"],
                row["center_lat"],
                row["size_km"],
                row["overall_score"],
                row["confidence"],
                row["confidence_band"],
                row["evidence_coverage"],
                *[(dimensions.get(d) or {}).get("score") for d in dims],
                *[(dimensions.get(d) or {}).get("coverage") for d in dims],
                *[scores.get(m) for m in methods],
                missing,
                " | ".join(row.get("limitations") or []),
            ]
        )
    return buffer.getvalue().encode("utf-8")


def _geojson_spatial_reference(body_id: str) -> dict[str, Any]:
    """Describe the coordinate frame without applying an Earth CRS to another body."""
    if body_id == "earth":
        return {
            "body_fixed": False,
            "crs": "OGC:CRS84 / WGS84 longitude-latitude",
            "longitude_direction": "east",
            "latitude_direction": "north",
            "angular_units": "degrees",
        }
    return {
        "body_fixed": True,
        "crs": (
            f"body-fixed {body_id} east-longitude/latitude; "
            "no EPSG code is assigned in this export"
        ),
        "longitude_direction": "east",
        "latitude_direction": "north",
        "angular_units": "degrees",
    }


def _geojson_bytes(payload: dict[str, Any]) -> bytes:
    features = []
    region = payload["reference_region"]
    ref_geom = region.get("geometry") or {
        "type": "Point",
        "coordinates": [region["center_lon"], region["center_lat"]],
    }
    features.append(
        {
            "type": "Feature",
            "geometry": ref_geom,
            "properties": {
                "role": "reference",
                "body_id": region["body_id"],
                "name": region["name"],
                "center_lon": region["center_lon"],
                "center_lat": region["center_lat"],
                "size_km": region["size_km"],
                "source": region.get("source"),
                "citation": region.get("citation"),
                "spatial_reference": _geojson_spatial_reference(region["body_id"]),
            },
        }
    )
    for row in payload["rankings"]:
        geometry = row.get("geometry") or {
            "type": "Point",
            "coordinates": [row["center_lon"], row["center_lat"]],
        }
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "role": "candidate",
                    "body_id": "earth",
                    "candidate_id": str(row["candidate_id"]),
                    "rank": row["rank"],
                    "display_name": row["display_name"],
                    "country": row.get("country"),
                    "admin_region": row.get("admin_region"),
                    "overall_score": row["overall_score"],
                    "confidence": row["confidence"],
                    "confidence_band": row["confidence_band"],
                    "evidence_coverage": row["evidence_coverage"],
                    "algorithm_version": payload["analysis"]["algorithm_version"],
                    "feature_schema_version": payload["analysis"]["feature_schema_version"],
                    "spatial_reference": _geojson_spatial_reference("earth"),
                },
            }
        )
    collection = {
        "type": "FeatureCollection",
        "name": "SNSX SPACE analogue candidates",
        "terrax:spatial_reference": {
            "note": (
                "GeoJSON has no standard planetary CRS. No collection-wide Earth CRS is "
                "applied. Each feature declares its body and spatial_reference; Earth features "
                "use WGS84/CRS84, while Moon and Mars features use body-fixed east-longitude, "
                "north-latitude degrees identified by body_id."
            )
        },
        "features": features,
    }
    return json.dumps(collection, indent=2, default=str).encode("utf-8")


def _pdf_bytes(payload: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="SnsxTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#0b3b46"),
            alignment=TA_LEFT,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SnsxH2",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#0b3b46"),
            spaceBefore=10,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SnsxBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SnsxSmall",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#43555e"),
        )
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#66777f"))
        canvas.drawString(
            18 * mm,
            10 * mm,
            "SNSX SPACE - planetary terrain analogue intelligence - not a landing-safety assessment",
        )
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=_plain(payload["analysis"]["title"]),
        author="SNSX SPACE",
        subject="Planetary analogue analysis",
    )
    story: list[Any] = []
    analysis = payload["analysis"]
    region = payload["reference_region"]
    story.append(Paragraph("SNSX SPACE analogue analysis", styles["SnsxTitle"]))
    story.append(Paragraph(_ascii(analysis["title"]), styles["SnsxH2"]))
    story.append(
        Paragraph(
            f"Analysis <b>{analysis['id']}</b> | reference <b>{_ascii(region['name'])}</b> "
            f"({region['body_id']}) | algorithm {analysis['algorithm_version']} | "
            f"feature schema {analysis['feature_schema_version']}",
            styles["SnsxSmall"],
        )
    )
    story.append(Spacer(1, 5))
    story.append(
        Paragraph(
            "This report compares measured terrain properties of a lunar or Martian reference "
            "region with measured Earth terrain. Similarity is a bounded, configuration-dependent "
            "heuristic, not a probability. Confidence is reported separately from similarity. "
            "Missing dimensions are excluded and listed; they are never imputed as perfect matches.",
            styles["SnsxBody"],
        )
    )

    summary = analysis.get("result_summary") or {}
    story.append(Paragraph("Run summary", styles["SnsxH2"]))
    story.append(
        Paragraph(
            f"Ranked candidates: <b>{summary.get('candidate_count', len(payload['rankings']))}</b>. "
            f"Mean evidence confidence: <b>{summary.get('mean_confidence', 'n/a')}</b>. "
            f"Footprint: {region['size_km']} km. "
            f"Search bbox: {payload.get('dataset_manifest', {}).get('search', {}).get('bbox')}.",
            styles["SnsxBody"],
        )
    )

    story.append(Paragraph("Reference region", styles["SnsxH2"]))
    story.append(
        Paragraph(
            f"{_ascii(region['name'])} at {region['center_lon']:.4f}, {region['center_lat']:.4f} "
            f"on {region['body_id']}. {_ascii(region.get('description') or '')}",
            styles["SnsxBody"],
        )
    )
    dem = (payload.get("dataset_manifest", {}).get("reference") or {}).get("dem") or {}
    story.append(
        Paragraph(
            f"Reference DEM: {_ascii(dem.get('title', 'n/a'))}; product {_ascii(dem.get('product_id', 'n/a'))}; "
            f"native resolution {dem.get('native_resolution_m', 'n/a')} m; vertical uncertainty "
            f"{dem.get('vertical_uncertainty_m', 'n/a')} m. {_ascii(dem.get('citation', ''))}",
            styles["SnsxSmall"],
        )
    )

    story.append(Paragraph("Rankings", styles["SnsxH2"]))
    table_data = [["#", "Candidate", "Score", "Conf.", "Band", "Coverage", "Lon", "Lat"]]
    for row in payload["rankings"]:
        table_data.append(
            [
                str(row["rank"]),
                _plain(row["display_name"])[:52],
                f"{row['overall_score']:.3f}",
                f"{row['confidence']:.3f}",
                row["confidence_band"],
                f"{row['evidence_coverage']:.2f}",
                f"{row['center_lon']:.3f}",
                f"{row['center_lat']:.3f}",
            ]
        )
    table = Table(table_data, repeatRows=1, colWidths=[9 * mm, 62 * mm, 15 * mm, 15 * mm, 17 * mm, 16 * mm, 18 * mm, 18 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3b46")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c3d0d5")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f6f7")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Scores are primary MCDA similarities. Confidence combines evidence coverage, feature "
            "coverage, valid pixels, source resolution, method agreement, and uncertainty.",
            styles["SnsxSmall"],
        )
    )

    top = payload["rankings"][:5]
    if top:
        story.append(PageBreak())
        story.append(Paragraph("Top candidate detail", styles["SnsxH2"]))
        for row in top:
            dims = row.get("dimension_scores") or {}
            methods = row.get("method_scores") or {}
            story.append(
                Paragraph(
                    f"{row['rank']}. {_ascii(row['display_name'])} - score {row['overall_score']:.3f}, "
                    f"confidence {row['confidence']:.3f} ({row['confidence_band']})",
                    styles["SnsxBody"],
                )
            )
            dimension_rows = [["Dimension", "Score", "Coverage", "Weight", "Features used"]]
            for dim, detail in dims.items():
                if not isinstance(detail, dict):
                    continue
                dimension_rows.append(
                    [
                        detail.get("label", dim),
                        "n/a" if detail.get("score") is None else f"{detail.get('score'):.3f}",
                        f"{detail.get('coverage', 0):.2f}",
                        f"{detail.get('weight_used', 0):.2f}",
                        str(detail.get("features_used", "")),
                    ]
                )
            dim_table = Table(
                dimension_rows,
                colWidths=[55 * mm, 25 * mm, 25 * mm, 22 * mm, 40 * mm],
                repeatRows=1,
            )
            dim_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6f0f2")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c3d0d5")),
                    ]
                )
            )
            story.append(dim_table)
            story.append(
                Paragraph(
                    "Methods: "
                    + ", ".join(
                        f"{name}={value:.3f}" if value is not None else f"{name}=n/a"
                        for name, value in methods.items()
                    ),
                    styles["SnsxSmall"],
                )
            )
            missing = row.get("missing_dimensions") or []
            if missing:
                story.append(
                    Paragraph(
                        "Unavailable dimensions: "
                        + "; ".join(
                            f"{m.get('dimension')} ({m.get('reason', 'unavailable')})" for m in missing
                        ),
                        styles["SnsxSmall"],
                    )
                )
            story.append(Spacer(1, 5))

        story.append(Paragraph("Evidence and citations", styles["SnsxH2"]))
        evidence = payload.get("evidence") or []
        by_dataset: dict[str, list[dict[str, Any]]] = {}
        for record in evidence:
            by_dataset.setdefault(record["dataset_id"], []).append(record)
        for _dataset_id, records in by_dataset.items():
            first = records[0]
            story.append(
                Paragraph(
                    f"<b>{_ascii(first['title'])}</b> ({len(records)} record(s)) - "
                    f"{_ascii(first['citation'])} Licence: {_ascii(first['license'])}.",
                    styles["SnsxBody"],
                )
            )
            urls = sorted({_ascii(r["source_url"]) for r in records})[:4]
            for url in urls:
                story.append(Paragraph(_ascii(url), styles["SnsxSmall"]))

    story.append(PageBreak())
    story.append(Paragraph("Datasets and provenance", styles["SnsxH2"]))
    manifest = payload.get("dataset_manifest") or {}
    for dataset in manifest.get("datasets", []):
        story.append(
            Paragraph(
                f"<b>{_ascii(dataset.get('dataset_id'))}</b> ({_ascii(dataset.get('role'))}) - "
                f"{_ascii(dataset.get('citation') or '')}",
                styles["SnsxSmall"],
            )
        )
    story.append(Paragraph("Reproducibility", styles["SnsxH2"]))
    repro = payload.get("reproducibility") or {}
    story.append(
        Paragraph(
            " | ".join(f"{_ascii(k)}: {_ascii(v)}" for k, v in repro.items()),
            styles["SnsxSmall"],
        )
    )
    story.append(Paragraph("Limitations", styles["SnsxH2"]))
    for limitation in payload.get("limitations") or []:
        story.append(Paragraph(f"- {_ascii(limitation)}", styles["SnsxBody"]))
    context_errors = manifest.get("context_errors") or []
    if context_errors:
        story.append(Paragraph("Context services that were unavailable", styles["SnsxH2"]))
        for error in context_errors:
            story.append(Paragraph(f"- {_ascii(error)}", styles["SnsxSmall"]))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _render(db: Session, report: Report, analysis: AnalysisExecution) -> tuple[bytes, str]:
    payload = _json_payload(db, analysis)
    if report.format == "json":
        return json.dumps(payload, indent=2, default=str).encode("utf-8"), "application/json"
    if report.format == "manifest":
        manifest = {
            "report": {
                "id": str(report.id),
                "title": report.title,
                "format": "manifest",
                "generated_at": _now().isoformat(),
                "report_format_version": "report-0.2.0",
            },
            "analysis": payload["analysis"],
            "reference_region": payload["reference_region"],
            "dataset_manifest": payload["dataset_manifest"],
            "reproducibility": payload["reproducibility"],
            "limitations": payload["limitations"],
            "candidate_ids": [str(r["candidate_id"]) for r in payload["rankings"]],
            "evidence_records": payload.get("evidence", []),
        }
        return json.dumps(manifest, indent=2, default=str).encode("utf-8"), "application/json"
    if report.format == "csv":
        return _csv_bytes(payload), "text/csv"
    if report.format == "geojson":
        return _geojson_bytes(payload), "application/geo+json"
    if report.format == "pdf":
        return _pdf_bytes(payload), "application/pdf"
    raise ValueError(f"unsupported report format {report.format!r}")


def generate_report(db: Session, report_id: uuid.UUID | str) -> dict[str, Any]:
    report = db.get(Report, uuid.UUID(str(report_id)))
    if report is None:
        raise ValueError("report not found")
    if report.status == "ready" and report.file_path and Path(report.file_path).exists():
        return {"report_id": str(report.id), "status": "ready", "cached": True}
    analysis = db.get(AnalysisExecution, report.analysis_id)
    if analysis is None:
        raise ValueError("analysis for report is missing")
    if analysis.status != "succeeded":
        raise ValueError(f"analysis is not complete (status={analysis.status})")

    report.status = "generating"
    report.error_message = None
    db.commit()
    try:
        content, media_type = _render(db, report, analysis)
        directory = settings.report_dir / str(analysis.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _safe_filename(analysis.id, report.id, report.format)
        path.write_bytes(content)
        report.file_path = str(path)
        report.size_bytes = len(content)
        report.checksum_sha256 = _hash(path)
        report.status = "ready"
        report.completed_at = _now()
        db.commit()
        return {
            "report_id": str(report.id),
            "status": "ready",
            "media_type": media_type,
            "size_bytes": len(content),
            "checksum_sha256": report.checksum_sha256,
        }
    except Exception as exc:
        report.status = "failed"
        report.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        db.commit()
        raise


def create_report(
    db: Session,
    *,
    analysis_id: uuid.UUID,
    fmt: str,
    title: str | None = None,
) -> Report:
    analysis = db.get(AnalysisExecution, analysis_id)
    if analysis is None:
        raise ValueError("analysis not found")
    if analysis.status != "succeeded":
        raise ValueError("analysis must be complete before generating a report")
    if fmt not in EXTENSIONS:
        raise ValueError(f"unsupported report format {fmt!r}")
    report = Report(
        analysis_id=analysis.id,
        format=fmt,
        title=title or f"{analysis.title} - {fmt.upper()} report",
        status="queued",
    )
    db.add(report)
    db.commit()
    return report


__all__ = ["generate_report", "create_report", "EXTENSIONS"]
