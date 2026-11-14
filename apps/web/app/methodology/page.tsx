import Link from "next/link";
import { api } from "@/lib/api";
import { score } from "@/lib/format";
import type { AnalysisResults, DimensionScore, FeatureDelta } from "@/lib/types";

export const dynamic = "force-dynamic";

const DIMENSION_COPY: Record<
  string,
  { label: string; weight: number; question: string; features: string }
> = {
  geomorphology: {
    label: "Geomorphology",
    weight: 0.35,
    question: "Does the terrain have the same vertical structure?",
    features: "Relief range, elevation dispersion, skew, kurtosis, hypsometric integral, entropy, relief ratio, median-centred elevation distribution.",
  },
  morphometry: {
    label: "Morphometry",
    weight: 0.3,
    question: "Are slopes and surface form distributed the same way?",
    features: "Mean and tail slope, slope spread, steep fractions, curvature, ridge and depression fractions, slope distribution.",
  },
  texture: {
    label: "Texture",
    weight: 0.2,
    question: "Does roughness repeat at the same scales and anisotropy?",
    features: "RMS roughness, tail roughness, terrain ruggedness index, TPI spread, anisotropy, fractal dimension, roughness scale ratio.",
  },
  operational: {
    label: "Operational",
    weight: 0.15,
    question: "Do simple trafficability proxies agree?",
    features: "Trafficable fraction, steep fraction, tail roughness, relief range. These are proxies, not mobility or landing analysis.",
  },
};

const DECLARED_TOLERANCES: Array<{ key: string; dimension: string; unit: string; tolerance: number }> = [
  { key: "relief_range", dimension: "geomorphology", unit: "m", tolerance: 250 },
  { key: "elevation_std", dimension: "geomorphology", unit: "m", tolerance: 120 },
  { key: "elevation_p10_p90", dimension: "geomorphology", unit: "m", tolerance: 250 },
  { key: "elevation_skew", dimension: "geomorphology", unit: "–", tolerance: 0.55 },
  { key: "elevation_kurtosis", dimension: "geomorphology", unit: "–", tolerance: 1.6 },
  { key: "hypsometric_integral", dimension: "geomorphology", unit: "–", tolerance: 0.12 },
  { key: "elevation_entropy", dimension: "geomorphology", unit: "–", tolerance: 0.12 },
  { key: "relief_ratio", dimension: "geomorphology", unit: "–", tolerance: 0.01 },
  { key: "elevation_distribution", dimension: "geomorphology", unit: "m", tolerance: 210 },
  { key: "slope_mean", dimension: "morphometry", unit: "deg", tolerance: 4 },
  { key: "slope_p95", dimension: "morphometry", unit: "deg", tolerance: 8 },
  { key: "slope_std", dimension: "morphometry", unit: "deg", tolerance: 3 },
  { key: "slope_gt_15", dimension: "morphometry", unit: "–", tolerance: 0.1 },
  { key: "slope_gt_25", dimension: "morphometry", unit: "–", tolerance: 0.08 },
  { key: "curvature_std", dimension: "morphometry", unit: "–", tolerance: 8 },
  { key: "curvature_mean_abs", dimension: "morphometry", unit: "–", tolerance: 5 },
  { key: "ridge_fraction", dimension: "morphometry", unit: "–", tolerance: 0.08 },
  { key: "depression_fraction", dimension: "morphometry", unit: "–", tolerance: 0.08 },
  { key: "slope_distribution", dimension: "morphometry", unit: "deg", tolerance: 4 },
  { key: "roughness_rms", dimension: "texture", unit: "m", tolerance: 8 },
  { key: "roughness_p95", dimension: "texture", unit: "m", tolerance: 12 },
  { key: "tri_mean", dimension: "texture", unit: "m", tolerance: 10 },
  { key: "tpi_std", dimension: "texture", unit: "m", tolerance: 8 },
  { key: "anisotropy", dimension: "texture", unit: "–", tolerance: 0.15 },
  { key: "fractal_dimension", dimension: "texture", unit: "–", tolerance: 0.12 },
  { key: "roughness_scale_ratio", dimension: "texture", unit: "–", tolerance: 0.3 },
  { key: "trafficable_fraction", dimension: "operational", unit: "–", tolerance: 0.15 },
  { key: "steep_fraction", dimension: "operational", unit: "–", tolerance: 0.08 },
  { key: "roughness_p95_ops", dimension: "operational", unit: "m", tolerance: 12 },
  { key: "relief_range_ops", dimension: "operational", unit: "m", tolerance: 250 },
];

async function loadLatest(): Promise<AnalysisResults | null> {
  try {
    const response = await api.server.analyses("?page_size=20");
    const candidate = response.items.find((row) => row.status === "succeeded");
    if (!candidate) return null;
    return await api.server.results(candidate.id);
  } catch {
    return null;
  }
}

function measuredTolerances(results: AnalysisResults | null) {
  const top = results?.rankings[0];
  if (!top) return null;
  const rows: Array<{ key: string; dimension: string; unit: string; tolerance: number; measured?: FeatureDelta }> = [];
  for (const [dimension, detail] of Object.entries(top.dimension_scores)) {
    const scores = detail as DimensionScore;
    for (const feature of scores.features) {
      rows.push({
        key: feature.key,
        dimension,
        unit: feature.unit,
        tolerance: feature.tolerance,
        measured: feature,
      });
    }
  }
  return rows.length > 0 ? rows : null;
}

export default async function MethodologyPage() {
  const results = await loadLatest();
  const config = results?.analysis.config;
  const weights = config?.weights ?? Object.fromEntries(Object.entries(DIMENSION_COPY).map(([key, value]) => [key, value.weight]));
  const measured = measuredTolerances(results);
  const toleranceRows = DECLARED_TOLERANCES.map((row) => ({
    ...row,
    measured: measured?.find((m) => m.key === row.key)?.measured ?? null,
  }));

  return (
    <main className="shell" style={{ padding: "34px 0 70px" }}>
      <div className="section-head">
        <div>
          <div className="eyebrow">Methodology · version 0.4.0</div>
          <h2>Transparent by construction</h2>
          <p style={{ marginTop: 10, maxWidth: 820 }}>
            TERRA-X measures terrain, not appearances. The reference window and each Earth
            candidate are reduced to the same physical footprint and grid before any comparison.
            Similarity and confidence are computed separately, every tolerance is declared in
            advance, and unavailable evidence stays visible as unavailable. Weather context from
            Meteomatics is reported beside candidate evidence when credentials are configured; it
            never enters the similarity score or confidence.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <span className="chip ok">algorithm 0.2.0</span>
          <span className="chip idle">features 0.2.0</span>
          {results ? <span className="chip live">live config from {results.analysis.id.slice(0, 8)}</span> : null}
        </div>
      </div>

      <div className="panel panel-pad" style={{ marginBottom: 24 }}>
        <div className="grid-4">
          <div className="stat">
            <div className="stat-value">{config?.footprint_km ?? 40} km</div>
            <div className="stat-label">Common footprint</div>
          </div>
          <div className="stat">
            <div className="stat-value">{config?.analysis_resolution_m ?? 500} m</div>
            <div className="stat-label">Scoring grid</div>
          </div>
          <div className="stat">
            <div className="stat-value">{Object.keys(weights).length}</div>
            <div className="stat-label">Scored dimensions</div>
          </div>
          <div className="stat">
            <div className="stat-value">{toleranceRows.length}</div>
            <div className="stat-label">Declared feature kernels</div>
          </div>
        </div>
        <div className="callout calm" style={{ marginTop: 14 }}>
          The numbers on this page are the declared algorithm constants. Where a completed run
          exists, dimension weights and measured feature tolerances are read from its persisted
          configuration and rankings; otherwise the declared defaults are shown. No explanatory
          text changes a stored score.
        </div>
      </div>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="section-head">
          <div>
            <div className="eyebrow cyan">Pipeline</div>
            <h2>One physical scale, end to end</h2>
          </div>
        </div>
        <div className="steps">
          <div className="step">
            <div className="step-num">01 · REFERENCE WINDOW</div>
            <h3>Read the planetary DEM</h3>
            <p>
              Moon windows come from the USGS LRO LOLA 128 ppd ISIS cube; Mars windows from the
              USGS MGS MOLA 463 m striped GeoTIFF. Only the footprint is read (windowed range
              requests), then resampled to a local azimuthal-equidistant grid at{" "}
              {config?.analysis_resolution_m ?? 500} m.
            </p>
          </div>
          <div className="step">
            <div className="step-num">02 · GLOBAL SCREEN</div>
            <h3>Triage real Earth topography</h3>
            <p>
              AWS Terrain Tiles are sampled over land cells at{" "}
              {config?.search.screening_resolution_km ?? 5} km to produce a compact coarse profile.
              This stage is explicitly triage: it narrows where to look and never produces the
              published similarity score.
            </p>
          </div>
          <div className="step">
            <div className="step-num">03 · REFINEMENT</div>
            <h3>Re-measure finalists at full detail</h3>
            <p>
              The top {config?.search.refine_top_n ?? 8} coarse sites are read from Copernicus DEM
              GLO-30, resampled to the same {config?.footprint_km ?? 40} km /{" "}
              {config?.analysis_resolution_m ?? 500} m grid, and reduced with the same feature
              code used for the reference.
            </p>
          </div>
          <div className="step">
            <div className="step-num">04 · SCORING & EVIDENCE</div>
            <h3>Rank, then test the ranking</h3>
            <p>
              Primary ranking is weighted MCDA with Gaussian kernels on physical deltas and
              Wasserstein distance on distributions. Secondary aggregations are recomputed from
              the same tolerance-normalized deltas; their disagreement lowers confidence rather
              than being averaged away.
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <div className="eyebrow cyan">Scored dimensions</div>
            <h2>What actually moves the ranking</h2>
          </div>
          <p style={{ maxWidth: 520 }}>
            Default run weights are shown. A run may store different weights; the workspace and
            reports always show the weights used by that run. Missing dimensions are excluded and
            the remaining configured weights are renormalized — they are never replaced with a
            perfect match.
          </p>
        </div>
        <div className="method-grid">
          {Object.entries(DIMENSION_COPY).map(([key, copy]) => {
            const weight = weights[key] ?? copy.weight;
            return (
              <div className="method-card" key={key}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
                  <b>{copy.label}</b>
                  <div className="method-weight">{(weight * 100).toFixed(0)}%</div>
                </div>
                <p className="small muted" style={{ margin: "10px 0 8px", lineHeight: 1.6 }}>{copy.question}</p>
                <p className="tiny faint" style={{ margin: 0, lineHeight: 1.65 }}>{copy.features}</p>
              </div>
            );
          })}
        </div>
        <div className="panel panel-pad" style={{ marginTop: 14 }}>
          <div className="grid-2">
            <div>
              <div className="eyebrow">Evidence-only dimensions</div>
              <p className="small muted" style={{ lineHeight: 1.65 }}>
                Surface albedo, thermal inertia, water/ice indicators, aeolian context,
                environmental setting, and credentialed weather context are not scored
                cross-body. Their products are not
                physically comparable at a common scale, or no authoritative product pair exists
                for the reference and candidate. They remain in the catalog and can appear as
                context evidence, never as a hidden similarity term.
              </p>
            </div>
            <div>
              <div className="eyebrow">What is never used</div>
              <p className="small muted" style={{ lineHeight: 1.65 }}>
                Rendered globe imagery, colour mosaics, hand-entered scores, candidate names, and
                elevation above an arbitrary datum do not enter the ranking. Elevation statistics
                are centred on each window&apos;s own median; absolute cross-body elevation is not
                compared.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <div className="eyebrow cyan">Similarity kernel</div>
            <h2>Declared tolerances, not pool statistics</h2>
          </div>
        </div>
        <div className="panel panel-pad">
          <div className="formula">
            <div>
              <span className="formula-label">per-feature similarity</span>
              s = exp(−½ · (Δ / τ)²)
            </div>
            <div>
              <span className="formula-label">distribution similarity</span>
              s = exp(−W₁(P, Q) / τ)
            </div>
            <div>
              <span className="formula-label">dimension score</span>
              S<sub>d</sub> = Σ w<sub>f</sub> s<sub>f</sub> / Σ w<sub>f</sub>
            </div>
            <div>
              <span className="formula-label">overall similarity</span>
              S = Σ W<sub>d</sub> S<sub>d</sub> / Σ W<sub>d</sub>
            </div>
          </div>
          <p className="small muted" style={{ lineHeight: 1.7, marginTop: 16 }}>
            τ is a physical 1-sigma tolerance declared per feature before scoring. Δ is the
            candidate minus reference value in the feature&apos;s own unit. W₁ is the
            one-dimensional Wasserstein distance between median-centred elevation or slope
            distributions. Weights are declared, not fitted; when a feature is missing the
            dimension&apos;s coverage is recomputed from the weights that remain, and the dimension
            is excluded entirely below the configured coverage floor
            {config ? ` (${(config.min_feature_coverage * 100).toFixed(0)}% in the latest run)` : ""}.
          </p>
          <div className="table-scroll" style={{ marginTop: 18 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Dimension</th>
                  <th>Unit</th>
                  <th>Tolerance τ</th>
                  <th>Latest measured Δ</th>
                </tr>
              </thead>
              <tbody>
                {toleranceRows.map((row) => (
                  <tr key={row.key}>
                    <td className="mono">{row.key}</td>
                    <td className="tiny muted">{row.dimension}</td>
                    <td className="tiny muted">{row.unit}</td>
                    <td className="mono">{row.tolerance}</td>
                    <td className="mono tiny">
                      {row.measured
                        ? row.measured.delta === null
                          ? `distribution · s=${score(row.measured.similarity)}`
                          : `${row.measured.delta.toFixed(3)} · s=${score(row.measured.similarity)}`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="tiny faint" style={{ marginTop: 10 }}>
            Measured deltas are shown only when the latest top candidate has that feature. A dash
            means no persisted measurement in the current run, not a zero difference.
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <div className="eyebrow cyan">Confidence</div>
            <h2>Evidence quality is not similarity</h2>
          </div>
          <p style={{ maxWidth: 560 }}>
            A candidate can resemble the reference and still have weak evidence. Confidence scores
            the evidence, then caps itself when independent similarity methods disagree.
          </p>
        </div>
        <div className="panel panel-pad">
          <div className="grid-2">
            <div>
              <div className="formula compact">
                <div>
                  <span className="formula-label">confidence</span>
                  0.25·D + 0.20·F + 0.15·V + 0.15·R + 0.25·A − U
                </div>
              </div>
              <ul className="method-list">
                <li><b>D</b> — weighted fraction of scored dimensions that are available.</li>
                <li><b>F</b> — weighted fraction of declared features measured for both windows.</li>
                <li><b>V</b> — minimum valid-pixel fraction in the two footprints.</li>
                <li><b>R</b> — resolution factor, clipped to [0.2, 1.0], from native to analysis grid.</li>
                <li><b>A</b> — method agreement, 1 − std(method scores) / 0.25, clipped to [0, 1].</li>
                <li><b>U</b> — slope uncertainty penalty from vertical error and grid spacing, capped at 0.12.</li>
              </ul>
            </div>
            <div>
              <div className="eyebrow">Bands and ceilings</div>
              <div className="confidence-scale">
                <div><span className="chip ok">high</span><span className="mono tiny">≥ 0.75</span></div>
                <div><span className="chip warn">moderate</span><span className="mono tiny">≥ 0.55</span></div>
                <div><span className="chip bad">low</span><span className="mono tiny">≥ 0.35</span></div>
                <div><span className="chip bad">very low</span><span className="mono tiny">&lt; 0.35</span></div>
              </div>
              <ul className="method-list" style={{ marginTop: 16 }}>
                <li>Agreement below <b>0.30</b> caps confidence at <b>0.55</b>.</li>
                <li>Agreement below <b>0.45</b> caps confidence at <b>0.70</b>.</li>
                <li>
                  Method agreement is the spread of MCDA, weighted Euclidean, standardized
                  Euclidean, cosine, Wasserstein, and percentile scores over the same
                  tolerance-normalized feature vector.
                </li>
                <li>Confidence is not a probability. A 0.90 band means the stored evidence is internally consistent, not that the site is safe or usable.</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <div className="eyebrow cyan">Reproducibility</div>
            <h2>Everything needed to re-run is stored</h2>
          </div>
        </div>
        <div className="panel panel-pad">
          <div className="grid-3">
            <div>
              <div className="label">Run record</div>
              <p className="small muted" style={{ lineHeight: 1.65 }}>
                Algorithm and feature-schema versions, weights, methods, coverage floor, missing
                data policy, footprint, grid spacing, search area, seed, and every source product
                are persisted with the analysis and repeated in its manifest report.
              </p>
            </div>
            <div>
              <div className="label">Candidate record</div>
              <p className="small muted" style={{ lineHeight: 1.65 }}>
                Each ranking stores dimension scores, feature deltas and kernels, method scores,
                missing dimensions, strongest and weakest dimensions, limitations, coordinates,
                geometry, and a link to the analysis that produced it.
              </p>
            </div>
            <div>
              <div className="label">Exports</div>
              <p className="small muted" style={{ lineHeight: 1.65 }}>
                PDF, CSV, GeoJSON, JSON, and manifest reports are generated from stored rows, not
                recomputed in the browser. Each report exposes its SHA-256 checksum and size.
              </p>
            </div>
          </div>
          <div className="callout" style={{ marginTop: 18 }}>
            <b>Scope.</b> TERRA-X ranks terrain analogues under an explicit configuration. It does
            not model geology, mineralogy, radiation, dust, thermal cycling, illumination, or
            mission operations, and it does not recommend landing sites.{" "}
            <Link className="link" href="/datasets">See the source catalog</Link> for the products
            that are present and their limits.
          </div>
        </div>
      </section>

      <div className="section-head" style={{ marginTop: 10 }}>
        <div>
          <div className="eyebrow">Continue</div>
          <h2>Inspect a real run</h2>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link className="btn" href="/datasets">Open data observatory</Link>
          <Link className="btn primary" href="/analyses">Start an analysis</Link>
        </div>
      </div>
    </main>
  );
}
