"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { BodyId, Region } from "@/lib/types";

const BBOX_PRESETS: Array<{ id: string; label: string; bbox: [number, number, number, number] }> = [
  { id: "atacama", label: "Atacama", bbox: [-70.5, -25.5, -68.5, -23.5] },
  { id: "death-valley", label: "Death Valley", bbox: [-117.5, 35.5, -115.5, 37.0] },
  { id: "dry-valleys", label: "Antarctic Dry Valleys", bbox: [160.0, -78.0, 164.0, -76.0] },
  { id: "iceland", label: "Iceland", bbox: [-22.5, 63.5, -15.5, 66.5] },
  { id: "andes", label: "High Andes", bbox: [-70.0, -30.0, -66.0, -26.0] },
  { id: "global", label: "Global land", bbox: [-180.0, -60.0, 180.0, 80.0] },
];

const BODY_LABEL: Record<BodyId, string> = {
  earth: "Earth",
  moon: "Moon",
  mars: "Mars",
};

export function CreateAnalysisForm() {
  const router = useRouter();
  const [regions, setRegions] = useState<Region[]>([]);
  const [body, setBody] = useState<BodyId>("moon");
  const [regionId, setRegionId] = useState("");
  const [title, setTitle] = useState("");
  const [preset, setPreset] = useState("atacama");
  const [bbox, setBbox] = useState<[number, number, number, number]>([-70.5, -25.5, -68.5, -23.5]);
  const [footprint, setFootprint] = useState(40);
  const [refineTopN, setRefineTopN] = useState(8);
  const [maxCandidates, setMaxCandidates] = useState(6);
  const [clusterRadius, setClusterRadius] = useState(100);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .regions()
      .then((response) => {
        if (cancelled) return;
        const rows = Array.isArray(response) ? response : response.items;
        setRegions(rows);
      })
      .catch(() => {
        if (!cancelled) setRegions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const bodyRegions = useMemo(() => regions.filter((region) => region.body_id === body), [regions, body]);
  const selectedRegion = bodyRegions.find((region) => region.id === regionId) || bodyRegions[0];

  useEffect(() => {
    if (bodyRegions.length > 0 && !bodyRegions.some((region) => region.id === regionId)) {
      setRegionId(bodyRegions[0].id);
    }
  }, [bodyRegions, regionId]);

  useEffect(() => {
    if (!title && selectedRegion) {
      setTitle(`${selectedRegion.name} analogue search`);
    }
  }, [selectedRegion, title]);

  function choosePreset(id: string) {
    const found = BBOX_PRESETS.find((item) => item.id === id);
    if (!found) return;
    setPreset(found.id);
    setBbox(found.bbox);
  }

  function updateBbox(index: number, value: number) {
    setBbox((current) => {
      const next = [...current] as [number, number, number, number];
      next[index] = value;
      return next;
    });
    setPreset("custom");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedRegion) {
      setError("No reference region is available for this body. Check that the API catalog is seeded.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const analysis = await api.createAnalysis({
        title: title.trim() || `${selectedRegion.name} analogue search`,
        reference_region_id: selectedRegion.id,
        config: {
          footprint_km: footprint,
          analysis_resolution_m: 500,
          search: {
            bbox,
            grid_mode: "latlon",
            cell_size_deg: 1.0,
            screening_resolution_km: 5.0,
            refine_top_n: refineTopN,
            max_candidates: maxCandidates,
            cluster_radius_km: clusterRadius,
            random_seed: 42,
            exclude_polar_gt_deg: 85.0,
          },
        },
      });
      router.push(`/analyses/${analysis.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Analysis request failed.");
      }
      setSubmitting(false);
    }
  }

  return (
    <form className="panel panel-pad" onSubmit={submit} style={{ display: "grid", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
        <div>
          <div className="eyebrow">New analysis</div>
          <h2 style={{ marginTop: 8, fontSize: 22, fontWeight: 600 }}>Measure a reference, search Earth</h2>
        </div>
        <span className="chip idle">real data only</span>
      </div>

      <div className="field">
        <span className="label">Reference body</span>
        <div className="body-switch">
          {(["moon", "mars"] as BodyId[]).map((item) => (
            <button
              type="button"
              key={item}
              className="body-option"
              data-active={body === item}
              onClick={() => {
                setBody(item);
                setRegionId("");
              }}
            >
              {BODY_LABEL[item]}
            </button>
          ))}
        </div>
      </div>

      <div className="form-grid">
        <div className="field">
          <label htmlFor="region">Reference region</label>
          <select
            id="region"
            className="select"
            value={selectedRegion?.id || ""}
            onChange={(event) => setRegionId(event.target.value)}
            disabled={bodyRegions.length === 0}
          >
            {bodyRegions.length === 0 ? <option value="">No seeded regions</option> : null}
            {bodyRegions.map((region) => (
              <option key={region.id} value={region.id}>
                {region.name} · {region.center_lat.toFixed(1)}°, {region.center_lon.toFixed(1)}°
              </option>
            ))}
          </select>
          {selectedRegion ? (
            <span className="tiny faint">{selectedRegion.description}</span>
          ) : null}
        </div>

        <div className="field">
          <label htmlFor="title">Run title</label>
          <input
            id="title"
            className="input"
            value={title}
            maxLength={300}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Jezero delta analogue search"
          />
        </div>
      </div>

      <div className="field">
        <span className="label">Earth search window</span>
        <div className="bbox-options">
          {BBOX_PRESETS.map((item) => (
            <button
              type="button"
              className="bbox-option"
              key={item.id}
              data-active={preset === item.id}
              onClick={() => choosePreset(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="grid-4" style={{ marginTop: 6 }}>
          {(["West", "South", "East", "North"] as const).map((label, index) => (
            <div className="field" key={label}>
              <label htmlFor={`bbox-${index}`}>{label} °</label>
              <input
                id={`bbox-${index}`}
                className="input"
                type="number"
                step="0.1"
                value={bbox[index]}
                onChange={(event) => updateBbox(index, Number(event.target.value))}
              />
            </div>
          ))}
        </div>
      </div>

      <div className="grid-4">
        <div className="field">
          <label htmlFor="footprint">Footprint km</label>
          <input
            id="footprint"
            className="input"
            type="number"
            min={5}
            max={300}
            value={footprint}
            onChange={(event) => setFootprint(Number(event.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="refine">Refine sites</label>
          <input
            id="refine"
            className="input"
            type="number"
            min={5}
            max={300}
            value={refineTopN}
            onChange={(event) => setRefineTopN(Number(event.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="max-candidates">Max candidates</label>
          <input
            id="max-candidates"
            className="input"
            type="number"
            min={1}
            max={100}
            value={maxCandidates}
            onChange={(event) => setMaxCandidates(Number(event.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="cluster">Cluster radius km</label>
          <input
            id="cluster"
            className="input"
            type="number"
            min={10}
            max={1000}
            value={clusterRadius}
            onChange={(event) => setClusterRadius(Number(event.target.value))}
          />
        </div>
      </div>

      <div className="callout calm">
        Scoring dimensions and tolerances are declared by the engine. Default weights:
        geomorphology 0.35, morphometry 0.30, texture 0.20, operational 0.15. Surface, thermal,
        water/ice, aeolian, and environment stay evidence-only unless a comparable product pair is
        attached.
      </div>

      {error ? <div className="error-panel">{error}</div> : null}

      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <button className="btn primary" type="submit" disabled={submitting || !selectedRegion}>
          {submitting ? <span className="spin" /> : null}
          {submitting ? "Queueing analysis" : "Queue analysis"}
        </button>
        <span className="tiny faint">
          The worker reads real source windows. Large planetary archives are never downloaded in
          full. <Link className="link" href="/methodology">What gets scored</Link>
        </span>
      </div>
    </form>
  );
}
