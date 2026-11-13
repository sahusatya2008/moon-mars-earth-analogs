export function ScoreBar({
  value,
  tone = "orange",
  height = 3,
}: {
  value: number | null | undefined;
  tone?: "orange" | "cyan";
  height?: number;
}) {
  const safe = typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  return (
    <div className={`score-bar ${tone === "cyan" ? "cyan" : ""}`} style={{ height }}>
      <span style={{ width: `${safe * 100}%` }} />
    </div>
  );
}
