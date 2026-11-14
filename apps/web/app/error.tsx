"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("SNSX SPACE page error", error);
  }, [error]);

  return (
    <main className="shell" style={{ padding: "70px 0" }}>
      <div className="panel panel-pad" style={{ maxWidth: 720 }}>
        <div className="eyebrow">Interface error</div>
        <h2 style={{ marginTop: 10 }}>This view could not be rendered</h2>
        <p className="small muted" style={{ lineHeight: 1.7 }}>
          The API may be unavailable, or the browser may not support a required capability. No
          sample data is substituted. The stored analysis is unchanged and can be reopened.
        </p>
        {error.message ? (
          <div className="error-panel" style={{ marginTop: 16 }}>
            {error.message}
            {error.digest ? <div className="tiny faint mono" style={{ marginTop: 6 }}>digest {error.digest}</div> : null}
          </div>
        ) : null}
        <div style={{ display: "flex", gap: 10, marginTop: 18, flexWrap: "wrap" }}>
          <button className="btn primary" onClick={reset} type="button">
            Try again
          </button>
          <Link className="btn" href="/analyses">
            Back to analyses
          </Link>
        </div>
      </div>
    </main>
  );
}
