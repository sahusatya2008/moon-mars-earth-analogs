import Link from "next/link";
import { HeroGlobes } from "@/components/HeroGlobes";
import { StatusChip } from "@/components/StatusChip";
import { Terrain3D } from "@/components/Terrain3D";
import { api } from "@/lib/api";
import { score, when } from "@/lib/format";
import type { Analysis, AnalysisResults, Dataset, Planet, Region } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadLanding() {
  const [planets, datasets, analyses, health] = await Promise.allSettled([
    api.server.planets(),
    api.server.datasets(),
    api.server.analyses("?page_size=6"),
    api.server.health(),
  ]);

  const planetRows = planets.status === "fulfilled" ? planets.value : [];
  const datasetRows = datasets.status === "fulfilled" ? datasets.value.items : [];
  const analysisRows = analyses.status === "fulfilled" ? analyses.value.items : [];
  const healthRow = health.status === "fulfilled" ? health.value : null;
  const regionRows: Region[] = [];
  try {
    const response = await api.regions();
    regionRows.push(...(Array.isArray(response) ? response : response.items));
  } catch {
    /* API unavailable */
  }

  let latest: AnalysisResults | null = null;
  const succeeded = analysisRows.find((row) => row.status === "succeeded");
  if (succeeded) {
    try {
      latest = await api.server.results(succeeded.id);
    } catch {
      latest = null;
    }
  }

  return {
    planets: planetRows,
    datasets: datasetRows,
    regions: regionRows,
    analyses: analysisRows,
    health: healthRow,
    latest,
    online:
      planets.status === "fulfilled" &&
      datasets.status === "fulfilled" &&
      health.status === "fulfilled",
  };
}

export default async function HomePage() {
  const data = await loadLanding();
  const pins: Record<string, Array<{ lon: number; lat: number; color?: string; label?: string }>> = {
    earth: [],
    moon: [],
    mars: [],
  };
  for (const region of data.regions) {
    pins[region.body_id]?.push({
      lon: region.center_lon,
      lat: region.center_lat,
      color: region.body_id === "moon" ? "#68e0d0" : "#ff8b5e",
      label: region.name,
    });
  }
  for (const ranking of data.latest?.rankings || []) {
    pins.earth.push({
      lon: ranking.center_lon,
      lat: ranking.center_lat,
      color: "#c9f25a",
      label: ranking.display_name,
    });
  }

  const top = data.latest?.rankings[0];
  const reference = data.latest?.reference_region;

  return (
    <main>
      <section className="hero">
        <div className="shell hero-grid">
          <div>
            <div className="eyebrow">Planetary analogue intelligence · v0.4</div>
            <h1 className="display">
              <span className="terra">TERRA</span>
              <span className="x">-X</span>
            </h1>
            <p className="hero-copy">
              A Moon or Mars region is not like Earth because it looks similar. TERRA-X measures
              the terrain at a common physical scale, screens real Earth topography, and ranks
              analogues with declared tolerances. Similarity and confidence stay separate, and
              every value is traceable to its source.
            </p>
            <div className="hero-actions">
              <Link className="btn primary" href="/analyses">
                Start an analysis
              </Link>
              {data.latest ? (
                <Link className="btn" href={`/analyses/${data.latest.analysis.id}`}>
                  Inspect latest result
                </Link>
              ) : (
                <Link className="btn" href="/datasets">
                  Inspect data catalog
                </Link>
              )}
            </div>
            <div className="hero-meta">
              <div className="hero-meta-item">
                <b>{data.datasets.length || "—"} authoritative datasets</b>
                <span>NASA · USGS · ESA · Copernicus</span>
              </div>
              <div className="hero-meta-item">
                <b>{data.regions.length || "—"} reference regions</b>
                <span>Moon and Mars, curated with citations</span>
              </div>
              <div className="hero-meta-item">
                <b>{data.health?.worker_queue?.entity_counts?.analyses ?? "—"} analyses stored</b>
                <span>PostGIS provenance records</span>
              </div>
              <div className="hero-meta-item">
                <b style={{ color: data.online ? "var(--cyan)" : "var(--amber)" }}>
                  {data.online ? "Live data plane" : "API offline"}
                </b>
                <span>{data.online ? "real sources, no synthetic scores" : "start the FastAPI service"}</span>
              </div>
            </div>
          </div>
          <HeroGlobes planets={data.planets as Planet[]} pins={pins} />
        </div>
      </section>

      {!data.online && (
        <div className="shell" style={{ paddingBottom: 24 }}>
          <div className="error-panel">
            The catalog API is not reachable. The interface is not substituting sample data. Start
            the backend with <code>uvicorn terrax.main:app</code> and reload.
          </div>
        </div>
      )}

      {top && reference && (
        <section className="section">
          <div className="shell">
            <div className="section-head">
              <div>
                <div className="eyebrow cyan">Latest completed run</div>
                <h2>{reference.name} → Earth</h2>
              </div>
              <Link className="link small" href={`/analyses/${data.latest?.analysis.id}`}>
                Open workspace
              </Link>
            </div>
            <div className="panel panel-pad" style={{ display: "grid", gap: 18 }}>
              <div className="grid-4">
                <div className="stat">
                  <div className="stat-value" style={{ color: "var(--orange)" }}>
                    {score(top.overall_score)}
                  </div>
                  <div className="stat-label">Top similarity</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{score(top.confidence)}</div>
                  <div className="stat-label">Evidence confidence</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{data.latest?.rankings.length}</div>
                  <div className="stat-label">Ranked candidates</div>
                </div>
                <div className="stat">
                  <div className="stat-value">
                    {data.latest?.analysis.config.footprint_km} km
                  </div>
                  <div className="stat-label">Common footprint</div>
                </div>
              </div>
              <div className="callout calm">
                <b>{top.display_name}</b> at {top.center_lon.toFixed(2)}°, {top.center_lat.toFixed(2)}°
                ranked first with a {top.confidence_band} confidence band. This is a terrain
                analogue under the stored configuration, not a landing recommendation.
              </div>
            </div>
          </div>
        </section>
      )}

      {top && reference && (
        <section className="section">
          <div className="shell">
            <div className="section-head">
              <div>
                <div className="eyebrow cyan">Real 3D terrain</div>
                <h2>Elevation, not a reference image.</h2>
              </div>
              <p>
                These meshes are sampled from the stored DEM products used by the latest run.
                Drag to orbit and change the display-only vertical exaggeration.
              </p>
            </div>
            <div className="terrain-pair">
              <div className="panel terrain-card">
                <div className="terrain-card-head">
                  <div>
                    <div className="eyebrow">Reference · {reference.body_id}</div>
                    <h3>{reference.name}</h3>
                  </div>
                  <span className="chip idle">{reference.size_km} km</span>
                </div>
                <Terrain3D
                  body={reference.body_id}
                  lon={reference.center_lon}
                  lat={reference.center_lat}
                  sizeKm={reference.size_km}
                  label={`${reference.name} 3D terrain`}
                />
              </div>
              <div className="panel terrain-card">
                <div className="terrain-card-head">
                  <div>
                    <div className="eyebrow">Top Earth analogue</div>
                    <h3>{top.display_name}</h3>
                  </div>
                  <span className="chip idle">{top.confidence_band} confidence</span>
                </div>
                <Terrain3D
                  body="earth"
                  lon={top.center_lon}
                  lat={top.center_lat}
                  sizeKm={top.size_km}
                  label={`${top.display_name} 3D terrain`}
                />
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="section">
        <div className="shell">
          <div className="section-head">
            <div>
              <div className="eyebrow">How a run works</div>
              <h2>Measure. Screen. Refine.</h2>
            </div>
            <p>
              The reference is measured once. Earth is screened coarsely, refined only where real
              terrain is promising, then scored on a common 500 m grid. No score is generated from
              a rendered image.
            </p>
          </div>
          <div className="steps">
            <div className="step">
              <div className="step-num">01 · REFERENCE</div>
              <h3>Inspect the planetary window</h3>
              <p>
                LRO LOLA (Moon) or MGS MOLA (Mars) is read only over the selected footprint,
                resampled to a local azimuthal-equidistant grid, and reduced to physical features:
                relief, slope, curvature, texture, trafficability proxies.
              </p>
            </div>
            <div className="step">
              <div className="step-num">02 · DISCOVERY</div>
              <h3>Screen real Earth terrain</h3>
              <p>
                AWS Terrain Tiles are sampled over land cells with a compact coarse profile. The
                best cells are refined with Copernicus GLO-30 at the same footprint. The coarse
                stage is triage only; it never produces the final score.
              </p>
            </div>
            <div className="step">
              <div className="step-num">03 · EVIDENCE</div>
              <h3>Score, then expose uncertainty</h3>
              <p>
                Gaussian kernels use declared physical tolerances. Missing dimensions are excluded
                and renormalized, never imputed. Confidence combines coverage, valid pixels,
                resolution, and method agreement. Reports export the stored record.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="shell">
          <div className="section-head">
            <div>
              <div className="eyebrow">Recent analyses</div>
              <h2>Stored runs</h2>
            </div>
            <Link className="link small" href="/analyses">
              All analyses
            </Link>
          </div>
          {data.analyses.length === 0 ? (
            <div className="empty">
              No analyses yet. <Link className="link" href="/analyses">Create the first one</Link>.
            </div>
          ) : (
            <div className="analyses-list">
              {data.analyses.map((analysis: Analysis) => (
                <Link
                  className="analysis-row"
                  key={analysis.id}
                  href={`/analyses/${analysis.id}`}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{analysis.title}</div>
                    <div className="tiny faint mono">{analysis.id.slice(0, 8)}</div>
                  </div>
                  <div>
                    <StatusChip status={analysis.status} />
                  </div>
                  <div className="tiny muted">{analysis.progress ? `${Math.round(analysis.progress * 100)}%` : "—"}</div>
                  <div className="tiny muted">{when(analysis.created_at)}</div>
                  <div className="tiny faint mono">{analysis.algorithm_version}</div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="section">
        <div className="shell grid-2">
          <div className="panel panel-pad">
            <div className="eyebrow cyan">Scientific position</div>
            <h2 className="display" style={{ fontSize: 38, margin: "12px 0 16px" }}>
              Four scored dimensions. Five evidence-only dimensions.
            </h2>
            <p className="small muted">
              Cross-body scoring uses geomorphology, morphometry, multi-scale texture, and
              operational trafficability proxies. Surface albedo, thermal, water/ice, aeolian, and
              environmental context are reported only when a comparable product pair exists. An
              unavailable dimension is visible as unavailable.
            </p>
            <div style={{ marginTop: 18 }}>
              <Link className="link small" href="/methodology">
                Read the methodology
              </Link>
            </div>
          </div>
          <div className="panel panel-pad">
            <div className="eyebrow">Data observatory</div>
            <h2 className="display" style={{ fontSize: 38, margin: "12px 0 16px" }}>
              Every dataset has a source, license, and live probe.
            </h2>
            <p className="small muted">
              The catalog records NASA CMR, USGS Astrogeology, Copernicus DEM, ESA WorldCover,
              Sentinel-2 L2A, NASA GIBS, NASA FIRMS, Microsoft Planetary Computer, and
              credentialed Meteomatics weather. Availability is probed, not assumed;
              credentials are reported as a configuration state.
            </p>
            <div style={{ marginTop: 18 }}>
              <Link className="link small" href="/datasets">
                Open the observatory
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
