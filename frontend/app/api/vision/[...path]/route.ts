import { NextRequest } from "next/server";
import { timingSafeEqual } from "node:crypto";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const LOOPBACK = ["localhost", "127.0.0.1"];
const OWNER = /^[a-f0-9]{32}$/;
const OWNER_COOKIE = "pitchlens-vision-owner";
// Vercel rejects function request bodies over 4.5 MB; the client sends 4 MB chunks.
const MAX_CHUNK = 4.5 * 1024 * 1024;

/** Spaces and line breaks around a code are never part of it: the site trims what
 * people type, and a value pasted into the hosting dashboard often carries a newline. */
function sameSecret(given: string | null, expected: string) {
  if (!given) return false;
  const a = Buffer.from(given.trim());
  const b = Buffer.from(expected.trim());
  return b.length > 0 && a.length === b.length && timingSafeEqual(a, b);
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const route = path.join("/");
  if (
    !/^(health|jobs|venues|ball-model|ball-model\/train|billing\/(summary|checkout|verify)|jobs\/from-url|jobs\/[a-f0-9]{32}(\/(result|video|cancel|start|retry|rerun|analysis|calibration|calibration\/preview|review|details|ball-labels|training-consent|teams|frames\/\d{1,6}))?)$/.test(
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
  // The HttpOnly cookie outlives script storage (Safari's 7-day cap, "clear
  // website data"), so a valid cookie wins: a freshly minted browser key must
  // never hide this browser's matches or replace the only surviving copy.
  const cookie = request.cookies.get(OWNER_COOKIE)?.value || "";
  const header = request.headers.get("x-pitchlens-owner") || "";
  const cookieOwner = OWNER.test(cookie) ? cookie : "";
  const owner = cookieOwner || (OWNER.test(header) ? header : "");
  // Optional shared code. With VISION_ACCESS_CODE unset, anyone who has the
  // site's link can start analyses on the paid worker (fine for a small test
  // group); set it again to require the code. Each browser still only sees its
  // own matches.
  const accessCode = (process.env.VISION_ACCESS_CODE || "").trim();
  const writes = !["GET", "HEAD"].includes(request.method);
  // Deleting costs no compute, and an owner must always be able to erase their
  // footage, even after the code changes: the owner key and the origin checks
  // below still guard it. The same holds for the training opt-in: withdrawing
  // must never need the current code, and granting it costs no compute.
  const erasure = request.method === "DELETE" && /^jobs\/[a-f0-9]{32}$/.test(route) && !!owner;
  const consent = request.method === "POST" && /^jobs\/[a-f0-9]{32}\/training-consent$/.test(route) && !!owner;
  if (writes) {
    const origin = request.headers.get("origin");
    // Browsers send Origin on same-origin POST/PUT/DELETE. Locally a same-origin
    // fetch metadata header also passes; a request with neither (another device
    // spoofing Host: localhost) is refused.
    if (!origin && (hosted || request.headers.get("sec-fetch-site") !== "same-origin"))
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
    // Analysis uses paid compute: the optional shared code keeps strangers out.
    const sent = request.headers.get("x-pitchlens-access");
    if (accessCode && !erasure && !consent && !sameSecret(sent, accessCode))
      return Response.json(
        {
          detail: sent
            ? "That access code was not accepted."
            : "Enter the access code to continue.",
          code: "access",
        },
        { status: 401 },
      );
  }
  // Training switches the ball model every browser uses: never from an open site.
  // The person reading this is a team owner, not the operator: no settings named.
  if (route === "ball-model/train" && request.method !== "GET" && !accessCode)
    return Response.json(
      { detail: "Training is not available on this site. Your labels are saved for the next ball-model update." },
      { status: 403 },
    );
  // Rebuild the query so a browser can never pick someone else's owner key. The
  // key goes only in the x-pitchlens-owner header below, never in the URL: the
  // worker's access log prints every path with its query.
  const search = new URLSearchParams(request.nextUrl.search);
  search.delete("owner");
  // Listings and new analyses are always scoped to a browser, locally too: an
  // ownerless listing would show every match on the worker.
  if ((route === "jobs" || route === "jobs/from-url" || route === "venues") && !owner) {
    if (request.method === "GET") return Response.json([]);
    return Response.json(
      { detail: "Allow site data for this site to upload and keep your matches.", code: "storage" },
      { status: 400 },
    );
  }
  if (hosted && route !== "health" && !owner) {
    return Response.json({ detail: "Open this match in the browser that uploaded it.", code: "ownership" }, { status: 401 });
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    // Relayed byte-for-byte: a compressed hop would break Content-Length/Range.
    "accept-encoding": "identity",
  };
  // The media element cannot set a custom header; its HttpOnly cookie is
  // established by the authenticated status/result request before playback.
  if (owner) headers["x-pitchlens-owner"] = owner;
  if (hosted) headers["x-pitchlens-require-owner"] = "1";
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
    if (init.body.byteLength > MAX_CHUNK)
      return Response.json({ detail: "Upload chunk is too large." }, { status: 413 });
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
      // Training from the site needs both an access code here (see above) and
      // the worker's VISION_SITE_TRAINING=1; otherwise its button could only fail.
      { ...data, hosted, accessRequired: !!accessCode, siteTraining: !!accessCode && data?.siteTraining === true },
      { status: upstream.status, headers: { "Cache-Control": "no-store" } },
    );
  }
  const out = new Headers({ "Cache-Control": "private, no-store" });
  if (owner) {
    // The page copies this back into its storage, so both copies converge.
    out.set("x-pitchlens-owner", owner);
    // Set only when there is no valid cookie or it already holds this key: the
    // durable copy is refreshed, never replaced by a different header key.
    if (upstream.ok && (!cookieOwner || cookieOwner === owner))
      out.append("Set-Cookie", `${OWNER_COOKIE}=${owner}; Path=/api/vision; Max-Age=31536000; HttpOnly; SameSite=Strict${hosted ? "; Secure" : ""}`);
  }
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
  if (route === "ball-model" && request.method === "GET" && !accessCode && upstream.ok) {
    // An open site refuses training above, whatever the worker allows: say so up front.
    const data = await upstream.json().catch(() => null);
    out.delete("content-length");
    out.set("content-type", "application/json");
    return new Response(JSON.stringify(data && typeof data === "object" ? { ...data, siteTraining: false } : data), {
      status: upstream.status,
      headers: out,
    });
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: out,
  });
}
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
