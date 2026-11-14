# SNSX SPACE Architecture

**Status:** implemented baseline · version 0.4.0

**Challenge:** NASA Space Apps Challenge 2026 — *Identify Earth Locations that Analog the Permanent
Moon Base Locations and Mars*

**Scope:** planetary analogue discovery for Earth, the Moon, and Mars

## 1. Design position

SNSX SPACE compares *measured* properties of a Moon or Mars reference window with measured
properties of real Earth windows. Every displayed number traces to a product, spatial window,
processing method, and version. Missing evidence is shown as missing.

The system is a **modular monolith plus independently scalable workers**:

- one FastAPI application, split into API routes, connectors, engine, jobs, reports, and assistant;
- one PostgreSQL/PostGIS database;
- one worker process type that can be scaled horizontally;
- one Next.js application;
- optional Redis, not required by the default queue.

```
                    ┌─────────────────────────────┐
                    │  Next.js App Router (React) │
                    │  globes, rankings, evidence │
                    └──────────────┬──────────────┘
                                   │ REST + SSE
                    ┌──────────────▼──────────────┐
                    │ FastAPI (modular monolith)  │
                    │ API / catalog / reports     │
                    └───────┬──────────────┬──────┘
                            │              │
             ┌──────────────▼───┐   ┌──────▼─────────────────────┐
             │ PostgreSQL/PostGIS│   │ Job queue (SKIP LOCKED)    │
             │ science records   │   │ worker process(es)         │
             └──────────────┬───┘   └──────┬─────────────────────┘
                            │              │
             ┌──────────────▼──────────────▼─────────────────────┐
             │ Scientific data layer                             │
             │ USGS LOLA/MOLA, Copernicus GLO-30, AWS Terrain     │
             │ Tiles, NASA GIBS/CMR, ESA WorldCover, Sentinel-2  │
             │ Microsoft Planetary Computer (DEM/LULC/optical)   │
             │ Meteomatics weather (credentialed context)         │
             └───────────────────────────────────────────────────┘
```

## 2. Why these choices

| Decision | Reason |
|---|---|
| Python 3.12 + FastAPI | Mature geospatial/scientific stack (rasterio, pyproj, scipy, numpy) and typed API contracts. |
| PostgreSQL + PostGIS | Transactional provenance, spatial indexes, one reproducible record per run. |
| Postgres-backed queue | `FOR UPDATE SKIP LOCKED` provides reliable claiming, retries, cancellation, progress, and recovery without a mandatory broker. |
| Modular monolith | The scientific domain shares one observation schema and one transaction boundary; splitting it prematurely would add failure modes without benefit. |
| Separate worker | Raster I/O and feature extraction must not block API requests. Workers scale horizontally from the same image. |
| Windowed raster access | Only the requested 40 km window is read from planetary cubes and COGs; global archives are never downloaded to analyse one region. |
| Deterministic science first | Rankings, reports, and Evidence Q&A work without any external model provider or API key. |

## 3. Runtime topology

### Local development

| Service | Address | Notes |
|---|---|---|
| PostGIS 16/3.4 | `localhost:5434` | port chosen to avoid the unrelated `leou-db` on 5433 |
| FastAPI (Uvicorn) | `localhost:8000` | `apps/api` virtualenv |
| Worker | process | `python -m terrax.worker` |
| Next.js | `localhost:3000` | `apps/web` |
| Cache | `data/cache` | planetary windows, Earth DEM crops, screening mosaics, tiles |
| Reports | `data/reports` | generated PDF/CSV/GeoJSON/JSON/manifest files |

### Containers

`docker-compose.yml` defines `db`, `api`, `worker`, and `web`, plus an optional `redis` under the
`scale` profile. The API and worker share `infrastructure/docker/api.Dockerfile`; the web image is
a multi-stage Next.js standalone build in `infrastructure/docker/web.Dockerfile`.

The API container runs `alembic upgrade head` before Uvicorn. The worker waits for the API health
check so migrations have completed before jobs are claimed. The database port is published as
`5434`; the web server uses `TERRAX_API_BASE_URL=http://api:8000` for server-rendered data and
`NEXT_PUBLIC_API_URL` for browser calls.

## 4. Module map

```
apps/api/terrax/
  __init__.py             version constants (0.4.0)
  config.py               environment + cache/report paths
  db.py                   SQLAlchemy engine/session
  models.py               normalized PostGIS schema (22 tables)
  schemas.py              Pydantic request/response contracts
  seed.py                 idempotent catalog, planets, preset regions
  main.py                 FastAPI app, middleware, lifespan
  api/routes/
    analyses.py           create/list/status/results/SSE/retry/cancel/delete/comparison
    catalog.py            planets, datasets, availability probes
    regions.py            preset + user regions
    jobs.py               job list/detail/events/cancel
    reports.py            generate/list/download/delete
    assistant.py          evidence Q&A endpoint
    media.py              real globe textures and regional previews
    terrain.py            real-DEM 3D mesh endpoint
    health.py             database + queue + configuration state
    admin.py              guarded seed/probe/recover operations
  connectors/
    planetary_dem.py      Moon LOLA ISIS tiled cube + Mars MOLA striped TIFF
    isis.py               ISIS cube tile metadata and window reads
    striped_tiff.py       tiled/striped TIFF range reads
    earth_dem.py          Copernicus GLO-30 windows with tile fallback + cache
    terrain_tiles.py      AWS Terrain Tiles screening mosaic
    copernicus_dem.py     GLO-30 COG access
    raster.py             common-grid warping, footprints, valid masks
    etopo.py              optional NOAA ETOPO access
    worldcover.py         ESA WorldCover 10 m land cover
    planetary_computer.py Planetary Computer STAC, SAS signing, DEM/LULC/optical checks
    meteomatics.py        credentialed point weather forecasts (context only)
    stac.py / cmr.py      Sentinel-2 and NASA CMR discovery
    gibs.py / trek.py     NASA imagery and planetary mosaics
    nominatim.py          reverse geocoding
    firms.py              optional fire context
    preview.py            regional preview rendering
  engine/
    features.py           feature specs, 500 m terrain profile extraction
    screening.py          coarse profile/similarity and clustering
    discovery.py          screening → densification → refinement → final thinning
    similarity.py         tolerance-normalized MCDA, secondary methods, confidence
    pipeline.py           analysis orchestration, persistence, manifest, summary
  jobs/
    queue.py              claim/retry/cancel/recover semantics
    handlers.py           analysis and report handlers
  reports/generator.py    PDF, CSV, GeoJSON, JSON, manifest
  assistant.py            deterministic citation-grounded evidence responder
  results.py              read models for results and candidate detail
  terrain3d.py            DEM window → JSON height grid + provenance cache
  catalog.py              authoritative dataset registry

apps/web/
  app/page.tsx            landing page, real texture globes, latest completed run
  app/analyses/           list + create form + workspace wrapper
  app/terrain/            catalog-wide real 3D terrain explorer
  app/datasets/           searchable data observatory with live probes
  app/methodology/        declared method, tolerances, confidence, limitations
  components/             Globe, HeroGlobes, Terrain3D, TerrainExplorer, AnalysisWorkspace, forms, chips
  lib/                    typed API client and contracts

docs/science/             methodology
docs/architecture/        this document
infrastructure/docker/    API/worker and web images
```

## 5. Data flow

1. **Catalog** seeds 21 real datasets, 3 bodies, and 9 preset regions with agency, mission,
   resolution, license, citation, access protocol, and credential requirements.
2. **Reference extraction** reads the Moon LOLA 128 ppd ISIS cube or Mars MOLA 463 m mosaic over
   the selected footprint. The window is reprojected to a 500 m local azimuthal-equidistant grid.
3. **Feature extraction** computes the declared feature vector, distributions, valid fraction,
   native resolution, and uncertainty. Absolute elevation is retained only as context.
4. **Screening** assembles AWS Terrain Tiles over the search bbox, samples a coarse terrain profile
   on a lat/lon grid, densifies around the best cells, and selects spatially distinct refinement
   sites. Coarse scores are stored but never published as final similarity.
5. **Refinement** reads Copernicus GLO-30 windows at the same 40 km / 500 m geometry. A tile
   fallback is recorded explicitly if used. Sites below 25% valid coverage are rejected.
6. **Similarity** computes the primary tolerance-normalized MCDA and the configured secondary
   methods, with coverage penalties, method agreement, and confidence ceilings.
7. **Context** queries the Microsoft Planetary Computer for NASADEM/ALOS DEM cross-checks, IO
   LULC agreement, and Sentinel-2/Landsat scenes. SAS-signed window reads are bounded; tokens and
   signed URLs are never stored. These records are evidence-only and do not enter scoring.
8. **Persistence** stores feature measurements, evidence records, candidate rankings, scores,
   methods, missing dimensions, manifest, and job events in one transaction.
9. **Reporting** generates PDF, CSV, GeoJSON, JSON, and manifest exports from stored rows and
   records size and SHA-256. GeoJSON features carry an explicit body id and spatial-reference
   block; a collection-wide Earth CRS is not applied to Moon or Mars coordinates.
10. **Frontend** reads the same stored result; it never recomputes a rank.

## 6. Spatial conventions

- Earth: WGS84 lon/lat for storage; local metric analysis in azimuthal equidistant.
- Moon/Mars: planetocentric/planetographic east-longitude, north-latitude coordinates stored as
  lon/lat with an explicit `body_id`. Local analysis uses a body-radius azimuthal equidistant CRS.
- Footprint: default 40 × 40 km; default scoring grid 500 m. Both reference and candidate use the
  same geometry and feature code.
- Resampling: bilinear for elevation, nearest for categorical layers. No-data is preserved as a
  mask; it is never converted to zero elevation.
- H3 grid mode is exposed in the configuration schema but the implemented screening path uses the
  regular lat/lon grid. Runs record which mode was configured.

## 7. Job lifecycle

```
queued → running → succeeded
              ├── retrying → queued
              ├── failed (attempts exhausted)
              └── cancelled (cooperative between stages)
```

Every job stores stage, numeric progress, structured events, attempts, worker ID, and timings.
Events are persisted in `job_events`; `/api/v1/analyses/{id}/events` streams them with SSE and
supports replay from `Last-Event-ID`. `/api/v1/analyses/{id}/event-log` returns the same stored
history as JSON for reload and polling, so the UI never parses SSE frames as JSON. Analysis status,
cancellation, streaming, and event history are scoped to `job_type = "analysis"`, so report jobs
cannot shadow the analysis job or mark an analysis failed.

## 8. API surface

All routes are under `/api/v1`.

| Area | Routes |
|---|---|
| Analyses | `POST/GET /analyses`, `GET /analyses/{id}`, `/status`, `/results`, `/result-summary`, `/candidates`, `/evidence`, `/event-log`, `POST /retry`, `/cancel`, `DELETE`, SSE `/events` |
| Candidates | `GET /candidates/{id}` with profile, feature measurements, and evidence |
| Comparison | `POST /comparisons` over stored candidate scores |
| Catalog | `GET /planets`, `GET /datasets`, `POST /datasets/probe`, `GET /datasets/{id}` |
| Regions | `GET/POST /regions`, `GET/DELETE /regions/{id}` |
| Jobs | `GET /jobs`, `GET /jobs/{id}`, `/events`, `POST /jobs/{id}/cancel` |
| Reports | `POST/GET /reports`, `GET /reports/{id}`, `/download`, `DELETE` |
| Evidence Q&A | `POST /assistant/query` (deterministic, cited) |
| Media | `GET /textures`, `/textures/{body}.jpg`, `/previews/{body}/{lon}/{lat}/{size}.jpg` |
| Terrain | `GET /terrain/{body}/{lon}/{lat}/{size}.json?resolution_m=500` — real DEM height grid with source metadata |
| Health | `GET /health` (database, queue, entity counts, credential state) |

## 9. Data model

The initial Alembic migration creates 22 tables. The central records are:

- `planets`, `dataset_collections`, `planetary_regions`;
- `analysis_executions` with config, manifest, versions, status, progress, and summary;
- `processing_jobs`, `job_events`, `audit_events`;
- `feature_measurements` with units, tolerance, native/analysis resolution, uncertainty, product,
  method, and valid fraction;
- `candidate_rankings`, `similarity_scores`, `evidence_records`;
- `reports` with format, byte size, and SHA-256;
- `comparisons`, `assistant_conversations`.

Large rasters stay outside PostgreSQL; only derived measurements and provenance metadata are
stored relationally.

## 10. Security and failure boundaries

- Secrets only via environment; `.env` is ignored. No provider credential reaches the browser.
- Pydantic validation on write paths; SQLAlchemy parameterized queries; allow-listed job types.
- CORS allow-list plus a localhost/127.0.0.1/::1 regex so Next.js dev servers work on any port;
  in-process rate limiting, pagination limits, and body-size limits.
- Optional admin API key for catalog refresh/recovery operations.
- Reports are generated into a controlled directory with an allow-listed path resolver; uploads are
  not accepted.
- A failed data source produces an explicit error, missing dimension, or unavailable probe; it is
  never replaced with synthetic data.
- The Three.js globe catches missing WebGL support and falls back to the real equirectangular
  source texture instead of crashing the analysis page.
- The frontend shows an explicit API-offline panel rather than sample values.

## 11. Scaling model

- API replicas are stateless apart from in-process rate limits.
- Workers scale horizontally; claims are transactional and disjoint.
- Processing cost is bounded by footprint, grid resolution, and configured refinement count.
- Source raster windows and screening mosaics are cached on disk by content key. Point the cache
  at a shared volume or object-storage cache for multi-host deployments.
- PostgreSQL indexes cover spatial geometry, dataset IDs, analysis IDs, job claim order, and
  ranking lookups.

## 12. Reproducibility contract

Every analysis stores algorithm/feature versions, the complete configuration, dataset and product
IDs, source URLs and citations, acquisition or processing metadata, execution timestamps, worker
and job IDs, feature values with units and uncertainty, per-method and per-dimension scores with
coverage, job events, and export manifests with checksums. Re-running against the same immutable
products and version reproduces the same ranking.
