# TERRA-X Verification Report

**Executed:** 2026-11-13 (local session, UTC timestamps below)  
**Version:** algorithm `analogue-0.2.0` · feature schema `features-0.2.0` · catalog `0.2.0`  
**Environment:** macOS, Python 3.12 virtualenv, Node 22, PostgreSQL/PostGIS on `localhost:5434`,
FastAPI on `:8000`, one worker, Next.js on `:3000`.

This report records what was actually executed. It distinguishes automated checks, completed
scientific runs, browser checks, and work that remains unverified. No test is marked passing
without a command and observed result.

## 1. Automated checks

| Check | Command | Observed result |
|---|---|---|
| Backend tests | `DATABASE_URL=.../terrax_test .venv/bin/pytest -q` | **54 passed, 3 warnings** |
| Backend lint | `.venv/bin/ruff check terrax tests` | **All checks passed** |
| Frontend types | `npm run typecheck` | **Passed** (`tsc --noEmit`, no diagnostics) |
| Frontend production build | `npm run build` | **Passed**; `/`, `/analyses`, `/terrain`, `/datasets`, `/methodology`, and dynamic `/analyses/[id]` emitted |
| Compose syntax | `docker compose config` | **Exit 0**, rendered configuration accepted |
| Docker image build | `docker compose build` | **Not executed** (see gaps) |

The three warnings are non-failing:

- one Starlette deprecation notice about the `httpx` test client;
- two SciPy precision warnings from the deliberate constant-surface test
  (`test_constant_surface_has_zero_relief_and_slope`), where skewness and kurtosis are computed on
  mathematically flat data.

The integration tests use a migrated and seeded `terrax_test` database and assert PostGIS
availability with `SELECT postgis_version()`. They cover health, catalog seeding, region presets,
analysis creation and idempotency, retry rules, missing-data policies, similarity behavior, JSON
event history, and CORS for local development origins on arbitrary ports.

The terrain tests cover the DEM-to-mesh payload, strict JSON `null` handling for missing cells,
disk-cache round trips, rejection of a constant-zero AWS fallback, invalidation of a cached
degenerate mesh, and route validation for unsupported bodies, invalid coordinates, and oversized
footprints. The report tests assert that GeoJSON does not apply a collection-wide Earth CRS to
Moon/Mars features. The observed count in this session was **54 passed**.

## 2. Completed scientific runs

### 2.1 Canonical Moon run — Aristarchus Plateau

| Field | Value |
|---|---|
| Analysis ID | `0f81536a-fd92-4e90-80be-558679889b59` |
| Analysis job ID | `20d79359-6338-4cc6-adff-84bbdd15c332` |
| Title | Aristarchus Plateau vs Atacama |
| Reference | Aristarchus Plateau, Moon, `25.40°N, 47.40°W` |
| Footprint / grid | 40 km / 500 m |
| Earth search bbox | `[-70.5, -25.5, -68.5, -23.5]`, seed 42 |
| Completed (UTC) | `2026-11-13T10:10:42.785587Z` |
| Status | `succeeded`, progress 1.0 |

Observed execution:

- **68** coarse Earth samples from AWS Terrain Tiles at 5 km triage resolution;
- **3** refinement sites, **3 succeeded**, **0 failed** from Copernicus GLO-30;
- **3** persisted Earth candidates, all with 100% weighted evidence coverage;
- **19** stored job events from queued through complete;
- **6** source datasets and **9** run-level limitations in the manifest.

| Rank | Candidate ID | Center | Similarity | Confidence | Band | Coverage |
|---:|---|---|---:|---:|---|---:|
| 1 | `71f8301e-c6fa-44bb-a028-e21f9097db74` | `25.00°S, 70.20°W` | **0.789007** | **0.904513** | high | 1.0 |
| 2 | `b537829f-6709-41f1-9f26-4601e59080d9` | `23.80°S, 70.00°W` | 0.682421 | 0.550000 | moderate | 1.0 |
| 3 | `b0a8eaad-52cf-4c6e-9338-d5ae1361dbc9` | `24.60°S, 69.00°W` | 0.646546 | 0.550000 | moderate | 1.0 |

Top candidate dimension scores: relief & hypsometry **0.722152**, slopes & landforms
**0.924036**, multi-scale texture **0.740701**, trafficability proxies **0.739351**.

Top candidate method sensitivity: MCDA **0.789007**, cosine **0.953576**, Wasserstein
**0.745067**, weighted Euclidean **0.749737**, standardized Euclidean **0.716103**. The
cross-candidate mean method standard deviation is **0.162233**. This spread is reported in the
workspace; the primary MCDA ranking is not replaced by an average of methods.

Reproducibility hashes stored with the run:

```text
config_sha256    33f646ed9877745193fe445d47960f49786debd0912c37ba0493150be45d3273
manifest_sha256  d608406a5f15b7cbd1196bf3e9289b6f2d3793d485b29e65457b70edd47cf7df
```

### 2.2 Mars cross-body run — Gale Crater

Executed earlier in the same session with the same persisted science schema:

| Field | Value |
|---|---|
| Analysis ID | `7770c661-a9fc-4bdf-8b85-55c0c7bdc9c4` |
| Reference | Gale Crater, Mars |
| Coarse samples / refinements | 68 / 2, 2 succeeded, 0 failed |
| Candidates | 2 |
| Top candidate | `25.40°S, 69.20°W`, similarity **0.565476**, confidence **0.550000** (moderate) |
| Second candidate | `24.40°S, 70.20°W`, similarity **0.429113**, confidence **0.550000** (moderate) |

This run exercises the Mars MOLA reference path and produces a materially lower similarity than
the Moon run. It is not evidence that either candidate is a geological analogue; it demonstrates
that the Mars path completes and persists inspectable scores.

### 2.3 Live progress (SSE) run

The canonical Moon run was watched from creation to completion through the browser:

- at **10%** the workspace showed status **RUNNING**, stage **Reference Features**, and the first
  five real worker events;
- at **79%** it still showed **RUNNING**, stage **Persistence**, after screening and refinement
  events;
- the terminal event moved the page to the completed workspace with 3 ranked rows and no error
  panel;
- the browser console contained **no JSON parse error** and the page text did not contain
  `is not valid JSON`.

![Live SSE progress](media/progress.png)

Two defects were found and fixed during this verification:

1. The browser history loader fetched the SSE endpoint `/analyses/{id}/events` as JSON, producing
   `Unexpected token 'i', "id: 165 ev"... is not valid JSON`. A separate JSON route,
   `/analyses/{id}/event-log`, now serves stored history; `/events` remains SSE-only.
2. Report jobs for the same analysis could become the “latest job” used by analysis status,
   cancellation, streaming, and event history. Analysis routes now select
   `job_type = "analysis"`. A regression test creates a newer report job and asserts that the
   analysis job still owns status and events.

## 3. Report exports

Five reports were generated for the canonical Moon run and downloaded again for independent
checksum verification.

| Format | Report ID | Bytes | SHA-256 (downloaded bytes) | Check |
|---|---|---:|---|---|
| PDF | `ea084dd2-958d-4d44-b04e-882b7ce13968` | 9,792 | `8df00262ca84b3e91814b9b024dce8a02217a290d15a13048368e23017d04042` | match |
| CSV | `e05edbe4-044d-4238-b228-fb0e810485db` | 1,757 | `cf6531fa08b0868751c6856f9db79943e870265c7e6bb8e2418749a1796d8d65` | match |
| GeoJSON | `854fa31f-d142-42df-80a0-614afc86908a` | 4,773 | `22e025f771a587d262837d421b7b66e4c0df0e9d2e0f0c7a3a90a1f63dd7619f` | match |
| JSON | `ad81f35a-dc49-43e3-9303-cf7cb46f9b14` | 91,008 | `95f2173780430781b5d0f35a62c2db89f9a858f7f8f34db0f86cbb1927542a10` | match |
| Manifest | `0cce458d-efb1-42db-a68b-44505ee69b25` | 46,613 | `c6202d9fa368cba0158f3a40e188b38ca00d0b6927eae151514dd314f85c8f36` | match |

The downloaded PDF is a valid 4-page PDF 1.4 document. The GeoJSON is a `FeatureCollection` with
4 features (3 candidates and the reference window). The CSV has one header plus 3 candidate rows.
The JSON and manifest parse as JSON; the manifest contains analysis, reference region, candidate
IDs, dataset manifest, evidence records, limitations, and reproducibility blocks.

## 4. Assistant

`POST /api/v1/assistant/query` was run against the canonical Moon analysis with the question
“Why did the top candidate rank first?”. Observed response:

- provider: `deterministic`;
- grounded: `true`;
- citations: **6**;
- answer quoted the stored top candidate, coordinates, similarity, confidence band, strongest
  dimensions, feature deltas, tolerances, and feature-level similarities.

No LLM key was configured for this check. The grounded narrator is the tested path.

## 5. Browser checks

The running production build was inspected through headless Chrome at 1600 px width.

| Page / interaction | Observed |
|---|---|
| Landing page | Real catalog counts, source-registry content, globe panel, and body tabs render |
| Analyses list | 9 stored runs and statuses render |
| Analyses filters | All/Queued/Running/Complete/Failed work; Complete shows 9, Failed 0, Moon 8 rows, Mars 1 row |
| Local dev origin | Port `3001` loads the analyses list and `/terrain` with a real terrain mesh and no `Failed to fetch` after the CORS fix |
| Workspace rankings | 3 candidates, scores, confidence bands, coverage, selectable rows |
| Candidate selection | Scatter/target row changes the profile and evidence panel |
| Candidate comparison | Stored-score comparison table renders for selected candidates |
| Report panel | 5 ready formats and 5 download links; downloads checksum-verified |
| Assistant | Cited answer renders with 6 citations |
| SSE progress | Live stage/progress/events render; terminal transition succeeds |
| Data observatory | Catalog filter, scoring-input filter, body groups, and live probe controls render |
| Methodology | Declared formulas, weights, tolerances, and limitations render |
| Missing WebGL | Globe shows the real equirectangular source texture and an explicit fallback label |
| 3D globe (SwiftShader) | Landing and workspace globes render the real NASA Earth texture, candidate pin, and body tabs; **no console errors** |
| 3D terrain landing | Two real DEM meshes render: Aristarchus (LOLA) and the top Atacama candidate (Copernicus); **no terrain error** |
| 3D terrain workspace | Four selectable surfaces render: reference plus all three Earth candidates; each tab requests the real DEM mesh |
| 3D terrain explorer | `/terrain` lists **14 distinct surfaces** (5 Earth, 4 Moon, 5 Mars); body filters and search work; all selected meshes render |
| Terrain exaggeration | Display slider changed from `×2.0` to `×3.5`; canvas remained live with no error |
| Mobile layout (390 px) | Nav links remain visible, `/terrain` stacks to one column, 14 entries and a terrain canvas render, and the document has no horizontal overflow |

Two headless Chrome modes were used. The default headless instance had GPU/WebGL disabled and
logged Three.js `A WebGL context could not be created` messages; the application caught the
condition and rendered the real equirectangular texture fallback without an uncaught exception.
A second instance launched with SwiftShader software WebGL (`--use-gl=angle
--use-angle=swiftshader`) rendered the textured Three.js globes on the landing page and in the
workspace, with **no console errors** in either capture. Drag/zoom interaction was not scripted;
the render path itself is verified. A hardware-GPU browser was not available.

### 5.1 Real 3D terrain

Terrain views are built by `apps/api/terrax/terrain3d.py` from the same window loaders used for
scientific measurement and served by `GET /api/v1/terrain/{body}/{lon}/{lat}/{size_km}.json`.
Every tested surface returned an **80×80 grid at 500 m sampling with 100% valid cells** and source
metadata in the payload and response headers:

| Surface | Coordinates | Source product |
|---|---|---|
| Moon · Aristarchus Plateau | `25.40°N, 47.40°W` | `lro_lola_ldem_128ppd` |
| Mars · Gale Crater | `5.40°S, 137.80°E` | `mgs_mola_megdr_463m` |
| Earth · top Atacama candidate | `25.00°S, 70.20°W` | `copernicus_dem_glo30` |

The direct check below returned HTTP 200 with `X-Terrain-Source: lro_lola_ldem_128ppd`:

```bash
curl -s -D - -o /dev/null \
  'http://localhost:8000/api/v1/terrain/moon/-47.4/25.4/40.json?resolution_m=500'
```

The browser loaded 2 real DEM meshes on the landing page, 4 surfaces in the canonical workspace,
and 14 distinct surfaces in `/terrain` (5 Earth candidates, 4 Moon regions, 5 Mars regions). Body
filters, search, surface switching, and the display-only vertical-exaggeration control were
exercised; every terrain request returned HTTP 200, no `.terrain-error` appeared, and the console
was clean. Geometry is elevation only: no imagery texture is used, missing cells stay `null`, and
triangles touching a missing cell are omitted. The unit tests assert strict JSON `null` handling
and cache round trips.

### 5.2 CORS and hydration fixes

Two runtime defects found in earlier browser passes were fixed and rechecked:

- **CORS on local origins.** The backend now accepts `localhost`, `127.0.0.1`, and `[::1]` on any
  port through a local-origin regex, with explicit origins still configurable. The Next.js dev
  server on port `3001` loaded the analyses list and `/terrain` without `Failed to fetch`; a
  regression test covers arbitrary local ports.
- **Root hydration warning.** Browser-extension attributes injected on `<html>` caused a mismatch.
  `apps/web/app/layout.tsx` now sets `suppressHydrationWarning` on the root `<html>` element only.
  Simulating the injected attribute before hydration produced no console error.

Screenshots in `docs/media/` were captured from these running services:

- `landing.png`, `terrain.png`, `datasets.png`, `methodology.png`, `workspace.png`,
  `progress.png`.

`landing.png` is the SwiftShader render with the real Earth texture, 16 datasets, 9 reference
regions, 9 stored analyses, the latest Aristarchus run, and the start of the real 3D terrain
section. `workspace.png` is the canonical Moon run with the four-surface 3D terrain panel, all five
reports ready, and a cited assistant answer. `terrain.png` is the `/terrain` explorer showing all
14 surfaces and a real Moon LOLA mesh. `progress.png` is the same canonical run mid-execution at
10%, showing `RUNNING`, not an error.

### 5.3 Terrain fallback and GeoJSON integrity fixes

Two data-integrity issues were found during the audit and fixed:

- **Constant-zero fallback terrain.** Before the fix, an arbitrary ocean/void coordinate could
  return a flat surface marked as fully valid:

  ```text
  GET /api/v1/terrain/earth/-25/-70.2/40.json?resolution_m=500
  → 80×80, 6,400/6,400 valid, min = max = 0.0 m, source aws_terrain_tiles
  ```

  The Earth DEM loader now rejects a constant-zero AWS Terrain Tiles fallback, deletes a cached
  entry written before the guard, and the terrain mesh cache version was moved to `terrain3d-v2`
  so a previously cached degenerate mesh is not served. The same coordinate now returns
  **HTTP 422** with an explicit “no real elevation” message. The canonical Copernicus candidate
  still returns **HTTP 200** with 6,400/6,400 valid cells and elevations from 1,031.93 m to
  2,663.65 m. After the cache-version change, all **14** catalog surfaces (5 Earth, 4 Moon,
  5 Mars) were re-requested: every one returned **HTTP 200**, **6,400/6,400** valid cells, and
  the expected LOLA, MOLA, or Copernicus source product; no cached mesh was degenerate.
- **Earth CRS applied to planetary GeoJSON.** The GeoJSON report no longer writes a
  collection-wide `CRS84` member. Each feature declares `body_id` and a `spatial_reference`
  block; Moon/Mars features are marked body-fixed, while Earth candidates use OGC:CRS84. A live
  report (`886090b6-c4af-4507-b7d7-2de004c4a31e`) was generated after the worker restart and
  verified to have no top-level `crs`, one lunar body-fixed feature, and three Earth CRS84
  candidates.

Licensing and disclaimer files were also added: `LICENSE` (Apache-2.0), `NOTICE` with data
attributions, a README non-affiliation statement, and a footer notice in the web interface. The
production Next.js build and the development server both render the footer notice.

## 6. Not verified / known gaps

These are deliberately unresolved rather than hidden:

1. **Docker images were not built.** `docker compose config` passes, but no container image or
   containerized end-to-end run was executed. Local Python/Node processes were used for all tests.
2. **No credential-gated live checks.** `NASA_EARTHDATA_TOKEN` and `NASA_FIRMS_MAP_KEY` were not
   configured. `/health` reports `earthdata_configured: false`; affected catalog rows report
   `requires_credentials`, not “connected”.
3. **No LLM provider was exercised.** Only the deterministic grounded narrator was tested.
4. **No Redis path was exercised.** Redis is optional and unused by the default Postgres queue.
5. **Large NASA/PDS archives were not downloaded.** The delivery network ran at roughly 100 KB/s;
   the implementation uses windowed range reads and local caches. Bulk archive ingestion remains
   untested.
6. **Screening was not global.** The verified runs searched the Atacama bbox only. The global
   `bbox` option exists, but a full-planet screening run was not executed.
7. **Mars verification predates the UI/SSE fixes.** The Mars science run completed before the
   separate event-history route and job-scope fix. Those changes affect presentation and job
   lookup, not feature extraction or scoring, but the Mars run was not re-executed afterward.
8. **No independent geological ground truth.** Similarity is a bounded heuristic, not a
   probability or a validated geologic analogue classification. No accuracy, precision, or recall
   claim is made.
9. **No load, concurrency, or multi-worker benchmark.** One worker processed all verified runs.
10. **3D globe and terrain rendering were verified only under SwiftShader software WebGL.** Surface
    switching, filters, search, and the exaggeration control were scripted; orbit/zoom drag
    interaction and a hardware-GPU browser were not exercised.
11. **Constant-zero guard, not a full land/water mask.** The terrain API rejects an all-zero
    fallback window, but it does not yet apply a global land mask to arbitrary Earth coordinates.
    A non-zero fallback surface over water could still be returned; curated candidate surfaces are
    land-refined and unaffected.

## 7. Reproduction

```bash
# Backend
cd apps/api
DATABASE_URL='postgresql+psycopg://terrax:terrax@localhost:5434/terrax_test' .venv/bin/pytest -q
.venv/bin/ruff check terrax tests

# Frontend
cd ../web
npm run typecheck
npm run build

# Compose syntax (images intentionally not built)
cd ../..
docker compose config
```

The canonical run can be re-created with the API:

```bash
curl -X POST http://localhost:8000/api/v1/analyses \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Aristarchus Plateau vs Atacama",
    "reference_region_id": "89a02f5d-12ce-496c-9c20-f608367bab3e",
    "config": {
      "footprint_km": 40,
      "analysis_resolution_m": 500,
      "search": {
        "bbox": [-70.5, -25.5, -68.5, -23.5],
        "grid_mode": "latlon",
        "cell_size_deg": 1,
        "screening_resolution_km": 5,
        "refine_top_n": 6,
        "max_candidates": 6,
        "cluster_radius_km": 100,
        "random_seed": 42,
        "exclude_polar_gt_deg": 85
      }
    }
  }'
```

Exact scores can change if upstream tiles or caches change; every run stores its configuration
hash, manifest hash, source products, and per-feature evidence.
