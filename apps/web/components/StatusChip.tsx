import { STATUS_TONE } from "@/lib/format";

export function StatusChip({ status, label }: { status: string; label?: string }) {
  const tone = STATUS_TONE[status] || "idle";
  return (
    <span className={`chip ${tone}`}>
      {tone === "live" ? <span className="dot" /> : null}
      {label || status.replace(/_/g, " ")}
    </span>
  );
}

export function BandChip({ band }: { band: string }) {
  const tone = band === "high" ? "ok" : band === "moderate" ? "warn" : "bad";
  return <span className={`chip ${tone}`}>{band.replace("_", " ")} confidence</span>;
}
