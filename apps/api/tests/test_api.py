"""API smoke tests against the real PostGIS test schema."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from terrax.db import SessionLocal
from terrax.main import app
from terrax.models import AnalysisExecution, JobEvent, ProcessingJob

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_database_and_queue(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "worker_queue" in body

    with SessionLocal() as db:
        assert db.execute(text("SELECT postgis_version()")).scalar()


def test_local_dev_origins_are_allowed(client):
    for origin in ("http://localhost:3001", "http://127.0.0.1:3012", "http://[::1]:3001"):
        response = client.get("/api/v1/health", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

    preflight = client.options(
        "/api/v1/analyses",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3001"
    assert "POST" in preflight.headers.get("access-control-allow-methods", "")


def test_terrain_route_validates_body_and_footprint(client):
    unsupported = client.get("/api/v1/terrain/venus/0/0/40.json")
    assert unsupported.status_code == 404

    too_large = client.get("/api/v1/terrain/moon/0/0/400.json")
    assert too_large.status_code == 422

    invalid_coordinates = client.get("/api/v1/terrain/moon/200/0/40.json")
    assert invalid_coordinates.status_code == 422


def test_catalog_is_seeded(client):
    planets = client.get("/api/v1/planets")
    assert planets.status_code == 200
    assert {p["id"] for p in planets.json()} >= {"earth", "moon", "mars"}

    datasets = client.get("/api/v1/datasets")
    assert datasets.status_code == 200
    rows = datasets.json()["items"] if isinstance(datasets.json(), dict) else datasets.json()
    assert len(rows) >= 16
    assert any(row["id"] == "lro_lola_ldem_128ppd" for row in rows)


def test_preset_regions_include_moon_and_mars(client):
    response = client.get("/api/v1/regions")
    assert response.status_code == 200
    rows = response.json()["items"] if isinstance(response.json(), dict) else response.json()
    bodies = {row["body_id"] for row in rows}
    assert {"moon", "mars"}.issubset(bodies)


def test_analysis_creation_validates_reference_body(client):
    earth_id = uuid.uuid4()
    response = client.post(
        "/api/v1/analyses",
        json={"title": "invalid earth reference", "reference_region_id": str(earth_id)},
    )
    assert response.status_code == 404


def test_analysis_creation_queues_a_job_and_is_idempotent(client):
    regions = client.get("/api/v1/regions", params={"body_id": "moon"}).json()
    rows = regions["items"] if isinstance(regions, dict) else regions
    region = rows[0]
    token = f"pytest-{uuid.uuid4()}"
    payload = {
        "title": "pytest Moon analysis",
        "reference_region_id": region["id"],
        "client_token": token,
        "config": {
            "footprint_km": 40.0,
            "search": {"bbox": [-70.5, -25.5, -68.5, -23.5], "refine_top_n": 5, "max_candidates": 2},
        },
    }
    first = client.post("/api/v1/analyses", json=payload)
    assert first.status_code == 201, first.text
    analysis_id = first.json()["id"]
    second = client.post("/api/v1/analyses", json=payload)
    assert second.status_code == 201
    assert second.json()["id"] == analysis_id

    jobs = client.get("/api/v1/jobs", params={"analysis_id": analysis_id}).json()
    job_rows = jobs["items"] if isinstance(jobs, dict) else jobs
    assert len(job_rows) == 1
    assert job_rows[0]["job_type"] == "analysis"

    with SessionLocal() as db:
        job_ids = [
            row.id
            for row in db.scalars(
                select(ProcessingJob).where(ProcessingJob.analysis_id == uuid.UUID(analysis_id))
            )
        ]
        for job_id in job_ids:
            db.execute(delete(JobEvent).where(JobEvent.job_id == job_id))
        db.execute(delete(ProcessingJob).where(ProcessingJob.analysis_id == uuid.UUID(analysis_id)))
        db.execute(delete(AnalysisExecution).where(AnalysisExecution.id == uuid.UUID(analysis_id)))
        db.commit()


def test_event_log_returns_stored_events_as_json(client):
    regions = client.get("/api/v1/regions", params={"body_id": "moon"}).json()
    region = (regions["items"] if isinstance(regions, dict) else regions)[0]
    created = client.post(
        "/api/v1/analyses",
        json={
            "title": "pytest event log",
            "reference_region_id": region["id"],
            "client_token": f"pytest-events-{uuid.uuid4()}",
        },
    )
    assert created.status_code == 201, created.text
    analysis_id = uuid.UUID(created.json()["id"])
    try:
        with SessionLocal() as db:
            job = db.scalar(select(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id))
            assert job is not None
            db.add(
                JobEvent(
                    job_id=job.id,
                    at=datetime.now(UTC),
                    level="info",
                    stage="testing",
                    progress=0.25,
                    message="stored event fixture",
                    data={"source": "pytest"},
                )
            )
            db.commit()

        with SessionLocal() as db:
            # A newer report job must not shadow the analysis job in status or event history.
            db.add(
                ProcessingJob(
                    job_type="report",
                    status="succeeded",
                    analysis_id=analysis_id,
                    payload={"format": "pdf"},
                )
            )
            db.commit()

        status = client.get(f"/api/v1/analyses/{analysis_id}/status")
        assert status.status_code == 200
        assert status.json()["job_id"] == str(job.id)

        response = client.get(f"/api/v1/analyses/{analysis_id}/event-log")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        items = response.json()["items"]
        assert any(item["message"] == "stored event fixture" for item in items)
        assert all(item["job_id"] == str(job.id) for item in items)
        assert all(item["stage"] for item in items)
    finally:
        with SessionLocal() as db:
            job_ids = [
                row.id
                for row in db.scalars(
                    select(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id)
                )
            ]
            for job_id in job_ids:
                db.execute(delete(JobEvent).where(JobEvent.job_id == job_id))
            db.execute(delete(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id))
            db.execute(delete(AnalysisExecution).where(AnalysisExecution.id == analysis_id))
            db.commit()


def test_unknown_analysis_returns_404(client):
    assert client.get(f"/api/v1/analyses/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/v1/analyses/{uuid.uuid4()}/results").status_code == 404
    assert client.post(f"/api/v1/analyses/{uuid.uuid4()}/retry").status_code == 404


def test_retry_only_accepts_failed_or_cancelled_and_requeues(client):
    regions = client.get("/api/v1/regions", params={"body_id": "mars"}).json()
    rows = regions["items"] if isinstance(regions, dict) else regions
    region = rows[0]
    payload = {
        "title": "pytest retry analysis",
        "reference_region_id": region["id"],
        "client_token": f"pytest-retry-{uuid.uuid4()}",
        "config": {
            "footprint_km": 20.0,
            "search": {"bbox": [-70.5, -25.5, -68.5, -23.5], "refine_top_n": 5, "max_candidates": 1},
        },
    }
    created = client.post("/api/v1/analyses", json=payload)
    assert created.status_code == 201, created.text
    analysis_id = uuid.UUID(created.json()["id"])

    try:
        # A queued run cannot be retried; it already has a live job.
        assert client.post(f"/api/v1/analyses/{analysis_id}/retry").status_code == 409

        with SessionLocal() as db:
            analysis = db.get(AnalysisExecution, analysis_id)
            assert analysis is not None
            analysis.status = "failed"
            analysis.stage = "failed"
            analysis.error_message = "synthetic failure used to exercise the retry route"
            analysis.progress = 0.4
            original_job = db.scalars(
                select(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id)
            ).first()
            assert original_job is not None
            original_job.status = "failed"
            original_job.stage = "failed"
            original_job.error_message = "synthetic failure"
            original_job.finished_at = datetime.now(UTC)
            db.commit()

        retried = client.post(f"/api/v1/analyses/{analysis_id}/retry")
        assert retried.status_code == 200, retried.text
        body = retried.json()
        assert body["status"] == "queued"
        assert body["stage"] == "queued"
        assert body["progress"] == 0.0
        assert body["error_message"] is None

        jobs = client.get("/api/v1/jobs", params={"analysis_id": str(analysis_id)}).json()
        job_rows = jobs["items"] if isinstance(jobs, dict) else jobs
        assert len(job_rows) == 2
        assert sum(1 for row in job_rows if row["status"] == "queued") == 1
        assert sum(1 for row in job_rows if row["status"] == "failed") == 1
    finally:
        with SessionLocal() as db:
            job_ids = [
                row.id
                for row in db.scalars(
                    select(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id)
                )
            ]
            for job_id in job_ids:
                db.execute(delete(JobEvent).where(JobEvent.job_id == job_id))
            db.execute(delete(ProcessingJob).where(ProcessingJob.analysis_id == analysis_id))
            db.execute(delete(AnalysisExecution).where(AnalysisExecution.id == analysis_id))
            db.commit()
