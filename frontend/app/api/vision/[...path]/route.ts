import { NextRequest } from "next/server";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const route = path.join("/");
  if (
    !/^(health|jobs|jobs\/[a-f0-9]{32}(\/(result|video|cancel))?)$/.test(route)
  )
    return Response.json({ detail: "Not found" }, { status: 404 });
  const base = process.env.VISION_SERVICE_URL;
  const token = process.env.VISION_SERVICE_TOKEN;
  if (!base || !token)
    return Response.json(
      {
        detail:
          "Local vision worker is not configured. Start it with the documented setup command.",
        available: false,
      },
      { status: 503 },
    );
  // This installation is a personal local workspace. Never expose its files in a cloud deployment.
  const host = request.headers.get("host")?.split(":")[0];
  if (!["localhost", "127.0.0.1"].includes(host || ""))
    return Response.json(
      { detail: "Local vision is only available on this computer." },
      { status: 403 },
    );
  const target = new URL(base);
  if (!["localhost", "127.0.0.1"].includes(target.hostname))
    return Response.json(
      { detail: "Vision worker must use a loopback address." },
      { status: 503 },
    );
  if (request.method === "POST") {
    const origin = request.headers.get("origin");
    let originHost: string | null = null;
    try { originHost = origin ? new URL(origin).host : null; }
    catch { return Response.json({ detail: "Invalid origin." }, { status: 403 }); }
    if (
      (origin && originHost !== request.headers.get("host")) ||
      request.headers.get("sec-fetch-site") === "cross-site"
    )
      return Response.json(
        { detail: "Cross-origin upload rejected." },
        { status: 403 },
      );
  }
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  for (const name of ["content-type", "range"]) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }
  try {
    const init: RequestInit & { duplex?: string } = {
      method: request.method,
      headers,
      cache: "no-store",
      signal: request.signal,
    };
    if (request.method === "POST") {
      init.body = request.body;
      init.duplex = "half";
    }
    const upstream = await fetch(
      `${base.replace(/\/$/, "")}/${route}${request.nextUrl.search}`,
      init,
    );
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
  } catch {
    return Response.json(
      {
        detail:
          "Cannot reach the local vision worker. Start the worker and retry.",
        available: false,
      },
      { status: 503 },
    );
  }
}
export const GET = proxy;
export const POST = proxy;
