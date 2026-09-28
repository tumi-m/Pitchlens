import { NextRequest } from "next/server";
import { timingSafeEqual } from "node:crypto";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const LOOPBACK = ["localhost", "127.0.0.1"];
const OWNER = /^[a-f0-9]{32}$/;
// Vercel rejects function request bodies over 4.5 MB; the client sends 4 MB chunks.
const MAX_CHUNK = 4.5 * 1024 * 1024;

function sameSecret(given: string | null, expected: string) {
  if (!given) return false;
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const route = path.join("/");
  if (
    !/^(health|jobs|jobs\/from-url|jobs\/[a-f0-9]{32}(\/(result|video|cancel|start))?)$/.test(
      route,
    )
  )
    return Response.json({ detail: "Not found" }, { status: 404 });
  const base = process.env.VISION_SERVICE_URL;
  const token = process.env.VISION_SERVICE_TOKEN;
  if (!base || !token)
    return Response.json(
      {
        detail:
          "The vision worker is not configured. Set VISION_SERVICE_URL and VISION_SERVICE_TOKEN.",
        available: false,
        configured: false,
      },
      { status: 503 },
    );
  let target: URL;
  try {
    target = new URL(base);
  } catch {
    return Response.json(
      { detail: "VISION_SERVICE_URL is not a valid URL.", available: false },
      { status: 503 },
    );
  }
  // Hosted worker: HTTPS on a public host, reached server-to-server with the
  // bearer token. Local worker: loopback only, served only to this computer.
  const hosted = !LOOPBACK.includes(target.hostname);
  if (hosted && target.protocol !== "https:")
    return Response.json(
      { detail: "A hosted vision worker must use an https:// URL.", available: false },
      { status: 503 },
    );
  if (!hosted) {
    const host = request.headers.get("host")?.split(":")[0];
    if (!LOOPBACK.includes(host || ""))
      return Response.json(
        {
          detail: "Local vision is only available on this computer.",
          available: false,
          hosted: false,
        },
        { status: 403 },
      );
  }
  const accessCode = process.env.VISION_ACCESS_CODE || "";
  // A public site in front of a paid worker must not run open to the internet.
  if (hosted && !accessCode)
    return Response.json(
      {
        detail:
          "Hosted analysis needs VISION_ACCESS_CODE set on the website before it can accept videos.",
        available: false,
        configured: false,
        hosted,
      },
      { status: 503 },
    );
  const writes = request.method !== "GET";
  if (writes) {
    const origin = request.headers.get("origin");
    if (hosted && !origin)
      return Response.json({ detail: "Missing origin." }, { status: 403 });
    let originHost: string | null = null;
    try {
      originHost = origin ? new URL(origin).host : null;
    } catch {
      return Response.json({ detail: "Invalid origin." }, { status: 403 });
    }
    if (
      (origin && originHost !== request.headers.get("host")) ||
      request.headers.get("sec-fetch-site") === "cross-site"
    )
      return Response.json(
        { detail: "Cross-origin upload rejected." },
        { status: 403 },
      );
    // Analysis uses paid compute: an optional shared code keeps strangers out.
    const sent = request.headers.get("x-pitchlens-access");
    if (accessCode && !sameSecret(sent, accessCode))
      return Response.json(
        {
          detail: sent
            ? "That access code was not accepted."
            : "Enter the access code to analyse videos.",
          code: "access",
        },
        { status: 401 },
      );
  }
  // Rebuild the query so a browser can never pick someone else's owner key.
  const search = new URLSearchParams(request.nextUrl.search);
  search.delete("owner");
  const owner = request.headers.get("x-pitchlens-owner");
  if (route === "jobs" || route === "jobs/from-url") {
    if (owner && OWNER.test(owner)) search.set("owner", owner);
    else if (hosted && request.method === "GET") return Response.json([]);
    else if (hosted)
      return Response.json(
        { detail: "Browser storage is required to track your analyses." },
        { status: 400 },
      );
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    // Relayed byte-for-byte: a compressed hop would break Content-Length/Range.
    "accept-encoding": "identity",
  };
  for (const name of ["content-type", "range"]) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }
  const init: RequestInit & { duplex?: string } = {
    method: request.method,
    headers,
    cache: "no-store",
    signal: request.signal,
  };
  if (request.method === "PUT") {
    // Chunks are small: buffer them so the worker receives an exact Content-Length.
    if (Number(request.headers.get("content-length")) > MAX_CHUNK)
      return Response.json(
        { detail: "Upload chunk is too large." },
        { status: 413 },
      );
    init.body = await request.arrayBuffer();
  } else if (request.method === "POST") {
    init.body = request.body;
    init.duplex = "half";
  }
  const query = search.toString();
  let upstream: Response;
  try {
    upstream = await fetch(
      `${base.replace(/\/$/, "")}/${route}${query ? `?${query}` : ""}`,
      init,
    );
  } catch {
    return Response.json(
      {
        detail: hosted
          ? "Cannot reach the vision server. It may be starting up; retry in a minute."
          : "Cannot reach the local vision worker. Start the worker and retry.",
        available: false,
        hosted,
      },
      { status: 503 },
    );
  }
  if (route === "health") {
    const data = await upstream.json().catch(() => ({}));
    return Response.json(
      { ...data, hosted, accessRequired: !!accessCode },
      { status: upstream.status, headers: { "Cache-Control": "no-store" } },
    );
  }
  const out = new Headers({ "Cache-Control": "private, no-store" });
  for (const name of [
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "content-disposition",
  ]) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: out,
  });
}
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
