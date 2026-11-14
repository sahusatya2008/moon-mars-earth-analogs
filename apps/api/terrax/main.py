"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api.routes import (
    admin,
    analyses,
    assistant,
    catalog,
    health,
    jobs,
    media,
    regions,
    reports,
    terrain,
)
from .config import get_settings
from .connectors.http import SourceUnavailable

settings = get_settings()
log = logging.getLogger("terrax.api")

RATE_WINDOW_SECONDS = 60.0
RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
RATE_LOCK = __import__("threading").Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_dirs()
    log.info(
        "SNSX SPACE API %s starting in %s; cache=%s reports=%s",
        __version__,
        settings.terrax_env,
        settings.cache_dir,
        settings.report_dir,
    )
    yield
    log.info("SNSX SPACE API stopping")


def _rate_limit(request: Request) -> bool:
    if settings.terrax_rate_limit_per_minute <= 0:
        return True
    path = request.url.path
    if path.endswith("/health"):
        return True
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{path.split('/')[3] if len(path.split('/')) > 3 else path}"
    now = time.monotonic()
    with RATE_LOCK:
        bucket = RATE_BUCKETS[key]
        while bucket and now - bucket[0] > RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= settings.terrax_rate_limit_per_minute:
            return False
        bucket.append(now)
        if len(RATE_BUCKETS) > 20_000:
            # Bound memory under scanning; drop the oldest half of keys.
            for stale in list(RATE_BUCKETS)[:10_000]:
                RATE_BUCKETS.pop(stale, None)
    return True


def create_app() -> FastAPI:
    app = FastAPI(
        title="SNSX SPACE Planetary Analogue Intelligence",
        description=(
            "Real-data planetary analogue discovery. Similarity is a bounded heuristic and is not "
            "a probability or a landing-safety assessment."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-TERRAX-Admin-Key", "Last-Event-ID"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms", "X-Checksum-SHA256"],
    )

    @app.middleware("http")
    async def observability_and_limits(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        if not _rate_limit(request):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded; retry shortly", "request_id": request_id},
                headers={"Retry-After": "60", "X-Request-ID": request_id},
            )
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(SourceUnavailable)
    async def source_unavailable(_request: Request, exc: SourceUnavailable):
        return JSONResponse(
            status_code=503,
            content={
                "detail": str(exc),
                "source_unavailable": True,
                "note": "The upstream scientific service is unavailable; no data was fabricated.",
            },
        )

    @app.get("/", tags=["meta"])
    def root():
        return {
            "name": "SNSX SPACE planetary analogue intelligence",
            "version": __version__,
            "api": "/api/v1",
            "docs": "/api/docs",
            "health": "/api/v1/health",
            "science": "Similarity is a heuristic, not a probability; confidence is separate.",
        }

    for router in (
        health.router,
        catalog.router,
        regions.router,
        analyses.router,
        jobs.router,
        reports.router,
        assistant.router,
        media.router,
        terrain.router,
        admin.router,
    ):
        app.include_router(router, prefix="/api/v1")

    return app


app = create_app()
