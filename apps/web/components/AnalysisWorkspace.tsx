"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Globe, type GlobePin } from "@/components/Globe";
import { ScoreBar } from "@/components/ScoreBar";
import { TerrainModelPanel } from "@/components/TerrainModelPanel";
import { StatusChip, BandChip } from "@/components/StatusChip";
import { api, ApiError, eventSourceUrl, reportDownloadUrl } from "@/lib/api";
import { coords, meters, pct, score, titleCase, when } from "@/lib/format";
import type {
  Analysis,
  AnalysisResults,
  AssistantAnswer,
  CandidateDetail,
  Comparison,
  Evidence,
  JobEvent,
  Ranking,
  Report,
} from "@/lib/types";

const FORMATS: Report["format"][] = ["pdf", "csv", "geojson", "json", "manifest"];
const DIMENSION_ORDER = ["geomorphology", "morphometry", "texture", "operational"];

const QUESTIONS = [
  "Why did the top candidate rank first?",
  "What evidence is missing or weak?",
  "How much do the similarity methods disagree?",
  "What are the main limitations of this run?",
];

function evidenceInsight(record: Evidence): string | null {
  const details = record.details || {};
  if (record.dataset_id === "nasadem_hgt_v001" || record.dataset_id === "alos_world3d_30m") {
    const mae = details.mae_m;
    const rmse = details.rmse_m;
    const bias = details.bias_m;
    const overlap = details.overlap_pixels;
    if (typeof mae !== "number" || typeof rmse !== "number" || typeof bias !== "number") {
      return null;
    }
    const cells = typeof overlap === "number" ? `${overlap.toLocaleString()} cells` : "overlapping cells";
    return `Independent DEM cross-check: MAE ${mae.toFixed(2)} m, RMSE ${rmse.toFixed(2)} m, bias ${bias >= 0 ? "+" : ""}${bias.toFixed(2)} m across ${cells}. Context only, not scored.`;
  }
  if (record.dataset_id === "io_lulc_annual_v02") {
    const agreement = details.worldcover_agreement;
    if (agreement && typeof agreement === "object" && "agreement" in agreement) {
      const value = (agreement as { agreement?: unknown }).agreement;
      if (typeof value === "number") {
        return `Broad-class agreement with ESA WorldCover: ${Math.round(value * 100)}%. Class definitions and epochs differ, so this is context only.`;
      }
    }
  }
  if (record.dataset_id === "pc_sentinel2_l2a" || record.dataset_id === "pc_landsat_c2_l2") {
    const parts: string[] = [];
    if (typeof details.cloud_cover === "number") parts.push(`cloud ${details.cloud_cover.toFixed(1)}%`);
    if (typeof details.scene_count === "number") parts.push(`${details.scene_count} scene${details.scene_count === 1 ? "" : "s"} found`);
    if (typeof details.platform === "string") parts.push(details.platform);
    return parts.length ? `${parts.join(" · ")}. Metadata and preview only; bulk imagery is not downloaded.` : null;
  }
  return null;
}

function mergeEvents(current: JobEvent[], incoming: JobEvent[]): JobEvent[] {
  const byId = new Map(current.map((event) => [event.id, event]));
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()].sort((a, b) => a.id - b.id).slice(-80);
}

function ProgressPanel({
  analysis,
  events,
}: {
  analysis: Analysis;
  events: JobEvent[];
}) {
  const active = ["queued", "running", "retrying", "cancelling"].includes(analysis.status);
  const failed = analysis.status === "failed";
  return (
    <div className="panel panel-pad">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start" }}>
        <div>
          <div className="eyebrow">{active ? "Analysis in progress" : failed ? "Analysis failed" : "Analysis state"}</div>
          <h3 style={{ marginTop: 8, fontSize: 20 }}>
            {titleCase(analysis.stage || analysis.status)}
          </h3>
        </div>
        <StatusChip status={analysis.status} />
      </div>
      <div className="progress-track">
        <div
          className={`progress-fill ${analysis.status === "succeeded" ? "done" : ""} ${failed ? "bad" : ""}`}
          style={{ width: `${Math.max(2, Math.min(100, analysis.progress * 100))}%` }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
        <span className="tiny faint mono">{Math.round(analysis.progress * 100)}%</span>
        <span className="tiny faint mono">real worker events</span>
      </div>
      {analysis.error_message ? (
        <div className="error-panel" style={{ marginTop: 16 }}>{analysis.error_message}</div>
      ) : null}
      <div className="event-log" style={{ marginTop: 16 }}>
        {events.length === 0 ? (
          <div className="tiny faint">Waiting for the worker to claim the job.</div>
        ) : (
          events.slice(-16).map((event) => (
            <div className="event-row" key={event.id} data-level={event.level}>
              <span className="event-stage">{event.stage || "event"}</span>
              <span className="event-msg">{event.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function RankingsTable({
  rankings,
  selectedId,
  onSelect,
  compareIds,
  onCompare,
}: {
  rankings: Ranking[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  compareIds: string[];
  onCompare: (id: string) => void;
}) {
  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center", borderBottom: "1px solid var(--line)" }}>
        <div>
          <div className="eyebrow cyan">Refined Earth candidates</div>
          <h3 style={{ marginTop: 6, fontSize: 18 }}>Ranked by stated similarity</h3>
        </div>
        <span className="chip idle">{rankings.length} candidates</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="ranking-table">
          <thead>
            <tr>
              <th style={{ width: 42 }}>Cmp</th>
              <th style={{ width: 44 }}>Rank</th>
              <th>Candidate</th>
              <th>Similarity</th>
              <th>Confidence</th>
              <th>Coverage</th>
            </tr>
          </thead>
          <tbody>
            {rankings.map((ranking) => (
              <tr
                key={ranking.candidate_id}
                data-selected={selectedId === ranking.candidate_id}
                onClick={() => onSelect(ranking.candidate_id)}
              >
                <td onClick={(event) => event.stopPropagation()}>
                  <input
                    type="checkbox"
                    aria-label={`Compare ${ranking.display_name}`}
                    checked={compareIds.includes(ranking.candidate_id)}
                    onChange={() => onCompare(ranking.candidate_id)}
                    style={{ accentColor: "var(--orange)", width: 15, height: 15 }}
                  />
                </td>
                <td><span className="rank-badge">{ranking.rank}</span></td>
                <td>
                  <div style={{ fontWeight: 600, maxWidth: 330, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {ranking.display_name}
                  </div>
                  <div className="tiny faint">{coords(ranking.center_lon, ranking.center_lat)} · 40 km</div>
                </td>
                <td>
                  <div className="score-cell">
                    <div className="score-line">
                      <span className="score-number small">{score(ranking.overall_score)}</span>
                    </div>
                    <ScoreBar value={ranking.overall_score} />
                  </div>
                </td>
                <td>
                  <div className="score-cell">
                    <div className="score-line">
                      <span className="mono" style={{ fontSize: 11 }}>{score(ranking.confidence)}</span>
                      <BandChip band={ranking.confidence_band} />
                    </div>
                    <ScoreBar value={ranking.confidence} tone="cyan" />
                  </div>
                </td>
                <td>
                  <span className="mono tiny">{pct(ranking.evidence_coverage)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CandidatePanel({
  ranking,
  detail,
  loading,
}: {
  ranking: Ranking | null;
  detail: CandidateDetail | null;
  loading: boolean;
}) {
  if (!ranking) {
    return (
      <div className="panel panel-pad">
        <div className="empty">Select a candidate to inspect its dimensions and evidence.</div>
      </div>
    );
  }
  const dimensions = DIMENSION_ORDER.map((key) => ranking.dimension_scores[key]).filter(Boolean);
  return (
    <div className="panel panel-pad">
      <div className="candidate-head">
        <div>
          <div className="eyebrow">Candidate profile</div>
          <h3 className="candidate-name">{ranking.display_name}</h3>
          <div className="tiny muted">
            {coords(ranking.center_lon, ranking.center_lat)}
            {ranking.country ? ` · ${ranking.country}` : ""}
            {ranking.admin_region ? ` · ${ranking.admin_region}` : ""}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="score-number" style={{ color: "var(--orange)", fontSize: 38 }}>{score(ranking.overall_score)}</div>
          <div className="tiny faint mono">similarity</div>
        </div>
      </div>

      <div className="grid-4" style={{ marginTop: 18 }}>
        <div className="stat">
          <div className="stat-value" style={{ fontSize: 23 }}>{score(ranking.confidence)}</div>
          <div className="stat-label">Confidence</div>
        </div>
        <div className="stat">
          <div className="stat-value" style={{ fontSize: 23 }}>{pct(ranking.evidence_coverage)}</div>
          <div className="stat-label">Weighted coverage</div>
        </div>
        <div className="stat">
          <div className="stat-value" style={{ fontSize: 23 }}>{meters(ranking.elevation_mean)}</div>
          <div className="stat-label">Mean elevation · context</div>
        </div>
        <div className="stat">
          <div className="stat-value" style={{ fontSize: 23 }}>{ranking.missing_dimensions.length}</div>
          <div className="stat-label">Missing dimensions</div>
        </div>
      </div>

      <div style={{ marginTop: 22 }}>
        <div className="label" style={{ marginBottom: 8 }}>Dimension scores</div>
        {dimensions.map((dimension) => (
          <div className="dimension-row" key={dimension.dimension}>
            <div className="dimension-name">{dimension.label}</div>
            <div>
              {dimension.score === null ? (
                <span className="tiny faint">unavailable · {dimension.unavailable_reason}</span>
              ) : (
                <ScoreBar value={dimension.score} />
              )}
            </div>
            <div className="dimension-value">
              {dimension.score === null ? "—" : score(dimension.score)}
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 20 }}>
        <div className="label" style={{ marginBottom: 8 }}>Method sensitivity</div>
        <div className="method-grid">
          {Object.entries(ranking.method_scores).map(([method, value]) => (
            <div className="method-card" key={method}>
              <span>{method.replace(/_/g, " ")}</span>
              <b>{score(value)}</b>
            </div>
          ))}
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 20 }}>
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Strongest dimensions</div>
          {ranking.strongest_dimensions.map((item) => (
            <div key={item.dimension} className="tiny" style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid rgba(207,226,235,0.07)" }}>
              <span className="muted">{item.label}</span>
              <span className="mono">{score(item.score)}</span>
            </div>
          ))}
        </div>
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Weakest dimensions</div>
          {ranking.weakest_dimensions.map((item) => (
            <div key={item.dimension} className="tiny" style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid rgba(207,226,235,0.07)" }}>
              <span className="muted">{item.label}</span>
              <span className="mono">{score(item.score)}</span>
            </div>
          ))}
        </div>
      </div>

      {ranking.missing_dimensions.length > 0 ? (
        <div className="callout" style={{ marginTop: 18 }}>
          <b>Unavailable dimensions.</b>{" "}
          {ranking.missing_dimensions.map((item) => `${item.label}${item.reason ? ` (${item.reason})` : ""}`).join("; ")}.
          Weights were renormalized; nothing was imputed.
        </div>
      ) : null}

      {ranking.limitations.length > 0 ? (
        <div style={{ marginTop: 18 }}>
          <div className="label" style={{ marginBottom: 8 }}>Limitations for this candidate</div>
          <ul className="tiny muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.65 }}>
            {ranking.limitations.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {loading ? <div className="tiny faint" style={{ marginTop: 16 }}>Loading feature-level evidence…</div> : null}
      {detail ? (
        <div style={{ marginTop: 20 }}>
          <div className="label" style={{ marginBottom: 8 }}>Inspectable evidence</div>
          <div className="evidence-list">
            {detail.evidence.map((record) => (
              <div className="evidence-item" key={record.id}>
                <div className="evidence-title">
                  <span>{record.title}</span>
                  <span className="chip idle">{record.dataset_id}</span>
                </div>
                {evidenceInsight(record) ? (
                  <div className="evidence-metrics">{evidenceInsight(record)}</div>
                ) : null}
                {typeof record.details?.preview_url === "string" &&
                record.details.preview_url.startsWith("https://") ? (
                  <a
                    className="evidence-preview"
                    href={record.details.preview_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={record.details.preview_url}
                      alt={`${record.dataset_id} scene preview`}
                      loading="lazy"
                    />
                  </a>
                ) : null}
                <div className="evidence-cite">
                  {record.citation}
                  {record.source_url ? (
                    <>
                      {" · "}
                      <a className="link" href={record.source_url} target="_blank" rel="noreferrer">
                        source
                      </a>
                    </>
                  ) : null}
                </div>
                <div className="tiny faint mono" style={{ marginTop: 5 }}>
                  {record.license || "license not recorded"}
                  {record.processing_level ? ` · ${record.processing_level}` : ""}
                  {record.spatial_resolution_m ? ` · ${record.spatial_resolution_m} m` : ""}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ReportsPanel({
  analysisId,
  reports,
  onCreate,
  busy,
}: {
  analysisId: string;
  reports: Report[];
  onCreate: (format: Report["format"]) => void;
  busy: Report["format"] | null;
}) {
  const latestByFormat = useMemo(() => {
    const map = new Map<string, Report>();
    for (const report of reports) {
      if (!map.has(report.format)) map.set(report.format, report);
    }
    return map;
  }, [reports]);

  return (
    <div className="panel panel-pad">
      <div className="eyebrow">Exports</div>
      <h3 style={{ marginTop: 7, fontSize: 17 }}>Reports from stored records</h3>
      <p className="tiny muted" style={{ marginTop: 6 }}>
        Each export is generated from the persisted analysis, hashed with SHA-256, and never
        recomputed from browser state.
      </p>
      <div className="report-grid" style={{ marginTop: 15 }}>
        {FORMATS.map((format) => {
          const report = latestByFormat.get(format);
          const ready = report?.status === "ready";
          return (
            <div key={format} style={{ display: "grid", gap: 5 }}>
              <button
                type="button"
                className="report-btn"
                disabled={busy === format}
                onClick={() => onCreate(format)}
                title={report?.error_message || undefined}
              >
                <b>{format.toUpperCase()}</b>
                <span>{report ? report.status : "generate"}</span>
              </button>
              {ready && report ? (
                <a className="link tiny" href={reportDownloadUrl(report.id)}>
                  download {report.size_bytes ? `${Math.round(report.size_bytes / 1024)} KB` : ""}
                </a>
              ) : null}
            </div>
          );
        })}
      </div>
      {reports.some((report) => report.error_message) ? (
        <div className="tiny" style={{ color: "var(--red)", marginTop: 10 }}>
          {reports.find((report) => report.error_message)?.error_message}
        </div>
      ) : null}
    </div>
  );
}

function AssistantPanel({ analysisId }: { analysisId: string }) {
  const [question, setQuestion] = useState(QUESTIONS[0]);
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await api.assistant({ analysis_id: analysisId, question });
      setAnswer(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Assistant request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel panel-pad">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div className="eyebrow cyan">Grounded assistant</div>
          <h3 style={{ marginTop: 7, fontSize: 17 }}>Ask the stored evidence</h3>
        </div>
        <span className="chip idle">{answer?.provider || "deterministic"}</span>
      </div>
      <form onSubmit={ask} style={{ display: "grid", gap: 8, marginTop: 14 }}>
        <textarea
          className="textarea"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={2000}
          style={{ minHeight: 70 }}
        />
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn primary small" type="submit" disabled={busy || question.trim().length < 3}>
            {busy ? <span className="spin" /> : null}
            {busy ? "Reading" : "Ask"}
          </button>
          <select
            className="select"
            style={{ minHeight: 32, width: 190, fontSize: 11 }}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            aria-label="Suggested questions"
          >
            {QUESTIONS.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>
      </form>
      {error ? <div className="error-panel" style={{ marginTop: 10 }}>{error}</div> : null}
      {answer ? (
        <div style={{ marginTop: 14 }}>
          <div className="assistant-answer">{answer.answer}</div>
          {answer.citations.length > 0 ? (
            <div className="citation-list">
              {answer.citations.map((citation, index) => (
                <div className="citation" key={`${citation.dataset_id}-${index}`}>
                  {citation.source_url ? (
                    <a className="link" href={citation.source_url} target="_blank" rel="noreferrer">
                      {citation.title}
                    </a>
                  ) : (
                    citation.title
                  )}
                  {citation.citation ? ` — ${citation.citation}` : ""}
                </div>
              ))}
            </div>
          ) : null}
          <div className="tiny faint" style={{ marginTop: 8 }}>
            Grounded: {answer.grounded ? "yes" : "no"} · provider: {answer.provider}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MethodAgreementPanel({ results }: { results: AnalysisResults }) {
  const methods = results.method_agreement?.methods || {};
  const agreement = results.method_agreement?.agreement;
  return (
    <div className="panel panel-pad">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 14 }}>
        <div>
          <div className="eyebrow">Method sensitivity</div>
          <h3 style={{ marginTop: 7, fontSize: 17 }}>Do independent aggregations agree?</h3>
        </div>
        <span className="chip warn">
          mean std {agreement?.mean_method_std === null || agreement?.mean_method_std === undefined ? "—" : agreement.mean_method_std.toFixed(3)}
        </span>
      </div>
      <div className="method-grid" style={{ marginTop: 14 }}>
        {Object.entries(methods).map(([method, value]) => (
          <div className="method-card" key={method}>
            <span>{method.replace(/_/g, " ")}</span>
            <b>{score(value.mean)}</b>
            <span style={{ textTransform: "none", letterSpacing: 0 }}>σ {value.std === null ? "—" : value.std.toFixed(3)} · n={value.n}</span>
          </div>
        ))}
      </div>
      <p className="tiny muted" style={{ marginTop: 12 }}>
        {agreement?.interpretation} The primary ranking remains the declared physical-tolerance
        MCDA; disagreements are surfaced, not averaged away.
      </p>
    </div>
  );
}

export function AnalysisWorkspace({ analysisId }: { analysisId: string }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [results, setResults] = useState<AnalysisResults | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reports, setReports] = useState<Report[]>([]);
  const [reportBusy, setReportBusy] = useState<Report["format"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [globeBody, setGlobeBody] = useState<"earth" | "reference">("earth");
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const loadedResultsFor = useRef<string | null>(null);

  const load = useCallback(async () => {
    try {
      const current = await api.analysis(analysisId);
      setAnalysis(current);
      if (current.status === "succeeded") {
        if (loadedResultsFor.current !== analysisId) {
          const [resultPayload, reportPayload] = await Promise.all([
            api.results(analysisId),
            api.reports(analysisId),
          ]);
          setResults(resultPayload);
          setReports(reportPayload);
          setSelectedId((previous) => previous || resultPayload.rankings[0]?.candidate_id || null);
          loadedResultsFor.current = analysisId;
        } else {
          setReports(await api.reports(analysisId));
        }
      } else {
        const eventPayload = await api.events(analysisId);
        setEvents((currentEvents) => mergeEvents(currentEvents, eventPayload.items));
      }
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("This analysis does not exist.");
      } else {
        setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Could not load analysis.");
      }
    } finally {
      setLoading(false);
    }
  }, [analysisId]);

  useEffect(() => {
    load();
  }, [load]);

  const active = analysis ? ["queued", "running", "retrying", "cancelling"].includes(analysis.status) : false;

  useEffect(() => {
    if (!active) return;
    const source = new EventSource(eventSourceUrl(analysisId));
    source.addEventListener("job-event", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as JobEvent;
        setEvents((current) => mergeEvents(current, [payload]));
        setAnalysis((current) => {
          if (!current) return current;
          const nextStatus =
            payload.stage === "cancelled"
              ? "cancelled"
              : payload.stage === "failed"
                ? "failed"
                : current.status === "queued" || current.status === "retrying"
                  ? "running"
                  : current.status;
          return {
            ...current,
            status: nextStatus,
            stage: payload.stage || current.stage,
            progress: typeof payload.progress === "number" ? payload.progress : current.progress,
          };
        });
      } catch {
        /* ignore malformed event */
      }
    });
    source.addEventListener("analysis-status", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as {
          status: string;
          stage: string;
          progress: number;
          error: string | null;
          terminal: boolean;
        };
        setAnalysis((current) =>
          current
            ? {
                ...current,
                status: payload.status,
                stage: payload.stage,
                progress: payload.progress,
                error_message: payload.error ?? current.error_message,
              }
            : current,
        );
        if (payload.terminal) {
          source.close();
          loadedResultsFor.current = null;
          window.setTimeout(load, 300);
        }
      } catch {
        /* ignore malformed event */
      }
    });
    source.onerror = () => {
      // EventSource reconnects automatically. The polling fallback below also keeps state fresh.
    };
    return () => source.close();
  }, [active, analysisId, load]);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [active, load]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    api
      .candidate(selectedId)
      .then((payload) => {
        if (!cancelled) setDetail(payload);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    const pending = reports.some((report) => ["queued", "generating"].includes(report.status));
    if (!pending) return;
    const timer = window.setInterval(async () => {
      try {
        setReports(await api.reports(analysisId));
      } catch {
        /* keep last known state */
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [reports, analysisId]);

  const selectedRanking = results?.rankings.find((ranking) => ranking.candidate_id === selectedId) || null;

  const pins: GlobePin[] = useMemo(() => {
    if (!results) return [];
    if (globeBody === "reference") {
      return [
        {
          lon: results.reference_region.center_lon,
          lat: results.reference_region.center_lat,
          color: "#68e0d0",
        },
      ];
    }
    return results.rankings.map((ranking) => ({
      lon: ranking.center_lon,
      lat: ranking.center_lat,
      color: ranking.candidate_id === selectedId ? "#c9f25a" : "#ff6b35",
    }));
  }, [results, globeBody, selectedId]);

  async function createReport(format: Report["format"]) {
    setReportBusy(format);
    try {
      const report = await api.createReport(analysisId, format);
      setReports((current) => [report, ...current]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Report request failed.");
    } finally {
      setReportBusy(null);
    }
  }

  async function runComparison() {
    if (!results || compareIds.length < 2) return;
    setCompareBusy(true);
    try {
      const payload = await api.comparison({
        reference_region_id: results.reference_region.id,
        candidate_ids: compareIds,
        analysis_id: analysisId,
      });
      setComparison(payload);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Comparison failed.");
    } finally {
      setCompareBusy(false);
    }
  }

  async function retry() {
    setRetrying(true);
    try {
      await api.retryAnalysis(analysisId);
      setRetrying(false);
      loadedResultsFor.current = null;
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Retry failed.");
      setRetrying(false);
    }
  }

  if (loading) {
    return (
      <main className="shell" style={{ padding: "40px 0" }}>
        <div style={{ display: "grid", gap: 12 }}>
          <div className="skeleton" style={{ height: 140 }} />
          <div className="skeleton" style={{ height: 420 }} />
        </div>
      </main>
    );
  }

  if (error && !analysis) {
    return (
      <main className="shell" style={{ padding: "40px 0" }}>
        <div className="error-panel">{error}</div>
        <div style={{ marginTop: 12 }}>
          <Link className="btn small" href="/analyses">Back to analyses</Link>
        </div>
      </main>
    );
  }

  if (!analysis || !results) {
    return (
      <main className="shell" style={{ padding: "34px 0 70px" }}>
        <div className="section-head">
          <div>
            <div className="eyebrow">Analysis workspace</div>
            <h2>{analysis?.title || "Analysis"}</h2>
          </div>
          {analysis ? <StatusChip status={analysis.status} /> : null}
        </div>
        {analysis ? <ProgressPanel analysis={analysis} events={events} /> : null}
        {error ? <div className="error-panel" style={{ marginTop: 18 }}>{error}</div> : null}
        <div style={{ marginTop: 18, display: "flex", gap: 8 }}>
          <Link className="btn small" href="/analyses">All analyses</Link>
          {analysis && ["failed", "cancelled"].includes(analysis.status) ? (
            <button className="btn primary small" onClick={retry} disabled={retrying} type="button">
              {retrying ? "Retrying…" : "Retry analysis"}
            </button>
          ) : null}
        </div>
      </main>
    );
  }

  const region = results.reference_region;
  const top = results.rankings[0];
  const summary = results.analysis.result_summary;
  const compareRankings = results.rankings.filter((ranking) => compareIds.includes(ranking.candidate_id));

  return (
    <main className="shell workspace">
      <div className="workspace-main">
        <section className="panel analysis-header">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start" }}>
            <div>
              <div className="eyebrow">
                {region.body_id.toUpperCase()} reference · {region.name}
              </div>
              <h1 className="analysis-title">{results.analysis.title}</h1>
              <div className="tiny muted">
                {coords(region.center_lon, region.center_lat)} · {region.size_km} km reference
                footprint · {results.analysis.algorithm_version}
              </div>
            </div>
            <div style={{ display: "grid", gap: 6, justifyItems: "end" }}>
              <StatusChip status={results.analysis.status} />
              <span className="chip idle">{results.analysis.feature_schema_version}</span>
            </div>
          </div>
          <div className="analysis-meta">
            <div className="meta-pair"><span>Top similarity</span><b>{score(top?.overall_score)}</b></div>
            <div className="meta-pair"><span>Confidence</span><b>{score(top?.confidence)} / {top?.confidence_band}</b></div>
            <div className="meta-pair"><span>Candidates</span><b>{results.rankings.length}</b></div>
            <div className="meta-pair"><span>Footprint</span><b>{results.analysis.config.footprint_km} km</b></div>
            <div className="meta-pair"><span>Grid</span><b>{results.analysis.config.analysis_resolution_m} m</b></div>
            <div className="meta-pair"><span>Completed</span><b>{when(results.analysis.completed_at)}</b></div>
            <div className="meta-pair"><span>Run id</span><b>{results.analysis.id.slice(0, 8)}</b></div>
          </div>
        </section>

        {summary ? (
          <div className="grid-4">
            <div className="stat"><div className="stat-value" style={{ color: "var(--orange)" }}>{top ? score(top.overall_score) : "—"}</div><div className="stat-label">Top similarity</div></div>
            <div className="stat"><div className="stat-value">{summary.candidate_count}</div><div className="stat-label">Ranked candidates</div></div>
            <div className="stat"><div className="stat-value">{score(summary.mean_confidence)}</div><div className="stat-label">Mean confidence</div></div>
            <div className="stat"><div className="stat-value">{summary.screening.coarse_samples ?? "—"}</div><div className="stat-label">Coarse Earth samples</div></div>
          </div>
        ) : null}

        <TerrainModelPanel results={results} selectedId={selectedId} />

        <RankingsTable
          rankings={results.rankings}
          selectedId={selectedId}
          onSelect={setSelectedId}
          compareIds={compareIds}
          onCompare={(id) =>
            setCompareIds((current) =>
              current.includes(id) ? current.filter((item) => item !== id) : [...current, id].slice(-3),
            )
          }
        />

        {compareIds.length >= 2 ? (
          <div className="panel panel-pad">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center" }}>
              <div>
                <div className="eyebrow cyan">Candidate comparison</div>
                <h3 style={{ marginTop: 7, fontSize: 17 }}>{compareIds.length} candidates selected</h3>
              </div>
              <button className="btn small" onClick={runComparison} disabled={compareBusy} type="button">
                {compareBusy ? <span className="spin" /> : null}
                {compareBusy ? "Comparing" : "Compare stored scores"}
              </button>
            </div>
            {comparison?.results ? (
              <div style={{ marginTop: 16, overflowX: "auto" }}>
                <table className="ranking-table">
                  <thead>
                    <tr>
                      <th>Dimension</th>
                      {compareRankings.map((ranking) => (
                        <th key={ranking.candidate_id}>{ranking.display_name.split(",")[0]}</th>
                      ))}
                      <th>Spread</th>
                    </tr>
                  </thead>
                  <tbody>
                    {DIMENSION_ORDER.map((dimension) => (
                      <tr key={dimension} style={{ cursor: "default" }}>
                        <td>{titleCase(dimension)}</td>
                        {compareRankings.map((ranking) => (
                          <td key={ranking.candidate_id} className="mono">
                            {score(ranking.dimension_scores[dimension]?.score)}
                          </td>
                        ))}
                        <td className="mono">
                          {score(comparison.results?.dimension_deltas[dimension]?.spread ?? null)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="tiny faint" style={{ marginTop: 10 }}>{comparison.results.note}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        <CandidatePanel ranking={selectedRanking} detail={detail} loading={detailLoading} />

        {results.rankings.length > 0 ? <MethodAgreementPanel results={results} /> : null}

        <details className="raw">
          <summary>Dataset manifest, provenance, and reproducibility</summary>
          <div className="grid-2" style={{ marginBottom: 16 }}>
            <div>
              <div className="label" style={{ marginBottom: 8 }}>Source products</div>
              {(results.dataset_manifest.datasets || []).map((dataset) => (
                <div key={`${dataset.dataset_id}-${dataset.role}`} className="tiny" style={{ padding: "7px 0", borderBottom: "1px solid rgba(207,226,235,0.07)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ color: "var(--text)" }}>{dataset.dataset_id}</span>
                    <span className="faint">{dataset.role}</span>
                  </div>
                  <div className="faint" style={{ marginTop: 2 }}>{dataset.citation}</div>
                  {dataset.source_url ? (
                    <a className="link" href={dataset.source_url} target="_blank" rel="noreferrer">source</a>
                  ) : null}
                </div>
              ))}
            </div>
            <div>
              <div className="label" style={{ marginBottom: 8 }}>Reproducibility</div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {JSON.stringify(results.reproducibility, null, 2)}
              </pre>
              <div className="label" style={{ margin: "14px 0 8px" }}>Screening and refinement</div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                {JSON.stringify({ screening: results.dataset_manifest.screening, refinement: results.dataset_manifest.refinement }, null, 2)}
              </pre>
            </div>
          </div>
          <div className="label" style={{ marginBottom: 8 }}>Run limitations</div>
          <ul className="tiny muted" style={{ margin: "0 0 16px", paddingLeft: 18, lineHeight: 1.65 }}>
            {results.limitations.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </details>
      </div>

      <aside className="workspace-side">
        <div className="panel" style={{ overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "13px 15px", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ fontSize: 9 }}>Spatial context</div>
            <div className="tabs" style={{ border: 0, padding: 0, background: "transparent" }}>
              <button className="tab" data-active={globeBody === "earth"} onClick={() => setGlobeBody("earth")} type="button">Earth</button>
              <button className="tab" data-active={globeBody === "reference"} onClick={() => setGlobeBody("reference")} type="button">{region.body_id}</button>
            </div>
          </div>
          <div style={{ height: 330, position: "relative" }}>
            <Globe
              body={globeBody === "earth" ? "earth" : region.body_id}
              pins={pins}
              interactive
              autoRotate={false}
              className="globe-fill"
            />
          </div>
          <div style={{ padding: "10px 15px 14px", borderTop: "1px solid var(--line)" }} className="tiny faint">
            {globeBody === "earth"
              ? "Refined Earth candidates are pinned at their measured centers. Textures are NASA GIBS / NASA TREK, not analysis inputs."
              : `${region.name} reference footprint. Texture is NASA TREK imagery, not a scoring input.`}
          </div>
        </div>

        <ReportsPanel analysisId={analysisId} reports={reports} onCreate={createReport} busy={reportBusy} />

        <AssistantPanel analysisId={analysisId} />
      </aside>
    </main>
  );
}
