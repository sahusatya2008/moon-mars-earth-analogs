"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CreateAnalysisForm } from "@/components/CreateAnalysisForm";
import { StatusChip } from "@/components/StatusChip";
import { api, ApiError } from "@/lib/api";
import { when } from "@/lib/format";
import type { Analysis, Region } from "@/lib/types";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "succeeded", label: "Complete" },
  { id: "failed", label: "Failed" },
];

export default function AnalysesPage() {
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [filter, setFilter] = useState("all");
  const [body, setBody] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [analysisResponse, regionResponse] = await Promise.all([api.analyses("?page_size=50"), api.regions()]);
      setAnalyses(analysisResponse.items);
      setRegions(Array.isArray(regionResponse) ? regionResponse : regionResponse.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Could not load analyses.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!analyses.some((analysis) => ["queued", "running", "retrying"].includes(analysis.status))) return;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [analyses, load]);

  const regionById = useMemo(() => new Map(regions.map((region) => [region.id, region])), [regions]);
  const visible = analyses.filter((analysis) => {
    const region = regionById.get(analysis.reference_region_id);
    if (filter !== "all" && analysis.status !== filter) return false;
    if (body !== "all" && region?.body_id !== body) return false;
    return true;
  });

  return (
    <main className="shell" style={{ padding: "34px 0 70px" }}>
      <div className="section-head">
        <div>
          <div className="eyebrow">Analysis workspace</div>
          <h2>Planetary analogue runs</h2>
          <p style={{ marginTop: 10 }}>
            Every run stores its configuration, source products, feature measurements, candidate
            rankings, evidence, and limitations. Nothing is recomputed from UI state.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <span className="chip ok">{analyses.filter((row) => row.status === "succeeded").length} complete</span>
          <span className="chip live">
            {analyses.filter((row) => ["queued", "running"].includes(row.status)).length} active
          </span>
        </div>
      </div>

      <div style={{ marginBottom: 22 }}>
        <CreateAnalysisForm />
      </div>

      <div className="panel">
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 18px", borderBottom: "1px solid var(--line)", flexWrap: "wrap" }}>
          <div className="tabs" style={{ border: 0, padding: 0, background: "transparent" }}>
            {FILTERS.map((item) => (
              <button
                key={item.id}
                className="tab"
                data-active={filter === item.id}
                onClick={() => setFilter(item.id)}
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>
          <div style={{ marginLeft: "auto" }}>
            <select className="select" style={{ minHeight: 32, width: 150 }} value={body} onChange={(event) => setBody(event.target.value)}>
              <option value="all">All bodies</option>
              <option value="moon">Moon</option>
              <option value="mars">Mars</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div style={{ padding: 20, display: "grid", gap: 8 }}>
            {[0, 1, 2].map((key) => (
              <div key={key} className="skeleton" style={{ height: 48 }} />
            ))}
          </div>
        ) : error ? (
          <div style={{ padding: 20 }}>
            <div className="error-panel">
              {error}
              <div style={{ marginTop: 10 }}>
                <button className="btn small" onClick={load} type="button">
                  Retry
                </button>
              </div>
            </div>
          </div>
        ) : visible.length === 0 ? (
          <div style={{ padding: 20 }}>
            <div className="empty">No analyses match this filter. Queue one above to start.</div>
          </div>
        ) : (
          <div>
            {visible.map((analysis) => {
              const region = regionById.get(analysis.reference_region_id);
              return (
                <Link
                  key={analysis.id}
                  href={`/analyses/${analysis.id}`}
                  className="analysis-row"
                  style={{ gridTemplateColumns: "1.6fr 0.55fr 0.6fr 0.7fr 0.5fr" }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{analysis.title}</div>
                    <div className="tiny faint">
                      {region ? `${region.body_id.toUpperCase()} · ${region.name}` : "reference region missing"}
                    </div>
                  </div>
                  <div>
                    <StatusChip status={analysis.status} />
                  </div>
                  <div className="tiny muted">{Math.round((analysis.progress || 0) * 100)}%</div>
                  <div className="tiny muted">{when(analysis.created_at)}</div>
                  <div className="tiny faint mono" style={{ textAlign: "right" }}>
                    {analysis.status === "succeeded" && analysis.result_summary
                      ? `${analysis.result_summary.candidate_count} cand.`
                      : analysis.algorithm_version}
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </main>
  );
}
