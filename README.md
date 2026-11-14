# SNSX SPACE

**Planetary analogue intelligence.** SNSX SPACE measures a Moon or Mars terrain window and finds
physically comparable regions on Earth using authoritative topography. Similarity, confidence, and
evidence coverage are separate, and every number is traceable to a stored source product.

> **NASA Space Apps Challenge 2026** — *Identify Earth Locations that Analog the Permanent Moon Base
> Locations and Mars.* SNSX SPACE is the working platform built for that problem statement.

> **Independent research prototype.** SNSX SPACE is not an official NASA product and is not
> affiliated with, endorsed by, or sponsored by NASA. Agency, mission, and product names identify
> data sources only. See [NOTICE](NOTICE) for data attributions and [LICENSE](LICENSE) for the
> code license.

This is a working scientific vertical slice: real DEM access, real screening, real refinement,
persisted runs, live job progress, reports, and tests. It is not a mockup, and it does not substitute
sample data when a service is unavailable. Every terrain view is a 3D mesh sampled from the same DEM
products used by the analysis; reference imagery is used for globes and as a display-only drape over
the measured terrain, never as geometry or a scoring input.

![SNSX SPACE landing page](docs/media/landing.png)

## What it does

1. **Reference** — reads a Moon window from the USGS LRO LOLA 128 ppd ISIS cube, or a Mars window
   from the USGS MGS MOLA 463 m mosaic. The window is resampled to a local 40 km,
   500 m azimuthal-equidistant grid.
2. **Screen** — samples real Earth topography from AWS Terrain Tiles over a user-selected bbox.
   This stage is triage only and never produces a published similarity score.
3. **Refine** — re-reads the best sites from Copernicus DEM GLO-30 at the same 40 km / 500 m
   geometry and computes the full declared feature set.
4. **Score** — ranks candidates with tolerance-normalized MCDA over geomorphology, morphometry,
   texture, and operational proxies. Secondary methods are reported and their disagreement lowers
   confidence.
5. **Inspect** — the workspace exposes dimensions, feature deltas, kernels, missing dimensions,
   method scores, source citations, product resolution, valid-pixel fractions, and limitations.
   Independent Planetary Computer cross-checks add NASADEM and ALOS DEM error statistics, IO LULC
   agreement with WorldCover, and Sentinel-2/Landsat scene previews as context-only evidence.
6. **Render** — every reference window and every ranked Earth candidate is also available as a real
   3D elevation mesh sampled from the stored DEM, with source provenance and valid-data coverage.
7. **Export** — PDF, CSV, GeoJSON, JSON, and a reproducibility manifest generated from stored rows.

## Screens

| Landing | Analysis workspace |
|---|---|
| ![Landing](docs/media/landing.png) | ![Workspace](docs/media/workspace.png) |

| 3D terrain explorer | Data observatory |
|---|---|
| ![3D terrain](docs/media/terrain.png) | ![Datasets](docs/media/datasets.png) |

| Methodology |
|---|
| ![Methodology](docs/media/methodology.png) |

## Real 3D terrain

The `/terrain` page lists every curated Moon and Mars reference window and every distinct Earth
candidate stored by completed analyses. Selecting a surface builds an 80×80 elevation grid from the
same product used by the scientific pipeline:

- Moon: LRO LOLA 128 ppd;
- Mars: MGS MOLA 463 m;
- Earth: Copernicus DEM GLO-30, with a recorded AWS Terrain Tiles fallback when no GLO-30 tile
  covers the window.

The geometry contains only sampled elevation values. Missing DEM cells remain `null` and the mesh
triangles that would use them are omitted; nothing is interpolated or invented. If the only
available Earth fallback is a constant-zero surface, the API returns an explicit error rather than
rendering a flat mesh as real terrain. Vertical exaggeration is a display control, labelled in the
interface, and is not an input to similarity or confidence. The landing page, analysis workspace,
and `/terrain` explorer all use the same endpoint:

```text
GET /api/v1/terrain/{body}/{lon}/{lat}/{size_km}.json?resolution_m=500
```

Real NASA imagery is aligned to the same footprint and draped over the measured geometry for
display only: NASA GIBS Blue Marble for Earth, the LRO WAC global mosaic for the Moon, and the Mars
Viking MDIM21 mosaic. The viewer offers a NASA-imagery/elevation toggle, auto-orbit, reset view, and
smooth vertical exaggeration; if the imagery request fails it falls back to the elevation ramp.
Imagery never contributes to elevation or similarity scoring, and the payload records its source
under `imagery` (cache version `terrain3d-v3`).

## One-command runner

`run.sh` installs dependencies, stops old project processes, starts PostGIS, applies migrations,
seeds the catalog, builds the web app, starts the API, worker, and web app, and verifies health.
Requires Docker Desktop (or Docker Engine with Compose) and `curl`; it installs `uv` and Node.js
through Homebrew or the official installer when they are missing.

```bash
./run.sh                # full clean start: install, stop old processes, build, start, verify
./run.sh restart        # same as the default
./run.sh start          # start without reinstalling dependencies
./run.sh stop           # stop API, worker, and web (keeps PostGIS)
./run.sh stop --all     # also stop PostGIS
./run.sh status         # process, database, and health status
./run.sh logs api       # follow logs: api, worker, web, db, or all
./run.sh install        # install/update Python and Node dependencies only
./run.sh doctor         # check prerequisites and current state
```

Runtime PIDs and logs are written to `.run/`, which is gitignored. Ports `3000` and `8000` are
checked before startup; the runner only stops processes whose working directory is this repository.
Use `--skip-install` or `--skip-build` with `run`/`restart` to reuse existing dependencies or the
current production build, and `--open` to open the web app after a successful start.

## Quick start (Docker)

Requires Docker with Compose. No scientific credentials are needed for the core workflow.

```bash
cp .env.example .env
docker compose up --build
```

- Web: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/v1/health>
- PostGIS: `localhost:5434`

The API container applies migrations and idempotently seeds the catalog before starting, so a fresh
volume comes up with all 22 datasets and 9 preset regions. The worker waits for API health, then
claims jobs. An optional Redis profile exists but is not used by the default queue.

## Local development

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22+, Docker for PostGIS.

```bash
cp .env.example .env
docker compose up -d db

cd apps/api
uv sync --extra dev
uv run alembic upgrade head
uv run python -m terrax.seed
uv run uvicorn terrax.main:app --host 0.0.0.0 --port 8000
```

In separate terminals:

```bash
cd apps/api && uv run python -m terrax.worker
cd apps/web && npm install && npm run dev
```

If port `5434` is unavailable, change it consistently in `docker-compose.yml` and `DATABASE_URL`.

## Configuration

All configuration is environment-based; see `.env.example`. The core path needs no keys.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy DSN; PostGIS is required |
| `TERRAX_DATA_DIR`, `TERRAX_CACHE_DIR`, `TERRAX_REPORT_DIR` | writable data roots |
| `NEXT_PUBLIC_API_URL` | browser-facing API base |
| `TERRAX_API_BASE_URL` | server-rendered Next.js API base (Compose uses `http://api:8000`) |
| `TERRAX_CORS_ORIGINS` | explicit browser origins; localhost, 127.0.0.1, and ::1 on any port are allowed by default |
| `NASA_EARTHDATA_TOKEN` | optional, for authenticated Earthdata products |
| `NASA_FIRMS_MAP_KEY` | optional fire context |
| `METEOMATICS_USERNAME`, `METEOMATICS_PASSWORD` | optional, credentialed weather context |
| `TERRAX_LLM_*`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | optional Evidence Q&A provider; the built-in responder works without any |
| `TERRAX_ADMIN_API_KEY` | optional protection for admin endpoints |

Missing credentials are reported as `requires_credentials`, never as connected.

## Tests

```bash
cd apps/api
DATABASE_URL='postgresql+psycopg://terrax:terrax@localhost:5434/terrax_test' uv run pytest -q
uv run ruff check terrax tests
```

The integration tests require a migrated and seeded `terrax_test` database:

```bash
docker exec terrax-db createdb -U terrax terrax_test
cd apps/api
DATABASE_URL='postgresql+psycopg://terrax:terrax@localhost:5434/terrax_test' uv run alembic upgrade head
DATABASE_URL='postgresql+psycopg://terrax:terrax@localhost:5434/terrax_test' uv run python -m terrax.seed
```

Frontend:

```bash
cd apps/web
npm run typecheck
npm run build
```

## Continuous integration

`.github/workflows/ci.yml` runs three jobs on every push to `main` and every pull request:

- **backend** — starts a `postgis/postgis:16-3.4` service, installs the locked environment with
  `uv sync --frozen --extra dev`, runs Ruff, applies Alembic migrations to `terrax_test`, and runs
  the full pytest suite;
- **frontend** — installs with `npm ci`, runs `tsc --noEmit`, then a production `next build`;
- **containers** — validates `docker-compose.yml` and builds the API, worker, and web images.

The workflow uses only free GitHub-hosted runners and needs no secrets.

## Scientific position

**Scored dimensions:** geomorphology (0.35), morphometry (0.30), texture (0.20), operational
(0.15). Default footprint 40 km, scoring grid 500 m.

**Evidence-only / unavailable cross-body:** surface albedo, thermal, water/ice, aeolian, and
environmental context. They are not silently folded into similarity.

**Missing data:** a dimension below the configured coverage floor is excluded and remaining weights
are renormalized. Above the floor, a `sqrt(coverage)` penalty prevents partial dimensions from
scoring as perfect matches. The policy is stored with every run.

**Confidence:** `0.25·dimension coverage + 0.20·feature coverage + 0.15·valid fraction +
0.15·resolution factor + 0.25·method agreement − uncertainty penalty`. Agreement below 0.30 caps
confidence at 0.55; below 0.45 caps it at 0.70.

Similarity is a bounded, configuration-dependent heuristic. It is **not** a probability, a
geological interpretation, a resource assessment, or a landing recommendation.

Read [the methodology](docs/science/methodology.md) and
[the architecture](docs/architecture/overview.md) for exact formulas, product tables, and
limitations.

## Repository layout

```
apps/api/          FastAPI, connectors, engine, worker, migrations, tests
apps/web/          Next.js App Router interface, Three.js globes, and DEM terrain meshes
docs/              methodology, architecture, verification report, media
infrastructure/    Dockerfiles
data/              local cache and generated reports (not committed)
```

## Data sources

USGS Astrogeology LRO LOLA and MGS MOLA, Copernicus DEM GLO-30, AWS Terrain Tiles
(Mapzen/Tilezen), NASA GIBS and CMR, ESA WorldCover, Sentinel-2 L2A via Earth Search, OpenStreetMap
Nominatim, and optional NASA FIRMS. Microsoft Planetary Computer adds NASADEM HGT v001, ALOS
World 3D-30m, Impact Observatory annual LULC V2, Sentinel-2 L2A, and Landsat Collection 2 Level-2.
Optional Meteomatics weather adds a 48-hour point forecast (temperature, wind, humidity,
precipitation, and cloud cover) as operational context. Every catalog entry carries its own
agency, citation, license, resolution, access protocol, and live-availability state.

Planetary Computer raster assets are private Azure blobs. SNSX SPACE obtains a short-lived public SAS
token per collection, appends it only for `*.blob.core.windows.net` reads, and never writes signed
URLs or tokens to evidence, manifests, reports, or logs. The STAC metadata and unsigned preview
links remain public and citable. Meteomatics uses HTTP Basic authentication from the environment;
the account username and password are never persisted. These products are cross-checks, optical
context, and weather context; Copernicus GLO-30 remains the only Earth DEM used for scoring.

The delivery environment reaches these archives at roughly 100 KB/s. The design therefore reads
windows and small ranges, caches them, and avoids bulk downloads. Large archives and credential-
gated products remain intentionally out of the core path.

## License and attribution

The SNSX SPACE source code is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE).
Data products and services retain their own terms and are attributed in [NOTICE](NOTICE). The
Apache license does not grant rights to NASA, USGS, ESA, Copernicus, or other agency names or
marks, and nothing in this repository implies endorsement by those agencies.

## Verification

Executed on 2026-11-13, with the Planetary Computer, Meteomatics, and container checks run on
2026-11-14:

- backend: **77 passed**, 3 non-failing warnings, including 6 terrain, 12 Planetary Computer, and
  8 Meteomatics tests; Ruff clean;
- frontend: TypeScript clean and production build passed, including `/terrain`;
- canonical Moon run `0f81536a-fd92-4e90-80be-558679889b59`: 68 coarse samples, 3/3 refinements
  succeeded, 3 candidates, top similarity **0.789007** with **0.904513** high confidence;
- Planetary Computer run `58500974-41e1-4649-9a0a-adcb9f4f126e` with catalog **0.3.0**: all five
  collections attached as context-only evidence with no context errors. NASADEM and ALOS
  cross-checked Copernicus over 6,400/6,400 cells (MAE **0.8688 m** and **2.0044 m**); IO LULC
  agreement with WorldCover was **0.23266** and is reported as disagreement; Sentinel-2 and
  Landsat returned 3 scenes each (cloud **0.500184%** and **9.14%**);
- live Planetary Computer probes: all five collections `available`; full catalog probe returned
  **21 available, 1 credential-gated, 0 unavailable**;
- Meteomatics weather was exercised against the live credentialed API: the probe returned
  `available` (HTTP 200); a 48-hour forecast at the Atacama test site returned 17 samples with
  mean temperature **20.66 °C**, max wind **7.4 m/s**, and mean cloud **14.37%**. The account
  username and password were absent from connector output, evidence, and all report exports;
- five reports generated for the Planetary Computer run, re-downloaded, and SHA-256 matched; no
  `sig=` or `<sas>` appeared in any export or in API/worker logs;
- 3D terrain: 2 landing meshes, 4 workspace surfaces, and **17** distinct surfaces in `/terrain`
  rendered real DEM geometry; all eight Earth candidate surfaces returned 6,400/6,400 Copernicus
  cells; an ocean/void coordinate now returns HTTP 422 instead of a flat 0 m mesh; real NASA
  imagery previews (GIBS Blue Marble, LRO WAC, Viking MDIM21) are draped over the measured
  geometry for display only, with a working imagery/elevation toggle, auto-orbit, reset view, and
  reduced-motion support;
- GeoJSON reports no longer apply an Earth CRS to Moon/Mars features; per-feature spatial
  reference metadata is emitted instead;
- CORS local-origin handling and the root hydration attribute warning were fixed and
  browser-verified;
- the SNSX SPACE identity was rechecked live: the navigation, hero, footer, workspace, data
  observatory, methodology page, page metadata, API title, and package names carry the new brand,
  and the exact NASA Space Apps Challenge 2026 statement appears in the landing banner, footer,
  methodology page, README, and docs; no page rendered the old identity;
- browser checks covered rankings, candidate comparison, reports, Evidence Q&A, SSE progress,
  Planetary Computer evidence metrics/previews, the no-WebGL fallback, and SwiftShader
  software-WebGL globe and terrain renders;
- **containerized stack verified**: API, worker, and web images built from the committed
  Dockerfiles (189 MB, 189 MB, 77.5 MB); a fresh PostGIS volume applied migrations and
  auto-seeded **22 datasets** and 9 regions; the API became healthy at version `0.4.0` with
  `meteomatics_configured: true`; Moon, Mars, and Earth terrain requests each returned 80×80 real
  DEM cells; analysis `6965dd2a-6462-46a3-8e1a-78dea3153af7` ran through the containerized worker
  to 3 candidates (top similarity **0.734385**, confidence **0.700000**) with
  NASADEM/ALOS/LULC/Sentinel-2/Landsat and Meteomatics weather evidence; all five reports matched
  their checksums, no export contained the Meteomatics username or password, and GeoJSON report
  `8275bf91-706d-48aa-8d69-c52a933e6dc3` had no collection-wide CRS and per-feature spatial
  references. The run used isolated ports `15434`/`18000`/`13000`.
- the CI workflow is committed and YAML-validated but has not executed on GitHub (no remote
  configured).

See [docs/verification.md](docs/verification.md) for full run IDs, candidate tables, report
checksums, browser observations, and unresolved gaps.
