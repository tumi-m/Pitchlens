import { NextRequest, NextResponse } from "next/server";
import { getApps, initializeApp, applicationDefault } from "firebase-admin/app";
import { getAuth } from "firebase-admin/auth";
export const maxDuration = 30;
export const runtime = "nodejs";
const MAX_BODY = 1_500_000;
let active = 0;
let windowStart = Date.now();
let requests = 0;
export async function GET() {
  return NextResponse.json(
    {
      configured: !!process.env.ROBOFLOW_API_KEY,
      requiresSignIn: process.env.NODE_ENV === "production",
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
export async function POST(request: NextRequest) {
  if (!process.env.ROBOFLOW_API_KEY)
    return NextResponse.json(
      { error: "Frame detection is not configured." },
      { status: 503 },
    );
  if (process.env.NODE_ENV === "production") {
    try {
      const token = request.headers
        .get("authorization")
        ?.match(/^Bearer (.+)$/)?.[1];
      if (!token)
        return NextResponse.json(
          { error: "Sign in to use AI detection." },
          { status: 401 },
        );
      const app =
        getApps()[0] ??
        initializeApp({
          credential: applicationDefault(),
          projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
        });
      await getAuth(app).verifyIdToken(token, true);
    } catch {
      return NextResponse.json(
        { error: "Unable to verify sign-in." },
        { status: 401 },
      );
    }
  }
  // Conservative per-process budget. A distributed user quota is required before scaling.
  if (Date.now() - windowStart > 3600_000) {
    requests = 0;
    windowStart = Date.now();
  }
  if (active >= 2 || requests >= 120)
    return NextResponse.json(
      { error: "Detection is busy. Retry later." },
      { status: 429, headers: { "Retry-After": "60" } },
    );
  if (Number(request.headers.get("content-length")) > MAX_BODY)
    return NextResponse.json({ error: "Frame is too large." }, { status: 413 });
  active++;
  try {
    const reader = request.body?.getReader();
    if (!reader)
      return NextResponse.json({ error: "Missing frame." }, { status: 400 });
    let size = 0;
    const chunks: Uint8Array[] = [];
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY) {
        await reader.cancel();
        return NextResponse.json(
          { error: "Frame is too large." },
          { status: 413 },
        );
      }
      chunks.push(value);
    }
    let frame: unknown;
    try {
      frame = JSON.parse(Buffer.concat(chunks).toString("utf8")).frame;
    } catch {
      return NextResponse.json(
        { error: "Invalid JSON body." },
        { status: 400 },
      );
    }
    if (
      typeof frame !== "string" ||
      !/^\/9j\/[A-Za-z0-9+/]*={0,2}$/.test(frame) ||
      frame.length < 16
    )
      return NextResponse.json(
        { error: "Expected a base64 JPEG frame." },
        { status: 400 },
      );
    requests++;
    const project =
      process.env.ROBOFLOW_PROJECT ?? "football-players-detection-3zvbc";
    const version = process.env.ROBOFLOW_VERSION ?? "9";
    const url = new URL(
      `https://detect.roboflow.com/${encodeURIComponent(project)}/${encodeURIComponent(version)}`,
    );
    url.search = new URLSearchParams({
      api_key: process.env.ROBOFLOW_API_KEY,
      confidence: "35",
      overlap: "30",
      format: "json",
    }).toString();
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: frame,
      signal: AbortSignal.timeout(15_000),
      cache: "no-store",
    });
    if (!response.ok)
      return NextResponse.json(
        { error: "Detection provider rejected this frame." },
        { status: 502 },
      );
    const data = await response.json();
    if (!Array.isArray(data.predictions))
      return NextResponse.json(
        { error: "Invalid detection response." },
        { status: 502 },
      );
    const predictions = data.predictions
      .filter(
        (p: Record<string, unknown>) =>
          ["x", "y", "width", "height", "confidence", "class_id"].every(
            (key) => typeof p[key] === "number" && Number.isFinite(p[key]),
          ) &&
          typeof p.class === "string" &&
          Number(p.width) > 0 &&
          Number(p.height) > 0 &&
          Number(p.confidence) >= 0 &&
          Number(p.confidence) <= 1,
      )
      .slice(0, 100)
      .map((p: Record<string, unknown>) => ({
        x: p.x,
        y: p.y,
        width: p.width,
        height: p.height,
        confidence: p.confidence,
        class_id: p.class_id,
        class: p.class,
      }));
    return NextResponse.json(
      { predictions },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { error: "Detection timed out or was unavailable." },
      { status: 502 },
    );
  } finally {
    active--;
  }
}
