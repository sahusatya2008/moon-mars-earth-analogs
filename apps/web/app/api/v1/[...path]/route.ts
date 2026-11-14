import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";
export const runtime = "nodejs";

// The public site talks to this route on its own origin. The route forwards the
// request to FastAPI over loopback (or the Compose service name), so the API
// port never needs to be exposed and no CORS preflight is involved.
const API_BASE = (
  process.env.TERRAX_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

const ALLOWED_METHODS = new Set(["GET", "HEAD", "POST", "OPTIONS"]);

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  "accept-encoding",
]);

// A public demo should not expose the administrative or destructive API. The
// web app only needs reads, analysis creation, retries, comparisons, reports,
// dataset probes, and the cited Evidence Q&A endpoint.
const BLOCKED_PREFIXES = new Set(["admin"]);
const BLOCKED_POST_PATHS = new Set(["regions"]);

const WRITE_LIMITS: Record<string, number> = {
  analyses: 8,
  reports: 30,
  comparisons: 30,
  assistant: 30,
  datasets: 30,
};

type Bucket = { count: number; resetAt: number };
const writeBuckets = new Map<string, Bucket>();

function jsonError(status: number, detail: string, headers: HeadersInit = {}) {
  return Response.json(
    { detail },
    {
      status,
      headers: {
        "cache-control": "no-store",
        ...headers,
      },
    },
  );
}

function clientAddress(request: NextRequest): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) {
    const first = forwarded.split(",")[0]?.trim();
    if (first) return first;
  }
  return request.headers.get("x-real-ip") || "unknown";
}

function writeAllowed(request: NextRequest, group: string): boolean {
  const limit = WRITE_LIMITS[group];
  if (!limit) return true;
  const key = `${clientAddress(request)}:${group}`;
  const now = Date.now();
  const bucket = writeBuckets.get(key);
  if (!bucket || bucket.resetAt <= now) {
    writeBuckets.set(key, { count: 1, resetAt: now + 60_000 });
    if (writeBuckets.size > 5_000) {
      for (const [staleKey, stale] of writeBuckets) {
        if (stale.resetAt <= now) writeBuckets.delete(staleKey);
      }
    }
    return true;
  }
  if (bucket.count >= limit) return false;
  bucket.count += 1;
  return true;
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const group = path[0] || "";

  if (!ALLOWED_METHODS.has(request.method)) {
    return jsonError(
      405,
      "This public deployment exposes read and analysis endpoints only.",
      { Allow: "GET, HEAD, POST, OPTIONS" },
    );
  }

  if (BLOCKED_PREFIXES.has(group)) {
    return jsonError(404, "Not found");
  }

  const joined = path.join("/");
  if (request.method === "POST" && (BLOCKED_POST_PATHS.has(joined) || joined.endsWith("/cancel"))) {
    return jsonError(405, "This endpoint is available only on the local deployment.");
  }

  if (request.method === "POST" && !writeAllowed(request, group)) {
    return jsonError(429, "Too many write requests; retry shortly.", {
      "Retry-After": "60",
    });
  }

  const target = new URL(
    `${API_BASE}/api/v1/${path.map((segment) => encodeURIComponent(segment)).join("/")}`,
  );
  target.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP) headers.delete(name);
  headers.delete("cookie");

  const forwardedHost = request.headers.get("x-forwarded-host") || request.headers.get("host");
  if (forwardedHost) headers.set("x-forwarded-host", forwardedHost);
  headers.set(
    "x-forwarded-proto",
    request.headers.get("x-forwarded-proto") || request.nextUrl.protocol.replace(":", ""),
  );

  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return jsonError(502, "The SNSX SPACE API is unavailable. No result was invented.");
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, name) => {
    if (HOP_BY_HOP.has(name) || name === "content-encoding") return;
    responseHeaders.append(name, value);
  });

  const contentType = upstream.headers.get("content-type") || "";
  if (contentType.includes("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
  } else if (!responseHeaders.has("cache-control")) {
    responseHeaders.set("cache-control", "no-store");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export {
  proxy as GET,
  proxy as HEAD,
  proxy as OPTIONS,
  proxy as POST,
};
