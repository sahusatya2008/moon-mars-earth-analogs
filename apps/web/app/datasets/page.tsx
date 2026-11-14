"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { StatusChip } from "@/components/StatusChip";
import { api, ApiError } from "@/lib/api";
import { pct, when } from "@/lib/format";
import type { Dataset, DatasetAvailability } from "@/lib/types";

const BODIES = [
  { id: "all", label: "All bodies" },
  { id: "earth", label: "Earth" },
  { id: "moon", label: "Moon" },
  { id: "mars", label: "Mars" },
];

const BODY_LABEL: Record<string, string> = {
  earth: "Earth",
  moon: "Moon",
  mars: "Mars",
};

type AvailabilityState = {
  loading: boolean;
  error: string | null;
  checkedAt: string | null;
  available: number | null;
  credentials: number | null;
  unavailable: number | null;
};

const EMPTY_PROBE: AvailabilityState = {
  loading: false,
  error: null,
  checkedAt: null,
  available: null,
  credentials: null,
  unavailable: null,
};

export default function DatasetsPage() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [body, setBody] = useState("all");
  const [scoringOnly, setScoringOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<AvailabilityState>(EMPTY_PROBE);
  const [availability, setAvailability] = useState<Record<string, DatasetAvailability>>({});

  const load = useCallback(async () => {
    try {
      const response = await api.datasets("?page_size=100");
      setDatasets(response.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Could not load the catalog.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const effectiveStatus = useCallback(
    (dataset: Dataset) => availability[dataset.id]?.status ?? dataset.status,
    [availability],
  );

  const counts = useMemo(() => {
    const out = { total: datasets.length, available: 0, credentials: 0, unknown: 0, problem: 0 };
    for (const dataset of datasets) {
      const status = effectiveStatus(dataset);
      if (status === "available") out.available += 1;
      else if (status === "requires_credentials") out.credentials += 1;
      else if (status === "unknown") out.unknown += 1;
      else out.problem += 1;
    }
    return out;
  }, [datasets, effectiveStatus]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return datasets.filter((dataset) => {
      if (body !== "all" && dataset.body_id !== body) return false;
      if (scoringOnly && !dataset.is_scoring_input) return false;
      if (!needle) return true;
      return [dataset.title, dataset.description, dataset.source_agency, dataset.mission, dataset.instrument, dataset.id]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [body, datasets, query, scoringOnly]);

  const grouped = useMemo(() => {
    const map = new Map<string, Dataset[]>();
    for (const dataset of visible) {
      const list = map.get(dataset.body_id) ?? [];
      list.push(dataset);
      map.set(dataset.body_id, list);
    }
    const order = body === "all" ? ["earth", "moon", "mars"] : [body];
    return order.filter((id) => map.has(id)).map((id) => ({ body: id, items: map.get(id) as Dataset[] }));
  }, [body, visible]);

  const runProbe = useCallback(async () => {
    setProbe((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await api.probe(body === "all" ? undefined : body, 100);
      setAvailability((current) => {
        const next = { ...current };
        for (const row of response.datasets) next[row.dataset_id] = row;
        return next;
      });
      setProbe({
        loading: false,
        error: null,
        checkedAt: response.checked_at,
        available: response.available,
        credentials: response.requires_credentials,
        unavailable: response.unavailable,
      });
      await load();
    } catch (err) {
      setProbe((current) => ({
        ...current,
        loading: false,
        error:
          err instanceof ApiError
            ? `${err.message}. The probe performs bounded HEAD/range reads and may be blocked by the network.`
            : err instanceof Error
              ? err.message
              : "Probe failed.",
      }));
    }
  }, [body, load]);

  return (
    <main className="shell" style={{ padding: "34px 0 70px" }}>
      <div className="section-head">
        <div>
          <div className="eyebrow">Data observatory</div>
          <h2>The catalog is a contract</h2>
          <p style={{ marginTop: 10, maxWidth: 780 }}>
            Every product used by SNSX SPACE is registered with its agency, mission, resolution,
            license, citation, and access protocol. The Microsoft Planetary Computer collections
            are read through public STAC metadata and automatically issued, short-lived SAS
            tokens; the tokens are never stored with results. Credential-gated Meteomatics
            weather evidence is attached only when account credentials are configured. Status is{" "}
            <b>unknown</b> until a bounded probe reads the actual source. Credential-gated services
            are reported as gated, never as connected.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <span className="chip ok">{counts.available} available</span>
          <span className="chip warn">{counts.credentials} gated</span>
          <span className="chip idle">{counts.unknown} unknown</span>
          {counts.problem > 0 ? <span className="chip bad">{counts.problem} degraded</span> : null}
        </div>
      </div>

      <div className="panel panel-pad" style={{ marginBottom: 20 }}>
        <div className="dataset-toolbar">
          <div className="tabs" style={{ border: 0, padding: 0, background: "transparent" }}>
            {BODIES.map((item) => (
              <button key={item.id} className="tab" data-active={body === item.id} onClick={() => setBody(item.id)} type="button">
                {item.label}
              </button>
            ))}
          </div>
          <label className="tiny muted" style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <input type="checkbox" checked={scoringOnly} onChange={(event) => setScoringOnly(event.target.checked)} />
            Scoring inputs only
          </label>
          <input
            className="input"
            style={{ minHeight: 34, maxWidth: 260 }}
            placeholder="Filter by title, agency, variable…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="btn small primary" onClick={runProbe} disabled={probe.loading} type="button">
            {probe.loading ? <span className="spin" /> : null}
            {probe.loading
              ? "Probing sources…"
              : body === "all"
                ? `Probe ${datasets.length || "all"} sources`
                : `Probe ${BODY_LABEL[body] ?? body} sources`}
          </button>
        </div>
        <div className="tiny faint" style={{ marginTop: 12, lineHeight: 1.6 }}>
          Probing performs one bounded request per dataset: a HEAD or single-byte range read, or,
          for Planetary Computer, STAC metadata plus a public SAS token check. It checks
          reachability, not scientific suitability, and it does not download data. Some archives
          published by USGS or NASA reject automated HEAD requests from this network; that is
          recorded as degraded rather than hidden.
          {probe.checkedAt ? (
            <>
              {" "}
              Last probe: <span className="mono">{when(probe.checkedAt)}</span>
              {probe.available !== null
                ? ` · ${probe.available} available, ${probe.credentials} gated, ${probe.unavailable} unavailable`
                : ""}
            </>
          ) : null}
        </div>
        {probe.error ? (
          <div className="error-panel" style={{ marginTop: 14 }}>
            {probe.error}
          </div>
        ) : null}
      </div>

      {loading ? (
        <div style={{ display: "grid", gap: 8 }}>
          {[0, 1, 2, 3].map((key) => (
            <div key={key} className="skeleton" style={{ height: 62 }} />
          ))}
        </div>
      ) : error ? (
        <div className="error-panel">
          {error}
          <div style={{ marginTop: 10 }}>
            <button className="btn small" onClick={load} type="button">
              Retry
            </button>
          </div>
        </div>
      ) : visible.length === 0 ? (
        <div className="empty">No catalog entries match this filter.</div>
      ) : (
        <div style={{ display: "grid", gap: 26 }}>
          {grouped.map((group) => (
            <section key={group.body}>
              <div className="dataset-group-head">
                <h3>
                  {BODY_LABEL[group.body] ?? group.body}
                  <span className="tiny faint mono" style={{ marginLeft: 10 }}>{group.items.length} products</span>
                </h3>
                <span className="hairline" />
              </div>
              <div style={{ display: "grid", gap: 6 }}>
                {group.items.map((dataset) => {
                  const live = availability[dataset.id];
                  const status = live?.status ?? dataset.status;
                  return (
                    <details className="dataset-card" key={dataset.id}>
                      <summary>
                        <span className="dataset-summary-main">
                          <span style={{ fontSize: 13, fontWeight: 600 }}>{dataset.title}</span>
                          <span className="tiny faint mono">{dataset.id}</span>
                        </span>
                        <span className="dataset-summary-meta">
                          {dataset.is_scoring_input ? <span className="chip idle">scoring</span> : <span className="chip idle">context</span>}
                          <span className="tiny muted">{dataset.spatial_resolution_m ? `${dataset.spatial_resolution_m} m` : "native res. n/a"}</span>
                          <StatusChip status={status} />
                        </span>
                      </summary>
                      <div className="dataset-body">
                        <p className="small muted" style={{ marginTop: 0 }}>{dataset.description}</p>
                        <div className="dataset-facts">
                          <div>
                            <span className="label">Agency</span>
                            <div className="small">{dataset.source_agency}</div>
                          </div>
                          <div>
                            <span className="label">Mission / instrument</span>
                            <div className="small">{[dataset.mission, dataset.instrument].filter(Boolean).join(" · ") || "—"}</div>
                          </div>
                          <div>
                            <span className="label">Access</span>
                            <div className="small mono">{dataset.access_protocol}</div>
                          </div>
                          <div>
                            <span className="label">Coverage</span>
                            <div className="small">{[dataset.temporal_coverage, dataset.temporal_resolution].filter(Boolean).join(" · ") || "—"}</div>
                          </div>
                          <div>
                            <span className="label">Processing</span>
                            <div className="small">{dataset.processing_level || "—"}</div>
                          </div>
                          <div>
                            <span className="label">Last successful read</span>
                            <div className="small">{when(dataset.last_success_at)}</div>
                          </div>
                        </div>
                        {dataset.variable_names.length > 0 ? (
                          <div style={{ marginTop: 14 }}>
                            <span className="label">Variables</span>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 6 }}>
                              {dataset.variable_names.map((variable) => (
                                <span className="chip idle" key={variable}>{variable}</span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        <div className="dataset-citation">
                          <span className="label">Citation</span>
                          <div className="tiny" style={{ lineHeight: 1.65, marginTop: 4 }}>{dataset.citation}</div>
                          <div className="tiny faint" style={{ marginTop: 6 }}>
                            License: {dataset.license}
                            {dataset.license_url ? (
                              <>
                                {" · "}
                                <a className="link" href={dataset.license_url} target="_blank" rel="noreferrer">
                                  terms
                                </a>
                              </>
                            ) : null}
                          </div>
                        </div>
                        {live ? (
                          <div className={`dataset-probe ${live.status === "available" ? "ok" : live.status === "requires_credentials" ? "warn" : "bad"}`}>
                            <b>Live probe · {live.status.replace(/_/g, " ")}</b>
                            <span>{live.detail}</span>
                            <span className="mono tiny">
                              {live.http_status ? `HTTP ${live.http_status}` : "no HTTP status"}
                              {live.latency_ms !== null ? ` · ${live.latency_ms} ms` : ""}
                              {live.probe_url ? ` · ${live.probe_url}` : ""}
                            </span>
                          </div>
                        ) : dataset.status_detail ? (
                          <div className="tiny faint" style={{ marginTop: 12 }}>
                            Stored status: {dataset.status.replace(/_/g, " ")} — {dataset.status_detail}
                          </div>
                        ) : null}
                        <div style={{ display: "flex", gap: 14, marginTop: 14, flexWrap: "wrap" }}>
                          <a className="link small" href={dataset.source_url} target="_blank" rel="noreferrer">
                            Source product
                          </a>
                          {dataset.documentation_url ? (
                            <a className="link small" href={dataset.documentation_url} target="_blank" rel="noreferrer">
                              Documentation
                            </a>
                          ) : null}
                          <a className="link small" href={`/analyses`}>
                            Runs using the catalog
                          </a>
                        </div>
                      </div>
                    </details>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      <div className="panel panel-pad" style={{ marginTop: 26 }}>
        <div className="grid-3">
          <div>
            <div className="eyebrow cyan">Scoring inputs</div>
            <p className="small muted" style={{ lineHeight: 1.65 }}>
              {pct(counts.total ? (datasets.filter((d) => d.is_scoring_input).length / counts.total) : 0)} of the
              catalog is eligible for cross-body scoring. Eligibility is declared per product in the
              seed catalog and repeated in each run manifest.
            </p>
          </div>
          <div>
            <div className="eyebrow cyan">Missing data</div>
            <p className="small muted" style={{ lineHeight: 1.65 }}>
              A dataset that is gated or unreachable contributes no measured value. The affected
              feature or dimension is marked unavailable, and confidence falls; nothing is
              back-filled from a model or from a visual similarity judgement.
            </p>
          </div>
          <div>
            <div className="eyebrow cyan">Provenance</div>
            <p className="small muted" style={{ lineHeight: 1.65 }}>
              Feature measurements store the product ID, source URL, citation, native and analysis
              resolution, method, processing version, and valid-pixel fraction alongside the number.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
