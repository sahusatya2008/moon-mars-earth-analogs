"use client";

import { useEffect, useMemo, useState } from "react";
import { Terrain3D } from "@/components/Terrain3D";
import { coords } from "@/lib/format";
import type { AnalysisResults } from "@/lib/types";

interface TerrainTarget {
  id: string;
  short: string;
  label: string;
  body: string;
  lon: number;
  lat: number;
  sizeKm: number;
  detail: string;
}

export function TerrainModelPanel({
  results,
  selectedId,
}: {
  results: AnalysisResults;
  selectedId: string | null;
}) {
  const targets = useMemo<TerrainTarget[]>(() => {
    const reference = results.reference_region;
    const rows: TerrainTarget[] = [
      {
        id: "reference",
        short: "Reference",
        label: `${reference.name} reference`,
        body: reference.body_id,
        lon: reference.center_lon,
        lat: reference.center_lat,
        sizeKm: reference.size_km,
        detail: `${reference.body_id.toUpperCase()} · ${coords(reference.center_lon, reference.center_lat)}`,
      },
    ];
    for (const ranking of results.rankings) {
      rows.push({
        id: ranking.candidate_id,
        short: `#${ranking.rank}`,
        label: ranking.display_name,
        body: "earth",
        lon: ranking.center_lon,
        lat: ranking.center_lat,
        sizeKm: ranking.size_km,
        detail: `EARTH · ${coords(ranking.center_lon, ranking.center_lat)} · similarity ${ranking.overall_score.toFixed(3)}`,
      });
    }
    return rows;
  }, [results]);

  const [active, setActive] = useState("reference");

  useEffect(() => {
    if (selectedId && targets.some((target) => target.id === selectedId)) {
      setActive(selectedId);
    }
  }, [selectedId, targets]);

  const target = targets.find((row) => row.id === active) || targets[0];
  if (!target) return null;

  return (
    <section className="panel terrain-panel">
      <div className="terrain-panel-head">
        <div>
          <div className="eyebrow cyan">3D terrain</div>
          <h3>Every surface as a real elevation mesh</h3>
          <p>
            The reference and every ranked Earth candidate are sampled from the same DEM
            products used by the analysis. These are geometry models, not reference photos or
            decorative textures.
          </p>
        </div>
        <span className="chip idle">{targets.length} surfaces</span>
      </div>

      <div className="terrain-tabs" role="tablist" aria-label="Choose a terrain surface">
        {targets.map((row) => (
          <button
            key={row.id}
            type="button"
            role="tab"
            aria-selected={row.id === target.id}
            className="terrain-tab"
            data-active={row.id === target.id}
            onClick={() => setActive(row.id)}
          >
            <span>{row.short}</span>
            <small>{row.body}</small>
          </button>
        ))}
      </div>

      <Terrain3D
        key={target.id}
        body={target.body}
        lon={target.lon}
        lat={target.lat}
        sizeKm={target.sizeKm}
        label={`${target.label} 3D terrain`}
      />

      <div className="terrain-panel-foot">
        <div>
          <b>{target.label}</b>
          <span>{target.detail}</span>
        </div>
        <span>
          Mesh vertices are sampled DEM elevations. Vertical exaggeration is a display control,
          not an analysis parameter.
        </span>
      </div>
    </section>
  );
}
