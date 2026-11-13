"""Deterministic, citation-grounded research assistant.

The default answer is assembled from persisted records only. It cannot invent a score, a place
name, or a dataset. An optional LLM can be enabled later, but the deterministic path is always
available and is what the UI labels as grounded.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AnalysisExecution,
    AssistantConversation,
    CandidateRanking,
    DatasetCollection,
    EvidenceRecord,
    PlanetaryRegion,
)
from .results import analysis_results, candidate_detail

INTENTS = {
    "why": ("why", "explain", "driving", "reason", "strongest"),
    "confidence": ("confidence", "uncertain", "reliable", "trust", "coverage"),
    "methods": ("method", "mcda", "euclidean", "cosine", "mahalanobis", "wasserstein", "agreement"),
    "sources": ("source", "data", "dataset", "citation", "provenance", "license"),
    "limitations": ("limitation", "caveat", "risk", "missing", "not", "wrong"),
    "compare": ("compare", "versus", "vs", "difference", "better"),
}


def _intent(question: str) -> str:
    q = question.lower()
    for intent, words in INTENTS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", q) for w in words):
            return intent
    return "summary"


def _citation(record: EvidenceRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, dict):
        return {
            "dataset_id": record.get("dataset_id"),
            "title": record.get("title"),
            "source_url": record.get("source_url"),
            "citation": record.get("citation"),
            "license": record.get("license"),
        }
    return {
        "dataset_id": record.dataset_id,
        "title": record.title,
        "source_url": record.source_url,
        "citation": record.citation,
        "license": record.license,
    }


def _candidate_citations(db: Session, candidate_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.candidate_id == candidate_id)
        .order_by(EvidenceRecord.dataset_id)
    ).all()
    return [_citation(row) for row in rows]


def _format_dimensions(detail: dict[str, Any]) -> str:
    parts = []
    for dim, value in (detail.get("dimension_scores") or {}).items():
        if not isinstance(value, dict):
            continue
        score = value.get("score")
        if score is None:
            continue
        parts.append(f"{value.get('label', dim)} {score:.3f} (coverage {value.get('coverage', 0):.0%})")
    return "; ".join(parts)


def _driving_features(detail: dict[str, Any]) -> str:
    strongest = detail.get("strongest_dimensions") or []
    if not strongest:
        return "No dimension level was available."
    lines = []
    for item in strongest:
        dim = item.get("dimension")
        record = (detail.get("dimension_scores") or {}).get(dim) or {}
        features = record.get("features") or []
        if not features:
            lines.append(f"{item.get('label', dim)} similarity {item.get('score')}")
            continue
        best = sorted(features, key=lambda f: -(f.get("similarity") or 0))[:3]
        pieces = []
        for feature in best:
            if feature.get("delta") is None:
                pieces.append(f"{feature.get('key')} distribution similarity {feature.get('similarity'):.2f}")
            else:
                pieces.append(
                    f"{feature.get('key')} delta {feature.get('delta'):+.3g} {feature.get('unit', '')} "
                    f"(tolerance {feature.get('tolerance'):g}, similarity {feature.get('similarity'):.2f})"
                )
        lines.append(f"{item.get('label', dim)}: " + ", ".join(pieces))
    return " ".join(lines)


def _candidate_answer(db: Session, question: str, candidate_id: uuid.UUID) -> tuple[str, list[dict], list[str]]:
    detail = candidate_detail(db, candidate_id)
    row = detail["candidate"]
    intent = _intent(question)
    limitations = list(row.get("limitations") or [])
    citations = _candidate_citations(db, candidate_id)
    head = (
        f"{row['display_name']} is ranked #{row['rank']} with similarity {row['overall_score']:.3f} "
        f"and {row['confidence_band']} evidence confidence {row['confidence']:.3f}. "
        f"It is at {row['center_lon']:.4f}, {row['center_lat']:.4f} and covers {row['size_km']:.0f} km. "
    )
    if intent == "confidence":
        low_dims = [
            k for k, v in (row.get("dimension_scores") or {}).items()
            if isinstance(v, dict) and (v.get("coverage") or 0) < 0.75
        ]
        answer = head + (
            f"Evidence coverage is {row['evidence_coverage']:.0%}. "
            + (
                "The following dimensions have incomplete feature coverage: " + ", ".join(low_dims) + ". "
                if low_dims
                else "All enabled dimensions have complete feature coverage. "
            )
            + "Confidence is a separate evidence score, not a probability that the site is a true analogue."
        )
    elif intent == "why":
        answer = head + "The strongest dimensions are: " + _driving_features(detail) + "."
    elif intent == "methods":
        methods = row.get("method_scores") or {}
        answer = head + "Method scores are " + ", ".join(
            f"{k}={v:.3f}" if v is not None else f"{k}=unavailable" for k, v in methods.items()
        ) + ". Agreement between methods is reported with the analysis, and disagreement is never hidden."
    elif intent == "sources":
        answer = head + (
            "This candidate is supported by " + ", ".join(sorted({c['dataset_id'] for c in citations if c.get('dataset_id')}))
            + ". The DEM, land-cover, imagery, gazetteer, and scene-metadata sources are listed with URLs and licences in the citations."
        )
    elif intent == "limitations":
        answer = head + "Known limitations: " + " ".join(limitations)
    else:
        answer = head + "Dimension scores: " + _format_dimensions(row) + ". "
        answer += "Strongest evidence: " + _driving_features(detail) + "."
    return answer, citations, limitations


def answer_question(
    db: Session,
    *,
    question: str,
    analysis_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    limitations: list[str] = []

    if candidate_id is not None:
        try:
            answer, citations, limitations = _candidate_answer(db, question, candidate_id)
            ranking = db.scalar(
                select(CandidateRanking).where(CandidateRanking.candidate_id == candidate_id)
            )
            analysis_id = ranking.analysis_id if ranking else analysis_id
        except ValueError as exc:
            return {
                "answer": f"The requested candidate could not be read: {exc}",
                "citations": [],
                "provider": "deterministic",
                "grounded": False,
                "limitations": [],
                "analysis_id": analysis_id,
            }
    elif analysis_id is not None:
        analysis = db.get(AnalysisExecution, analysis_id)
        if analysis is None:
            return {
                "answer": "That analysis does not exist in this deployment.",
                "citations": [],
                "provider": "deterministic",
                "grounded": False,
                "limitations": [],
                "analysis_id": analysis_id,
            }
        if analysis.status != "succeeded":
            return {
                "answer": (
                    f"Analysis '{analysis.title}' is {analysis.status} at stage '{analysis.stage}' "
                    f"({analysis.progress:.0%}). No scientific result is available yet."
                ),
                "citations": [],
                "provider": "deterministic",
                "grounded": True,
                "limitations": [analysis.error_message] if analysis.error_message else [],
                "analysis_id": analysis.id,
            }
        payload = analysis_results(db, analysis)
        rankings = payload["rankings"]
        if not rankings:
            answer = "The analysis completed but contains no ranked candidates."
            citations = []
            limitations = payload.get("limitations") or []
        else:
            top = rankings[0]
            intent = _intent(question)
            summary = analysis.result_summary or {}
            head = (
                f"For '{analysis.title}', the highest-ranked Earth analogue is {top['display_name']} "
                f"at {top['center_lon']:.4f}, {top['center_lat']:.4f}. Similarity is {top['overall_score']:.3f} "
                f"and evidence confidence is {top['confidence']:.3f} ({top['confidence_band']}). "
            )
            if intent == "confidence":
                bands = summary.get("confidence_bands") or {}
                answer = head + (
                    f"Mean confidence across {len(rankings)} candidates is {summary.get('mean_confidence', 0):.3f}; "
                    f"the band distribution is {bands}. Evidence coverage for the top candidate is "
                    f"{top['evidence_coverage']:.0%}. Confidence is deliberately separate from similarity."
                )
            elif intent == "why":
                answer = head + "Its strongest dimensions are: " + _driving_features(top) + "."
            elif intent == "methods":
                agreement = payload.get("method_agreement") or {}
                answer = head + (
                    "Primary MCDA method scores by dimension: " + _format_dimensions(top) + ". "
                    f"Across-candidate method spread (mean standard deviation) is "
                    f"{(agreement.get('agreement') or {}).get('mean_method_std')}. "
                    "A large spread is a sensitivity warning, not a hidden error."
                )
            elif intent == "sources":
                datasets = payload.get("dataset_manifest", {}).get("datasets") or []
                answer = head + "The run used: " + "; ".join(
                    f"{d.get('dataset_id')} ({d.get('role')})" for d in datasets
                ) + ". Full citations, URLs, and licences are attached."
            elif intent == "limitations":
                answer = head + "Known limitations: " + " ".join(payload.get("limitations") or [])
            elif intent == "compare" and len(rankings) > 1:
                second = rankings[1]
                answer = head + (
                    f"The next candidate is {second['display_name']} at similarity {second['overall_score']:.3f} "
                    f"and confidence {second['confidence']:.3f}. The difference in primary score is "
                    f"{abs(top['overall_score'] - second['overall_score']):.3f}. "
                    "Inspect the dimension and method breakdown before treating this ordering as meaningful."
                )
            else:
                answer = head + "Dimension scores: " + _format_dimensions(top) + ". "
                answer += "Strongest evidence: " + _driving_features(top) + "."
            limitations = payload.get("limitations") or []
            citations = [
                {
                    "dataset_id": d.get("dataset_id"),
                    "title": d.get("role"),
                    "source_url": d.get("source_url"),
                    "citation": d.get("citation"),
                    "license": d.get("license"),
                }
                for d in (payload.get("dataset_manifest", {}).get("datasets") or [])
                if d.get("source_url")
            ]
    else:
        datasets = int(db.scalar(select(func.count()).select_from(DatasetCollection)) or 0)
        regions = int(db.scalar(select(func.count()).select_from(PlanetaryRegion)) or 0)
        analyses = int(db.scalar(select(func.count()).select_from(AnalysisExecution)) or 0)
        answer = (
            "TERRA-X measures a lunar or Martian reference region from an authoritative DEM, screens "
            "real Earth terrain, refines candidates with Copernicus GLO-30 on a common 500 m grid, "
            "and ranks multidimensional similarity separately from evidence confidence. "
            f"This deployment currently has {datasets} catalogued datasets, {regions} reference regions, "
            f"and {analyses} analyses. Select an analysis to ask about specific scores, methods, "
            "provenance, or limitations."
        )
        citations = []
        limitations = [
            "Similarity is a heuristic, not a probability.",
            "Absolute elevation is not compared across bodies.",
            "Cross-body thermal and albedo dimensions remain evidence-only unless calibrated products exist.",
        ]

    conversation = AssistantConversation(
        analysis_id=analysis_id,
        question=question,
        answer=answer,
        citations=citations,
        provider="deterministic",
        grounded=True,
    )
    db.add(conversation)
    db.commit()
    return {
        "answer": answer,
        "citations": citations,
        "provider": "deterministic",
        "grounded": True,
        "limitations": limitations,
        "analysis_id": analysis_id,
    }


__all__ = ["answer_question"]
