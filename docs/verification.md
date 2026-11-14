# TERRA-X Verification Report

**Executed:** 2026-11-13 (local session); Planetary Computer, Meteomatics, and container checks
2026-11-14

**Version:** algorithm `analogue-0.2.0` · feature schema `features-0.2.0` · catalog `0.4.0`

**Environment:** macOS, Python 3.12 virtualenv, Node 22, PostgreSQL/PostGIS on `localhost:5434`,
FastAPI on `:8000`, one worker, Next.js on `:3000`; Docker Compose end-to-end on `15434`/`18000`/`13000`.

This report records what was actually executed. It distinguishes automated checks, completed
scientific runs, browser checks, and work that remains unverified. No test is marked passing
without a command and observed result.

## 1. Automated checks

| Check | Command | Observed result |
|---|---|---|
| Backend tests | `DATABASE_URL=.../terrax_test .venv/bin/pytest -q` | **76 passed, 3 warnings** |
| Backend lint | `.venv/bin/ruff check terrax tests` | **All checks passed** |
| Frontend types | `npm run typecheck` | **Passed** (`tsc --noEmit`, no diagnostics) |
| Frontend production build | `npm run build` | **Passed**; `/`, `/analyses`, `/terrain`, `/datasets`, `/methodology`, and dynamic `/analyses/[id]` emitted |
| Compose syntax | `docker compose config` | **Exit 0**, rendered configuration accepted |
| Docker image build | `docker compose ... build` | **Passed**; API **189 MB**, worker **189 MB**, web **77.5 MB** (catalog-0.4.0 rebuild) |
| Compose end-to-end | isolated-port stack + `verify_compose.py` | **All checks passed** (see 1.1) |

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
Moon/Mars features. Config tests assert that the repository root resolves correctly in both the
checkout layout (`<repo>/apps/api/terrax/config.py`) and the container layout
(`/app/terrax/config.py`). The observed count in this session was **76 passed**.

Twelve dedicated Planetary Computer tests cover SAS signing restricted to Azure blob hosts,
token redaction, cached token refresh, antimeridian splitting and item de-duplication, DEM
difference statistics with missing cells, LULC year selection, WorldCover/IO LULC broad-class
agreement, optical search retry without a cloud filter, probe states, and registration of all
five collections as Earth context-only datasets. These use synthetic HTTP/raster fixtures; live
Planetary Computer access is reported separately in 1.2 and 2.4.

Eight dedicated Meteomatics tests cover the documented URL path, HTTP Basic auth, multiple
locations, optional query parameters, forecast summary statistics including circular wind
direction, response caching, credential rejection, API parameter errors, probe states, and
registration as a context-only dataset. The tests assert that the response `user` field and the
configured username/password never appear in connector output. These use mocked HTTP; live access
is reported in 1.3 and 2.5.

### 1.1 Containerized Compose run (2026-11-14)

The catalog-0.4.0 stack was rebuilt from the committed Dockerfiles with a fresh PostGIS volume.
Images measured: API **189 MB**, worker **189 MB**, web **77.5 MB** (uncompressed arm64 images).
`rasterio`'s manylinux wheel links `libexpat.so.1`, which `python:3.12-slim` omits, so the API
runtime stage installs `libexpat1`; `python -c "import terrax.main"` succeeds inside the image
with `REPO_ROOT=/app`.

The stack used isolated host ports—PostGIS `15434`, API `18000`, web `13000`—so an existing local
development stack on `5434`/`8000`/`3000` stayed untouched. Its database volume was empty before
the run. Earlier builds (catalog 0.2.0 and 0.3.0) passed the same checks; this rerun supersedes
them.

| Check | Observed result |
|---|---|
| Startup | initial migration `0697627b9ac4` applied; seed logged `{'planets': 3, 'datasets': 22, 'regions': 9}`; API became healthy |
| Catalog | `/health` returned `status=ok`, `database=ok`, version `0.4.0`, **22** datasets, **9** regions, `meteomatics_configured: true` |
| Web | landing returned HTTP 200, rendered v0.4 with **22** authoritative datasets and the non-affiliation notice; headless Chrome on the container workspace rendered **9** evidence records including the Planetary Computer previews and the Meteomatics weather insight |
| Moon terrain | `lro_lola_ldem_128ppd`, 80×80, **6,400/6,400** valid, −2,548.0 to −560.5 m |
| Mars terrain | `mgs_mola_megdr_463m`, 80×80, **6,400/6,400** valid, −3,444.4 to 761.0 m |
| Earth terrain | `copernicus_dem_glo30`, 80×80, **6,400/6,400** valid, 1,234.1 to 2,476.6 m |
| Analysis | `6965dd2a-6462-46a3-8e1a-78dea3153af7` succeeded through the containerized worker; **3** candidates; top `602bdf71-1f57-4f69-a1cc-050fbfdc5f51` · **Provincia de Antofagasta, Antofagasta Region, Chile**, similarity **0.734385**, confidence **0.700000** (moderate); **no context errors** |
| Planetary Computer | containerized worker attached NASADEM (MAE **0.8688 m**), ALOS (MAE **2.0044 m**), IO LULC (agreement **0.23266**), Sentinel-2 (cloud **0.676448%**), and Landsat (cloud **9.14%**); workspace rendered both unsigned preview images |
| Meteomatics | containerized worker attached weather evidence for all **3** candidates; the top candidate recorded mean **20.66 °C**, max wind **7.4 m/s**, mean cloud **14.37%**, humidity **26.21%**, and sampled precipitation **0.0 mm** at `25.30°S, 69.90°W` |
| Reports | JSON, manifest, GeoJSON, CSV, and PDF all `ready`; downloaded SHA-256 matched; no `sig=`, `<sas>`, Meteomatics username, or password leak |
| GeoJSON | report `8275bf91-706d-48aa-8d69-c52a933e6dc3`, 6,015 bytes, no collection-wide `crs`, **4** features with `spatial_reference` |

Container report IDs: JSON `4056c6cc-fe1c-446c-9919-a2f351900ff1`, manifest
`b63f271b-3020-4015-a72f-60642a1be0ed`, GeoJSON `8275bf91-706d-48aa-8d69-c52a933e6dc3`, CSV
`148963a0-9b4a-481d-8f26-68acb13a4724`, PDF `36794dd1-dbdb-4759-a16c-bceff2ad755a`. The JSON
export contains `meteomatics_weather`; GeoJSON, CSV, and PDF are candidate-summary exports and do
not.

The worker made live network requests during the run: Planetary Computer STAC, SAS signing, and
signed window reads succeeded; Earth Search and NASA GIBS also returned HTTP 200. The API and
worker logs contained no SAS token or signed query string. This exercises refinement and evidence
fetching over the network inside the containers, not only from the local cache.

### 1.2 Live Planetary Computer connector checks (2026-11-14)

The five collections were exercised directly against
`https://planetarycomputer.microsoft.com/api/stac/v1`, not only through fixtures. The connector
obtained a public SAS token, signed only Azure blob URLs, and read bounded windows at an Atacama
test site (`25.0°S, 70.2°W`, 40 km):

| Check | Observed result |
|---|---|
| NASADEM HGT v001 vs Copernicus GLO-30 | bias **+0.2223 m**, MAE **0.8989 m**, RMSE **1.1097 m**, Pearson r **0.999991**, **6,400/6,400** overlapping cells |
| ALOS World 3D-30m vs Copernicus GLO-30 | bias **+3.0964 m**, MAE **3.0969 m**, RMSE **3.2556 m**, Pearson r **0.999993**, **6,400/6,400** overlapping cells |
| IO LULC 2023 | 57.079% bare ground, 42.921% rangeland; WorldCover 2021 at the same window was 99.799% bare; broad-class agreement **0.57261** |
| Sentinel-2 L2A | **3** scenes; lowest cloud cover **0.500184%** (`S2B_MSIL2A_20261113T143649_R096_T19JDN_20261113T181914`) |
| Landsat C2 L2 | **3** scenes; lowest cloud cover **6.83%** (`LC09_L2SP_001077_20261024_02_T1`) |
| Probes | all five collections returned `available` with public STAC metadata and a SAS token |

`POST /api/v1/datasets/probe?limit=100` returned **20 available, 1 credential-gated
(`firms_viirs`), 0 unavailable**. The gated service was not reported as connected. API and worker
logs, and all report exports from the run below, were searched for `sig=` and `<sas>`; no token or
signed query string was found.

### 1.3 Live Meteomatics connector checks (2026-11-14)

The Meteomatics username and password were supplied through the gitignored `.env`; the API health
response reported `meteomatics_configured=true`. No credential was written to the repository.

| Check | Observed result |
|---|---|
| Probe | `POST /api/v1/datasets/probe?dataset_id=meteomatics_weather` returned `available`, HTTP **200**, latency **965 ms** |
| Full catalog | `POST /api/v1/datasets/probe?limit=100` returned **21 available, 1 credential-gated (`firms_viirs`), 0 unavailable** |
| Point forecast | `25.30°S, 69.90°W`, 48 h from `2026-11-14T07:00:00Z` at 3-hour steps, **17** samples |
| Temperature | mean **20.66 °C**, min **13.8 °C**, max **27.5 °C** |
| Wind | mean **3.15 m/s**, max **7.4 m/s**, circular mean direction **136.3°** |
| Humidity / cloud | mean humidity **26.21%**; mean cloud **14.37%**, range 0.0-74.2% |
| Precipitation | no sampled precipitation in the 48 h window |
| Credential handling | the API response `user` field is discarded; the connector output and all report exports contained neither the account username nor the password |
| Log scan | API and worker logs were searched for the username, password, and `Basic `; **0** matches |

The connector calls the documented path
`/{validdatetime}/{parameters}/{locations}/{format}` with HTTP Basic authentication. Missing
credentials produce `requires_credentials`; invalid credentials also produce
`requires_credentials` rather than `available`.

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

### 2.4 Planetary Computer cross-check run — Aristarchus Plateau

This run exercised the full catalog-0.3.0 pipeline through the local API and worker, including
all five Planetary Computer collections as evidence.

| Field | Value |
|---|---|
| Analysis ID | `58500974-41e1-4649-9a0a-adcb9f4f126e` |
| Title | Planetary Computer live verification |
| Reference | Aristarchus Plateau, Moon, `25.40°N, 47.40°W` |
| Footprint / grid | 40 km / 500 m |
| Earth search bbox | `[-70.5, -25.5, -68.5, -23.5]`, seed 42 |
| Screening / refinement | **26** coarse samples, **4** sites attempted, **4 succeeded**, **0 failed** |
| Status | `succeeded`, progress 1.0, **no context errors** |
| Manifest | catalog `0.3.0`, **10** datasets: 2 scoring DEMs and 8 context/evidence products |

| Rank | Candidate ID | Center | Similarity | Confidence | Band | Coverage |
|---:|---|---|---:|---:|---|---:|
| 1 | `e4004e13-a5d2-4c1e-8be8-305b91997e3c` | `25.30°S, 69.90°W` | **0.734385** | 0.700000 | moderate | 1.0 |
| 2 | `3ff8b704-ff6a-4233-963b-2c99ec01f93b` | `24.10°S, 68.70°W` | 0.651816 | 0.700000 | moderate | 1.0 |
| 3 | `1f7eb49c-690a-4022-8692-e173b9f3816e` | `23.70°S, 69.90°W` | 0.641907 | 0.550000 | moderate | 1.0 |

The cross-candidate mean method standard deviation was **0.176128**. This run used a different
`refine_top_n` (5) from the canonical run, so its top similarity is not directly comparable to the
earlier 0.789007 result; it is an independent execution, not a revised score.

Stored evidence for the top candidate:

| Product | Observed result |
|---|---|
| NASADEM HGT v001 | bias **+0.7250 m**, MAE **0.8688 m**, RMSE **1.1212 m**, median **+0.6310 m**, p95 absolute deviation **2.3119 m**, r **0.999992**, **6,400/6,400** cells |
| ALOS World 3D-30m | bias **+2.0033 m**, MAE **2.0044 m**, RMSE **2.1305 m**, median **+2.0225 m**, p95 absolute deviation **3.1986 m**, r **0.999994**, **6,400/6,400** cells |
| IO LULC 2023 | **76.76%** rangeland, **23.232%** bare ground, **0.004%** crops; WorldCover 2021 at the same window was **99.964%** bare; broad-class agreement **0.23266** |
| Sentinel-2 L2A | **3** scenes; selected scene `S2B_MSIL2A_20261113T143649_R096_T19JDN_20261113T181914`, cloud **0.500184%**, platform Sentinel-2B |
| Landsat C2 L2 | **3** scenes; selected scene `LC09_L2SP_001078_20261024_02_T1`, cloud **9.14%**, platform Landsat-9 |

The WorldCover and IO LULC products disagree strongly at this window. The disagreement is stored
and displayed as **23% agreement**, not hidden or resolved in favour of either product. The two
DEMs agree closely with Copernicus in this window, but that is an independent cross-check only:
vertical datums, acquisition epochs, and surface definitions differ, so it is not a product
accuracy assessment.

### 2.5 Meteomatics weather run — Aristarchus Plateau

This run exercised catalog 0.4.0 through the local API and worker, adding credentialed
Meteomatics weather evidence to the Planetary Computer cross-checks.

| Field | Value |
|---|---|
| Analysis ID | `f541712c-5000-4dc2-a6f3-f1a7db59fe0f` |
| Title | Meteomatics live verification |
| Reference | Aristarchus Plateau, Moon, `25.40°N, 47.40°W` |
| Footprint / grid | 40 km / 500 m |
| Earth search bbox | `[-70.5, -25.5, -68.5, -23.5]`, seed 42, `refine_top_n` 5 |
| Status | `succeeded`, progress 1.0, **no context errors** |
| Manifest | catalog `0.4.0`, **11** datasets: 2 scoring DEMs and 9 context/evidence products |
| Context datasets | ESA WorldCover, IO LULC, NASADEM, ALOS, **Meteomatics**, NASA GIBS, Nominatim, PC Sentinel-2, PC Landsat |

| Rank | Candidate ID | Center | Similarity | Confidence | Band | Coverage |
|---:|---|---|---:|---:|---|---:|
| 1 | `b2fdcc4c-5ef0-4c96-a4b0-fa074ebdcec0` | `25.30°S, 69.90°W` | **0.734385** | 0.700000 | moderate | 1.0 |
| 2 | `c453026f-8d95-4ae1-9fc6-0387d3b0dfcd` | `24.10°S, 68.70°W` | 0.651816 | 0.700000 | moderate | 1.0 |
| 3 | `c06775e7-6fba-4c22-9a9c-39c1d0903d59` | `23.70°S, 69.90°W` | 0.641907 | 0.550000 | moderate | 1.0 |

Scores are identical to run 2.4 because the search configuration and seed are the same; the new
product adds evidence only and does not change the ranking. Stored weather evidence for the top
candidate:

| Field | Observed result |
|---|---|
| Window | `2026-11-14T07:00:00Z` to `2026-11-16T07:00:00Z`, 3-hour steps, **17** samples, UTC |
| Temperature | mean **20.66 °C**, min **13.8 °C**, max **27.5 °C** |
| Wind | mean **3.15 m/s**, max **7.4 m/s**, circular mean direction **136.3°** |
| Humidity | mean **26.21%** |
| Cloud | mean **14.37%**, min **0.0%**, max **74.2%** |
| Precipitation | sampled maximum **0.0 mm**; sampled sum **0.0 mm** |
| Role | operational weather context, **not scored** |

The run's JSON and manifest exports contain `meteomatics_weather`; the GeoJSON, CSV, and PDF are
candidate-summary exports and do not. None of the five downloads, nor the API/worker logs,
contained the Meteomatics username or password.

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

### 3.1 Planetary Computer run exports

Five reports were generated for analysis `58500974-41e1-4649-9a0a-adcb9f4f126e`, re-downloaded,
and checked for checksum integrity and SAS-token leakage.

| Format | Report ID | Bytes | SHA-256 (downloaded bytes) | Check |
|---|---|---:|---|---|
| JSON | `1616b9a3-85b4-4f10-b7b9-c16783bb5c0e` | 131,406 | `18410eb9d76c386c714eed33b6439d5db526742b11447128dafd33e28b51e5f9` | match · no `sig=` · no `<sas>` |
| Manifest | `e99aa802-1872-41fb-b90f-dcfcce004f97` | 86,903 | `61d865710916500443bebf04a1f67f6462c70fd922a06a3cf93b11b5b9b21e9d` | match · no `sig=` · no `<sas>` |
| GeoJSON | `af0c4699-0ed8-4037-a351-61eb82bec935` | 6,015 | `4ef7a46100242b56a986f9974985bcaace58bda5c9eae11a5e2e87b14b8ee4e6` | match · no `sig=` · no `<sas>` |
| CSV | `0832f3d9-38ad-4e0a-928a-557a05e0ab3d` | 1,832 | `9e03c6985f11b1dd04b1fc4e4f74c5e0cf2e82d72e4e0f6951dff56f31c1bdcc` | match · no `sig=` · no `<sas>` |
| PDF | `62352f37-8034-4cc6-861d-88736341ed02` | 10,897 | `392a8e4a1e87437ae90ac0dc2b52545b2dbed45e1f6724d2529423a167b626b4` | match · no `sig=` · no `<sas>` |

The GeoJSON has **4** features and **no top-level `crs`**; its per-feature `body_id` values are
`earth` and `moon`. The JSON export contains the NASADEM/ALOS statistics, IO LULC agreement,
Sentinel-2/Landsat scene metadata, and unsigned preview URLs, but no signed query parameter.

### 3.2 Meteomatics run exports

Five reports were generated for analysis `f541712c-5000-4dc2-a6f3-f1a7db59fe0f`, re-downloaded,
and checked for checksum integrity and credential leakage.

| Format | Report ID | Bytes | SHA-256 (downloaded bytes) | Check |
|---|---|---:|---|---|
| JSON | `a4fe34a1-dcde-433e-bab5-668d81747e76` | 171,637 | `15e3f36b9a339f1df7ed5b9f94626f45cd64e22195881bf9650ff081603d56ed` | match · no username · no password · weather present |
| Manifest | `3e503f06-e818-4a8a-8dea-cbe755397bdd` | 127,143 | `e07dd6e01f9bc0694bb894d5b810081a4ae83a19717ea21d202f6957f14385ef` | match · no username · no password · weather present |
| GeoJSON | `a0ed4e46-a6e3-4c91-ad84-46944a84bfba` | 6,015 | `70a3523aee48352d7f31d3f2ea5b0132ba2c09a63881ea6cb395d1d8ab5cc4e5` | match · no username · no password · 4 features · no top-level `crs` |
| CSV | `aaf8fc22-ec80-4a2e-99f5-818e72e73816` | 1,832 | `e17109a37d183d6c867166769aad6bb2e38932991d355d219bdf6c3a5ccee4a4` | match · no username · no password |
| PDF | `0326cb2b-edbe-494c-af5f-552ef58dd841` | 11,116 | `976dc45d29377a481febf7ed25b706aef0ddc5e487b941e31e7230acda52ab02` | match · no username · no password |

All five downloads matched their stored SHA-256, and the connector's response `user` field was
not persisted.

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
| Data observatory | **22** products render (**16 Earth, 3 Moon, 3 Mars**), including all five Planetary Computer collections and credential-gated Meteomatics; catalog filter, scoring-input filter, body groups, and live probe controls render |
| Planetary Computer evidence | Top candidate shows NASADEM/ALOS DEM metrics, IO LULC agreement, and Sentinel-2/Landsat scene context; unsigned preview `<img>` URLs load HTTP 200 `image/png` |
| Meteomatics evidence | Top candidate shows **9** evidence records including `meteomatics_weather`; the panel renders mean 20.7 °C, wind max 7.4 m/s, sampled precipitation 0.0 mm, mean cloud 14%, humidity 26%, with an explicit forecast-only, not-scored caveat |
| Methodology | Declared formulas, weights, tolerances, and limitations render |
| Missing WebGL | Globe shows the real equirectangular source texture and an explicit fallback label |
| 3D globe (SwiftShader) | Landing and workspace globes render the real NASA Earth texture, candidate pin, and body tabs; **no console errors** |
| 3D terrain landing | Two real DEM meshes render: Aristarchus (LOLA) and the top Atacama candidate (Copernicus); **no terrain error** |
| 3D terrain workspace | Four selectable surfaces render: reference plus all three Earth candidates; each tab requests the real DEM mesh |
| 3D terrain explorer | `/terrain` lists **17 distinct surfaces** (8 Earth, 4 Moon, 5 Mars); body filters and search work; all selected meshes render |
| Terrain exaggeration | Display slider changed from `×2.0` to `×3.5`; canvas remained live with no error |
| Mobile layout (390 px) | Nav links remain visible, `/terrain` stacks to one column, 17 entries and a terrain canvas render, and the document has no horizontal overflow |

Two headless Chrome modes were used. The default headless instance had GPU/WebGL disabled and
logged Three.js `A WebGL context could not be created` messages; the application caught the
condition and rendered the real equirectangular texture fallback without an uncaught exception.
A second instance launched with SwiftShader software WebGL (`--use-gl=angle
--use-angle=swiftshader`) rendered the textured Three.js globes on the landing page and in the
workspace, with **no console errors** in either capture. Drag/zoom interaction was not scripted;
the render path itself is verified. A hardware-GPU browser was not available.

The Planetary Computer UI pass used the default headless instance. `/datasets` rendered all 22
catalog rows, the SAS-access explanation, and the credential-gated weather note. Workspace
`/analyses/58500974-41e1-4649-9a0a-adcb9f4f126e` auto-selected the top candidate and rendered:

- ALOS cross-check: **MAE 2.00 m**, RMSE 2.13 m, bias +2.00 m across 6,400 cells;
- NASADEM cross-check: **MAE 0.87 m**, RMSE 1.12 m, bias +0.72 m across 6,400 cells;
- IO LULC agreement: **23%** with WorldCover, with the taxonomy/epoch caveat;
- Landsat: cloud **9.1%**, 3 scenes, `landsat-9`;
- Sentinel-2: cloud **0.5%**, 3 scenes, `Sentinel-2B`;
- both unsigned preview URLs returned HTTP 200 `image/png` (2,184,434 and 1,204,885 bytes).

Workspace `/analyses/f541712c-5000-4dc2-a6f3-f1a7db59fe0f` then rendered the weather record
stored by run 2.5:

- `Weather context: mean 20.7 °C, wind max 7.4 m/s, sampled precipitation 0.0 mm, mean cloud
  14%, humidity 26%. Model forecast, not an in-situ measurement; not scored.`

The rendered metrics and previews came from persisted evidence records, not client-side
recomputation.

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
and 17 distinct surfaces in `/terrain` (8 Earth candidates, 4 Moon regions, 5 Mars regions). Body
filters, search, surface switching, and the display-only vertical-exaggeration control were
exercised; every terrain request returned HTTP 200, no `.terrain-error` appeared, and the console
was clean. All eight Earth candidate surfaces were also requested directly after the Planetary
Computer run: each returned an 80×80 Copernicus grid with **6,400/6,400** valid cells and elevations
between 613.5 m and 4,294.9 m. Geometry is elevation only: no imagery texture is used, missing
cells stay `null`, and triangles touching a missing cell are omitted. The unit tests assert strict
JSON `null` handling and cache round trips.

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

`landing.png` is the SwiftShader render with the real Earth texture, **22** datasets, 9 reference
regions, 11 stored analyses, the latest Meteomatics Aristarchus run (top similarity 0.734), and the
start of the real 3D terrain section. `datasets.png` shows the 22-product catalog, the Planetary
Computer access note, credential-gated Meteomatics, and the probe chips **21 available / 1 gated /
0 unknown**. `methodology.png` shows methodology version 0.4.0 with the persisted `f541712c`
configuration and the weather-context statement. `workspace.png` is the Meteomatics run with the
four-surface 3D terrain panel, five ready reports, all three candidates, the weather insight, and
the NASADEM/ALOS and IO LULC evidence metrics. `terrain.png` is the `/terrain` explorer showing
all **17** surfaces (8 Earth, 4 Moon, 5 Mars), a real Earth Copernicus mesh, and candidate labels
from the Meteomatics run. `progress.png` is the canonical run mid-execution at 10%, showing
`RUNNING`, not an error.

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
  2,663.65 m. After the cache-version change, all **14** catalog surfaces present at that time
  (5 Earth, 4 Moon,
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

1. **Container verification is a development check, not a production deployment.** The images
   build and the isolated-port Compose run passed on 2026-11-14, but the stack uses a host bind
   mount, runs the API and worker as root, has no TLS or resource limits, and was not exercised
   under load. The CI workflow is committed and YAML-validated but has not executed on GitHub
   because no remote is configured.
2. **Some credential-gated sources remain unexercised.** Meteomatics was exercised live with its
   configured account, but `NASA_EARTHDATA_TOKEN` and `NASA_FIRMS_MAP_KEY` were not configured.
   `/health` reports `earthdata_configured: false` and `meteomatics_configured: true`; the
   unconfigured rows report `requires_credentials`, not “connected”.
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

# Full container stack (build, migrations, idempotent seed)
cd ../..
docker compose config
docker compose up -d --build
curl http://localhost:8000/api/v1/health

# The verified end-to-end run used isolated ports so a local dev stack was untouched:
# PostGIS 15434, API 18000, web 13000.
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
