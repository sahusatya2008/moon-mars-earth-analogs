export function score(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(3) : "—";
}

export function pct(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";
}

export function meters(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${Math.round(value).toLocaleString()} m`;
}

export function when(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function coords(lon: number, lat: number): string {
  const lonLabel = `${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  const latLabel = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}`;
  return `${latLabel}, ${lonLabel}`;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function bandTone(band: string): "high" | "moderate" | "low" | "verylow" {
  if (band === "high") return "high";
  if (band === "moderate") return "moderate";
  if (band === "low") return "low";
  return "verylow";
}

export const STATUS_TONE: Record<string, string> = {
  succeeded: "ok",
  complete: "ok",
  ready: "ok",
  available: "ok",
  running: "live",
  claimed: "live",
  queued: "idle",
  unknown: "idle",
  retrying: "warn",
  degraded: "warn",
  requires_credentials: "warn",
  failed: "bad",
  unavailable: "bad",
  cancelled: "idle",
};
