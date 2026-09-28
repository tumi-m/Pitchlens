// Public provider callback: the worker authenticates the exact raw bytes with
// Paystack's secret. Browser ownership and CSRF checks do not apply to webhooks.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 30;

const MAX_BODY = 64 * 1024;

export async function POST(request: Request) {
  const signature = request.headers.get("x-paystack-signature") || "";
  if (!/^[a-f0-9]{128}$/.test(signature))
    return Response.json({ detail: "Invalid payment signature" }, { status: 401 });
  const base = process.env.VISION_SERVICE_URL;
  const token = process.env.VISION_SERVICE_TOKEN;
  let target: URL;
  try {
    if (!base || !token) throw new Error();
    target = new URL(`${base.replace(/\/$/, "")}/billing/webhook`);
    if (target.protocol !== "https:" || target.username || target.password ||
        ["localhost", "127.0.0.1", "::1", "[::1]"].includes(target.hostname)) throw new Error();
  } catch {
    return Response.json({ detail: "Payment service is not configured" }, { status: 503 });
  }
  if (Number(request.headers.get("content-length")) > MAX_BODY)
    return Response.json({ detail: "Payment request is too large" }, { status: 413 });
  // Bound streamed requests as well as those carrying Content-Length.
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (reader) {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BODY) {
          await reader.cancel();
          return Response.json({ detail: "Payment request is too large" }, { status: 413 });
        }
        chunks.push(value);
      }
    } catch {
      return Response.json({ detail: "Incomplete payment request" }, { status: 400 });
    } finally {
      reader.releaseLock();
    }
  }
  const raw = Buffer.concat(chunks);
  try {
    const upstream = await fetch(target, {
      method: "POST", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(20_000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "x-paystack-signature": signature },
      body: raw,
    });
    // Only a successful durable worker acknowledgement acknowledges delivery.
    return Response.json(upstream.ok ? { received: true } : { detail: "Payment event was not accepted" },
      { status: upstream.ok ? 200 : upstream.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ detail: "Payment service unavailable; retry delivery" }, { status: 503 });
  }
}
