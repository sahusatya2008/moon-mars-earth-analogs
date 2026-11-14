# TERRA-X Scientific Methodology

**Version:** 0.4.0 · algorithm `analogue-0.2.0` · feature schema `features-0.2.0` · catalog `0.4.0`

This document describes what the code actually computes. Declared constants are in
`apps/api/terrax/engine/features.py` and `apps/api/terrax/engine/similarity.py`; each completed run
also persists its own configuration, weights, manifest, and per-feature values.

## 1. Position and scope

An analogue is a *multidimensional, evidence-weighted similarity under a stated configuration*. It
is not an identity claim, a probability, a geological map, or a landing-safety assessment.

TERRA-X reports three different things separately:

- **similarity** — how close a candidate's measured physical feature vector is to the reference;
- **confidence** — how complete, valid, resolved, and method-consistent the evidence is;
- **evidence** — the source product, citation, native resolution, processing method, uncertainty,
  and valid-pixel fraction behind every measurement.

A high similarity with weak evidence is shown as a lead. A low similarity with excellent evidence
is still a low similarity. Neither value is allowed to masquerade as the other.

## 2. One physical scale

Default footprint: **40 × 40 km**. Default scoring grid: **500 m**. Reference and candidate windows
are both resampled to a local azimuthal-equidistant grid in metres before any feature is computed.

| Body | Primary DEM | Native resolution | Access path |
|---|---|---:|---|
| Moon | LRO LOLA global DEM, 128 pixels/degree (USGS ISIS tiled cube) | ≈237 m/pixel | per-tile reads with local disk cache |
| Mars | MGS MOLA global DEM mosaic | 463 m | windowed striped-TIFF reader with local crop cache |
| Earth (refinement) | Copernicus DEM GLO-30 | 30 m | windowed COG reads |
| Earth (screening only) | AWS Terrain Tiles (Mapzen/Tilezen) | z/x/y mosaic, sampled at 5 km | tiles used only to choose refinement sites |

Earth candidates are re-measured from Copernicus GLO-30 whenever a tile is available. If a
Copernicus tile is unavailable, the fallback is AWS Terrain Tiles and the product is recorded
explicitly in the candidate provenance and manifest. The coarse stage never produces the final
ranked score.

### Independent cross-checks

The Microsoft Planetary Computer supplies four context-only checks. Meteomatics adds a fifth,
credential-gated weather check. None of them changes a similarity score, confidence, or candidate
rank.

| Product | Role | Comparison |
|---|---|---|
| NASADEM HGT v001 (30 m) | independent DEM | bias, MAE, RMSE, median difference, p95 absolute deviation, Pearson r, overlap count against the candidate Copernicus window |
| ALOS World 3D-30m DSM (30 m) | independent DEM | same statistics against Copernicus |
| IO 10 m Annual LULC V2 | land-cover cross-check | broad-class total-variation agreement with ESA WorldCover, with years and taxonomies recorded |
| Sentinel-2 L2A and Landsat C2 L2 | optical context | recent scenes, cloud cover, platform, STAC item link, unsigned preview |
| Meteomatics weather (credentialed) | operational weather context | 48-hour point forecast: temperature, wind speed/direction, humidity, sampled precipitation, and cloud cover |

DEM statistics are computed only on cells valid in both products. Vertical datums and acquisition
epochs differ, so the statistics are a product-agreement check, not an accuracy assessment.
Land-cover agreement maps both legends to broad classes before comparing distributions; the
products use different class definitions and epochs, so disagreement is reported rather than
resolved by preference. Planetary Computer data access uses public STAC metadata and automatic
short-lived SAS tokens; tokens are never persisted. Meteomatics uses HTTP Basic authentication,
and neither the account identity nor the password is written to evidence, manifests, reports, or
logs. Weather values are forecast model output, not in-situ measurements.

Absolute elevation is not compared across bodies. Earth uses an orthometric datum, the Moon uses a
sphere-referenced radius, and Mars uses the MOLA areoid. The elevation **distribution** feature is
median-centred inside each window; `elevation_mean`, `elevation_min`, and `elevation_max` are stored
for context with zero scoring weight.

Resampling suppresses texture below 500 m. That is a stated limitation, not a hidden correction.

## 3. Scored feature set

A feature with weight `0.0` is measured for context but never enters a score.

### Geomorphology — 35% default dimension weight

| Feature | Unit | τ | Weight | Definition |
|---|---|---:|---:|---|
| `relief_range` | m | 250 | 0.20 | p95 − p05 of valid elevation |
| `elevation_std` | m | 120 | 0.10 | standard deviation of valid elevation |
| `elevation_p10_p90` | m | 250 | 0.10 | p90 − p10 |
| `elevation_skew` | – | 0.55 | 0.05 | Fisher–Pearson skewness |
| `elevation_kurtosis` | – | 1.6 | 0.04 | excess kurtosis |
| `hypsometric_integral` | – | 0.12 | 0.08 | `(mean − min) / (max − min)` |
| `elevation_entropy` | – | 0.12 | 0.06 | Shannon entropy of a 32-bin histogram / log(32) |
| `relief_ratio` | – | 0.010 | 0.05 | relief / window width |
| `elevation_distribution` | m | 210 | 0.12 | 1-Wasserstein distance of median-centred quantiles |

### Morphometry — 30% default dimension weight

| Feature | Unit | τ | Weight | Definition |
|---|---|---:|---:|---|
| `slope_mean` | deg | 4.0 | 0.15 | mean Horn gradient magnitude |
| `slope_p95` | deg | 8.0 | 0.08 | 95th percentile slope |
| `slope_std` | deg | 3.0 | 0.07 | slope standard deviation |
| `slope_gt_15` | – | 0.10 | 0.09 | fraction steeper than 15° |
| `slope_gt_25` | – | 0.08 | 0.06 | fraction steeper than 25° |
| `curvature_std` | – | 8.0 | 0.08 | std of dimensionless Laplacian curvature |
| `curvature_mean_abs` | – | 5.0 | 0.04 | mean absolute dimensionless curvature |
| `ridge_fraction` | – | 0.08 | 0.08 | fraction with 2.5 km TPI > +25 m |
| `depression_fraction` | – | 0.08 | 0.08 | fraction with 2.5 km TPI < −25 m |
| `slope_distribution` | deg | 4.0 | 0.12 | 1-Wasserstein distance of slope quantiles |

### Texture — 20% default dimension weight

| Feature | Unit | τ | Weight | Definition |
|---|---|---:|---:|---|
| `roughness_rms` | m | 8.0 | 0.20 | std of the 1 km high-pass residual |
| `roughness_p95` | m | 12.0 | 0.10 | p95 of absolute 1 km residual |
| `tri_mean` | m | 10.0 | 0.14 | terrain ruggedness index, 8-neighbour mean |
| `tpi_std` | m | 8.0 | 0.09 | std of 2.5 km topographic position index |
| `anisotropy` | – | 0.15 | 0.14 | gradient structure-tensor anisotropy |
| `fractal_dimension` | – | 0.12 | 0.14 | `3 − H` from variogram power law |
| `roughness_scale_ratio` | – | 0.30 | 0.10 | roughness(1 km) / roughness(4 km) |

### Operational — 15% default dimension weight

| Feature | Unit | τ | Weight | Definition |
|---|---|---:|---:|---|
| `trafficable_fraction` | – | 0.15 | 0.40 | fraction slope < 15° and \|1 km residual\| < 10 m |
| `steep_fraction` | – | 0.08 | 0.20 | fraction slope > 25° |
| `roughness_p95_ops` | m | 12.0 | 0.20 | operational roughness proxy |
| `relief_range_ops` | m | 250 | 0.20 | operational relief proxy |

These are terrain proxies for comparative research. They are not rover mobility, landing-ellipse,
bearing-capacity, or trafficability certification.

### Context only

`elevation_mean`, `elevation_min`, `elevation_max`, and `valid_fraction` have score weight `0.0`.
They are displayed and exported but never move the ranking.

### Evidence-only dimensions

`surface`, `thermal`, `water_ice`, `aeolian`, and `environment` are not scored across bodies in this
build. The catalog records candidate products, but a comparable physical measurement pair is
required before they could be used. Rendered colour, dark patches, and air temperature are never
substituted for surface albedo, ice, or surface temperature.

## 4. Similarity kernels

Feature similarities use declared physical tolerances, not statistics fitted to the candidate pool:

```
s = exp(−½ · (Δ / τ)²)
```

where `Δ = candidate − reference` in the feature's own unit and `τ` is the declared 1-sigma
tolerance in the table above.

Distribution features compare quantile functions:

```
s = exp(−W₁(P, Q) / τ)
```

`W₁` is the mean absolute difference between 101 quantiles reconstructed from the stored
histograms. Elevation histograms are median-centred before comparison.

Dimension score:

```
S_d = sqrt(coverage_d) · Σ_{f∈valid} w_f s_f / Σ_{f∈valid} w_f
coverage_d = Σ_{f∈valid} w_f / Σ_{f∈declared} w_f
```

The `sqrt(coverage_d)` factor is the missing-data penalty. A dimension with half its declared
weight measured cannot score as a perfect match even if the measured half agrees exactly.

Missing-data policy:

- `penalize` (default) — dimensions at or above the coverage floor are included with the
  `sqrt(coverage)` penalty; below the floor they are excluded.
- `exclude_dimension` — a dimension with any missing declared feature is excluded, in addition to
  the coverage floor.

In both cases unavailable dimensions are removed from the overall weighted mean and the remaining
configured weights are renormalized:

```
S = Σ_{d∈available} W_d S_d / Σ_{d∈available} W_d
```

Default dimension weights (configurable per run): geomorphology 0.35, morphometry 0.30, texture
0.20, operational 0.15. Runs persist the weights they actually used.

## 5. Secondary methods and agreement

The primary rank is the MCDA score above. Secondary aggregations are recomputed from the same
tolerance-normalized deltas so that disagreement reflects the data, not an artefact of pool size:

- **weighted Euclidean** — exponential of weighted mean squared normalized delta;
- **standardized Euclidean** — exponential of mean squared normalized delta;
- **cosine** — agreement between the per-feature match profile and an all-match profile;
- **Wasserstein** — distribution-feature agreement;
- **Mahalanobis** — shrunk covariance, only when the candidate pool is large enough;
- **percentile** — pool-relative percentile similarity.

Pool-relative standardization is deliberately not used for the primary kernel: with a handful of
candidates, tiny pool variance would manufacture artificial disagreement.

Method agreement is:

```
A = clip(1 − std(method scores) / 0.25, 0, 1)
```

A missing method does not count as agreement. If fewer than two methods are available, `A = 0.5`.
The per-method means, standard deviations, and sample counts are persisted in the result summary.

## 6. Confidence

Confidence is a bounded evidence score, not a probability:

```
C = 0.25·D + 0.20·F + 0.15·V + 0.15·R + 0.25·A − U
```

| Term | Meaning |
|---|---|
| `D` | configured dimension weight with an available score / total configured weight |
| `F` | coverage-weighted mean feature coverage of available dimensions |
| `V` | `min(reference valid fraction, candidate valid fraction)` |
| `R` | `clip(sqrt(analysis_resolution / max native resolution), 0.2, 1.0)` |
| `A` | method agreement above |
| `U` | slope uncertainty penalty: `clip(atan(√2·σz / Δx) / 45°, 0, 0.12)` |

Disagreement ceilings prevent complete evidence from hiding inconsistent methods:

- `A < 0.30` caps confidence at **0.55**;
- `A < 0.45` caps confidence at **0.70**.

Bands:

| Band | Confidence |
|---|---:|
| high | ≥ 0.75 |
| moderate | ≥ 0.55 |
| low | ≥ 0.35 |
| very low | < 0.35 |

`evidence_coverage` is the weighted fraction of scored dimension weight that is available. It is
reported beside confidence and is not the same quantity.

## 7. Discovery and refinement

The user supplies a search bounding box and grid configuration.

1. **Screening mosaic** — AWS Terrain Tiles are assembled for the bbox at a zoom chosen from the
   bbox span and sampled at 5 km (default).
2. **Coarse grid** — centers are generated on a regular lat/lon grid (default 2°, configurable),
   excluding polar cells beyond 85°.
3. **Coarse profile** — each cell receives a compact terrain profile and a coarse similarity. This
   is triage only.
4. **Densification** — the best coarse cells are sampled on a 5×5 sub-grid to avoid missing nearby
   structure.
5. **Clustering** — greedy haversine clustering (default 100 km) keeps spatially distinct
   refinement sites. `refine_top_n` defaults to 24.
6. **Refinement** — each site is read from Copernicus GLO-30 over the common footprint and
   resampled to 500 m. A site with less than 25% valid coverage is rejected.
7. **Final scoring** — the full declared feature set is measured and scored. Final candidates are
   spatially thinned again to `max_candidates`.

Coarse scores are stored with the run but are never displayed as final similarity. Any coarse
screen can miss a small analogue; that limitation is repeated in the result manifest.

## 8. Reproducibility

Every analysis stores:

- algorithm and feature-schema versions;
- full config: reference region, footprint, grid, dimensions, dimension weights, methods,
  coverage floor, missing-data policy, bbox, cell size, screening resolution, refinement count,
  maximum candidates, clustering radius, seed, and polar exclusion;
- source product IDs, URLs, citations, licenses, native resolution, and method text;
- per-feature values, units, uncertainty, valid fraction, and distribution metadata;
- per-dimension scores, feature deltas, kernels, method scores, and missing dimensions;
- job events, timings, and worker IDs;
- report checksums.

Re-running with the same immutable source products and version reproduces the same ranking.
Copernicus tiles, cached planetary windows, and gazetteer responses are recorded so a later change
at a provider does not silently rewrite the provenance of an old run.

## 9. Validation

- Algorithm tests use synthetic fixtures with known invariants: identical windows score 1.0,
  absolute elevation offsets do not change similarity, rougher candidates score lower, missing
  dimensions are excluded rather than imputed, kernels match declared tolerances, and scores are
  bounded and deterministic.
- Integration tests exercise the real PostGIS schema, job claiming/retry/cancellation semantics,
  API contracts, report generation, and the retry route.
- End-to-end validation runs use the real LOLA/MOLA references and real Earth DEM windows. The
  results are stored, not hardcoded; a known site is never injected into the candidate pool.

## 10. Known limitations

- Terrain similarity is scale-dependent; 500 m harmonisation suppresses sub-kilometre texture.
- Absolute elevation is not cross-body comparable; only relative shape, slope, and texture are
  scored.
- Vertical datums and product uncertainty differ; the uncertainty penalty is an approximation.
- The Moon and Mars references are single DEM products; mineralogy, regolith thickness, and
  mechanical properties are not measured.
- Thermal, albedo, water/ice, aeolian, and environmental dimensions are unavailable for cross-body
  scoring in this build.
- Coarse screening at 5 km can miss small, locally exceptional sites. Refinement only visits the
  selected sites.
- Gazetteer names come from OpenStreetMap Nominatim when the service returns a result; otherwise a
  coordinate label is used.
- Similarity is heuristic and configuration-dependent. It is not a probability, a geological
  interpretation, or a mission recommendation.
