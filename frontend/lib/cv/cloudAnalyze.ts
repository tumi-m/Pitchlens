import type { TrackingPayload } from "./types";
import { isTrackingPayload } from "./importAnalysis";

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
}

export function cloudApiConfigured(): boolean {
  return /^https?:\/\//.test(apiBase());
}

export async function cloudStatus(signal?: AbortSignal) {
  const base = apiBase();
  if (!base) return { enabled: false, ultralytics: false, roboflowConfigured: false };
  const res = await fetch(`${base}/api/v1/cv-status`, { signal, cache: "no-store" });
  if (!res.ok) throw new Error("Railway engine is unreachable.");
  return res.json();
}

export async function analyzeOnRailway(
  file: File,
  options: { signal?: AbortSignal; onStage?: (label: string) => void; onProgress?: (pct: number) => void } = {},
): Promise<TrackingPayload> {
  const base = apiBase();
  if (!base) throw new Error("NEXT_PUBLIC_API_URL is not set on Vercel.");
  const { signal, onStage = () => {}, onProgress = () => {} } = options;
  onStage("Uploading video to Railway");
  onProgress(5);
  const body = new FormData();
  body.append("video", file);
  const start = await fetch(`${base}/api/v1/analyze`, { method: "POST", body, signal });
  if (!start.ok) {
    const err = await start.json().catch(() => ({}));
    throw new Error(typeof err.detail === "string" ? err.detail : "Railway rejected the upload.");
  }
  const { jobId } = await start.json();
  if (!jobId) throw new Error("Railway did not return a job id.");
  for (;;) {
    signal?.throwIfAborted();
    const poll = await fetch(`${base}/api/v1/analyze/${jobId}`, { signal, cache: "no-store" });
    if (!poll.ok) throw new Error("Lost contact with the Railway job.");
    const job = await poll.json();
    onStage(job.message || job.status);
    onProgress(Math.max(8, Number(job.progress) || 8));
    if (job.status === "completed") {
      if (!isTrackingPayload(job.result)) throw new Error("Railway returned an unexpected analysis payload.");
      return job.result;
    }
    if (job.status === "failed") throw new Error(job.error || "Cloud analysis failed.");
    await new Promise((r) => setTimeout(r, 2000));
  }
}
