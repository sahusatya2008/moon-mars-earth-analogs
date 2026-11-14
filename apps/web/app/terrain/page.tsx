import { TerrainExplorer, type TerrainTarget } from "@/components/TerrainExplorer";
import { api } from "@/lib/api";
import type { Analysis, Region } from "@/lib/types";

export const dynamic = "force-dynamic";

function regionTarget(region: Region): TerrainTarget {
  return {
    id: `region-${region.id}`,
    label: region.name,
    body: region.body_id,
    lon: region.center_lon,
    lat: region.center_lat,
    sizeKm: region.size_km,
    group: `${region.body_id === "moon" ? "Moon" : "Mars"} reference regions`,
    detail: `${region.body_id.toUpperCase()} · ${region.center_lat.toFixed(2)}°, ${region.center_lon.toFixed(2)}° · ${region.size_km} km`,
  };
}

export default async function TerrainPage() {
  const [regionResponse, analysisResponse] = await Promise.allSettled([
    api.server.regions(),
    api.server.analyses("?page_size=50"),
  ]);

  const regions: Region[] =
    regionResponse.status === "fulfilled"
      ? Array.isArray(regionResponse.value)
        ? regionResponse.value
        : regionResponse.value.items
      : [];
  const analyses: Analysis[] =
    analysisResponse.status === "fulfilled" ? analysisResponse.value.items : [];

  const succeeded = analyses.filter((analysis) => analysis.status === "succeeded");
  const resultRows = await Promise.all(
    succeeded.map(async (analysis) => {
      try {
        return { analysis, results: await api.server.results(analysis.id) };
      } catch {
        return null;
      }
    }),
  );

  const earthTargets: TerrainTarget[] = [];
  const seen = new Set<string>();
  for (const row of resultRows) {
    if (!row) continue;
    for (const ranking of row.results.rankings) {
      const key = `${ranking.center_lat.toFixed(3)}:${ranking.center_lon.toFixed(3)}:${ranking.size_km}`;
      if (seen.has(key)) continue;
      seen.add(key);
      earthTargets.push({
        id: `candidate-${ranking.candidate_id}`,
        label: ranking.display_name,
        body: "earth",
        lon: ranking.center_lon,
        lat: ranking.center_lat,
        sizeKm: ranking.size_km,
        group: "Earth candidate sites",
        detail: `EARTH · ${ranking.center_lat.toFixed(2)}°, ${ranking.center_lon.toFixed(2)}° · ${ranking.size_km} km · ${row.analysis.title}`,
      });
    }
  }

  const targets = [
    ...earthTargets,
    ...regions.filter((region) => region.body_id === "moon").map(regionTarget),
    ...regions.filter((region) => region.body_id === "mars").map(regionTarget),
  ];

  return (
    <main className="shell" style={{ padding: "34px 0 70px" }}>
      <div className="section-head">
        <div>
          <div className="eyebrow cyan">Terrain observatory</div>
          <h2>Every terrain is a real 3D elevation model</h2>
          <p style={{ marginTop: 10, maxWidth: 820 }}>
            Browse the curated Moon and Mars reference windows and every distinct Earth candidate
            stored by completed analyses. Each surface is sampled on demand from the same DEM
            products used for measurement: LRO LOLA, MGS MOLA, or Copernicus GLO-30. Real NASA
            imagery is draped over the measured geometry for display only; it never contributes to
            elevation or similarity scoring.
          </p>
        </div>
        <span className="chip idle">{targets.length} surfaces</span>
      </div>

      {targets.length === 0 ? (
        <div className="error-panel">
          No terrain surfaces are available. The catalog API may be offline; this page does not
          substitute synthetic geometry. Start the backend and reload.
        </div>
      ) : (
        <TerrainExplorer targets={targets} />
      )}
    </main>
  );
}
