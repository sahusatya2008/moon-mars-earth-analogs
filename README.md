# TERRA-X

**Planetary analogue intelligence.** TERRA-X measures a Moon or Mars terrain window and finds
physically comparable regions on Earth using authoritative topography. Similarity, confidence, and
evidence coverage are separate, and every number is traceable to a stored source product.

> **Independent research prototype.** TERRA-X is not an official NASA product and is not
> affiliated with, endorsed by, or sponsored by NASA. Agency, mission, and product names identify
> data sources only. See [NOTICE](NOTICE) for data attributions and [LICENSE](LICENSE) for the
> code license.

This is a working scientific vertical slice: real DEM access, real screening, real refinement,
persisted runs, live job progress, reports, and tests. It is not a mockup, and it does not substitute
sample data when a service is unavailable. Every terrain view is a 3D mesh sampled from the same DEM
products used by the analysis; reference imagery is used for globes, never as terrain geometry.

![TERRA-X landing page](docs/media/landing.png)

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

The API container applies migrations before starting. The worker waits for API health, then claims
jobs. An optional Redis profile exists but is not used by the default queue.

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
| `TERRAX_LLM_*`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | optional assistant provider; the deterministic grounded narrator works without any |
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
Nominatim, and optional NASA FIRMS. Every catalog entry carries its own agency, citation, license,
resolution, access protocol, and live-availability state.

The delivery environment reaches these archives at roughly 100 KB/s. The design therefore reads
windows and small ranges, caches them, and avoids bulk downloads. Large archives and credential-
gated products remain intentionally out of the core path.

## License and attribution

The TERRA-X source code is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE).
Data products and services retain their own terms and are attributed in [NOTICE](NOTICE). The
Apache license does not grant rights to NASA, USGS, ESA, Copernicus, or other agency names or
marks, and nothing in this repository implies endorsement by those agencies.

## Verification

Executed on 2026-11-13:

- backend: **54 passed**, 3 non-failing warnings; Ruff clean;
- frontend: TypeScript clean and production build passed, including `/terrain`;
- canonical Moon run `0f81536a-fd92-4e90-80be-558679889b59`: 68 coarse samples, 3/3 refinements
  succeeded, 3 candidates, top similarity **0.789007** with **0.904513** high confidence;
- five reports generated, re-downloaded, and SHA-256 matched;
- 3D terrain: 2 landing meshes, 4 workspace surfaces, and 14 distinct surfaces in `/terrain`
  rendered real DEM geometry; all curated terrain requests returned HTTP 200 with no console
  errors; an ocean/void coordinate now returns HTTP 422 instead of a flat 0 m mesh;
- GeoJSON reports no longer apply an Earth CRS to Moon/Mars features; per-feature spatial
  reference metadata is emitted instead;
- CORS local-origin handling and the root hydration attribute warning were fixed and
  browser-verified;
- browser checks covered rankings, candidate comparison, reports, assistant, SSE progress, the
  no-WebGL fallback, and SwiftShader software-WebGL globe and terrain renders;
- `docker compose config` passed; container images were not built.

See [docs/verification.md](docs/verification.md) for full run IDs, candidate tables, report
checksums, browser observations, and unresolved gaps.
