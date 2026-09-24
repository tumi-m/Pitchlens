export type VisionJob = {
  id: string;
  title: string;
  status: string;
  stage: string;
  progress: number;
  createdAt: number;
  processedSeconds?: number;
  etaSeconds?: number;
  video?: { duration: number };
  /** Hosted retention removed the footage; the measured result remains. */
  videoDeleted?: boolean;
};
export type VisionPlayer = {
  id: number;
  team: number;
  box: number[];
  confidence: number;
};
export type VisionFrame = {
  t: number;
  scene: number;
  players: VisionPlayer[];
  ball: { x: number; y: number; box: number[]; confidence: number } | null;
};
export type VisionResult = {
  schemaVersion: 1;
  source: "computer-vision";
  model: string;
  modelSha256: string;
  ballModel?: string;
  ballModelSha256?: string;
  video: { duration: number; width: number; height: number; fps: number };
  analysedDuration: number;
  sampleFps: number;
  teams: { id: number; label: string; colour: string }[];
  metrics: {
    sampledFrames: number;
    playerFrames: number;
    ballFrames: number;
    teamSeconds: number[];
    unknownSeconds: number;
    possessionShare: (number | null)[];
    possessionCoverage: number;
    trackCount: number;
    events: {
      id: string;
      type: string;
      t: number;
      from: number;
      to: number;
      team: number;
      confidence: number;
      status: string;
    }[];
  };
  frames: VisionFrame[];
  limitations: string[];
};
export type VisionHealth = {
  available: boolean;
  profiles?: string[];
  /** Worker runs on a server (not this computer). */
  hosted?: boolean;
  /** The site owner requires an access code before videos can be analysed. */
  accessRequired?: boolean;
  retentionHours?: number | null;
  /** false when this deployment has no VISION_SERVICE_URL/TOKEN at all. */
  configured?: boolean;
  detail?: string;
};
export const clockTime = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export class VisionError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

const OWNER_KEY = "pitchlens-vision-owner";
const ACCESS_KEY = "pitchlens-vision-access";

/** Random per-browser key: the hosted worker only lists this browser's analyses. */
export function visionOwner(): string {
  try {
    const saved = localStorage.getItem(OWNER_KEY);
    if (saved && /^[a-f0-9]{32}$/.test(saved)) return saved;
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const created = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    localStorage.setItem(OWNER_KEY, created);
    return created;
  } catch {
    return "";
  }
}

export function visionAccessCode(): string {
  try {
    return localStorage.getItem(ACCESS_KEY) || "";
  } catch {
    return "";
  }
}

export function setVisionAccessCode(code: string) {
  try {
    if (code) localStorage.setItem(ACCESS_KEY, code);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {}
}

function visionHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const owner = visionOwner();
  if (owner) headers.set("x-pitchlens-owner", owner);
  const access = visionAccessCode();
  if (access) headers.set("x-pitchlens-access", access);
  return headers;
}

export async function visionJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`/api/vision/${path}`, {
    ...init,
    headers: visionHeaders(init?.headers),
  });
  let data: { detail?: string; code?: string } = {};
  try {
    data = await response.json();
  } catch {
    if (!response.ok)
      throw new VisionError(
        `Vision request failed (${response.status})`,
        response.status,
      );
  }
  if (!response.ok)
    throw new VisionError(
      data.detail || "Vision request failed",
      response.status,
      data.code,
    );
  return data as T;
}

/** Stays under Vercel's 4.5 MB function request limit, with room for headers. */
export const UPLOAD_CHUNK = 4 * 1024 * 1024;
const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Upload cancelled.", "AbortError"));
      },
      { once: true },
    );
  });

/** Network drops shorter than this resume where they left off. It stays under
 *  the worker's 5-minute idle release (VISION_UPLOAD_IDLE_SECONDS). */
const RESUME_WINDOW_MS = 240_000;
const transient = (e: unknown) =>
  !(e instanceof VisionError) || e.status >= 500 || e.status === 429;

/**
 * Resumable chunked upload: reserve the worker, send 4 MB pieces (each retried
 * with backoff, resyncing if the server already has them), then start analysis.
 */
export async function uploadToVision(
  file: Blob,
  options: {
    title: string;
    profile?: string;
    fps?: string;
    signal?: AbortSignal;
    onProgress?: (percent: number) => void;
  },
): Promise<VisionJob> {
  const { signal, onProgress } = options;
  const query = new URLSearchParams({
    title: options.title,
    profile: options.profile || "general",
    fps: options.fps || "3",
    size: String(file.size),
  });
  signal?.throwIfAborted();
  // Never abort the reservation mid-flight: the worker would stay reserved
  // under an id this browser never learns. Cancellation is checked just after.
  const job = await visionJson<VisionJob>(`jobs?${query}`, {
    method: "POST",
    headers: { "Content-Type": "video/mp4" },
  });
  let starting = false;
  try {
    signal?.throwIfAborted();
    let offset = 0;
    let lastAck = Date.now();
    onProgress?.(0);
    while (offset < file.size) {
      const piece = file.slice(offset, Math.min(file.size, offset + UPLOAD_CHUNK));
      for (let attempt = 0; ; attempt++) {
        signal?.throwIfAborted();
        try {
          const { received } = await visionJson<{ received: number }>(
            `jobs/${job.id}/video?offset=${offset}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/octet-stream" },
              body: piece,
              signal,
            },
          );
          offset = received;
          lastAck = Date.now();
          break;
        } catch (e) {
          if (signal?.aborted) throw e;
          const resync =
            e instanceof VisionError &&
            e.status === 409 &&
            /Expected offset (\d+)/.exec(e.message);
          if (resync) {
            offset = Number(resync[1]);
            break;
          }
          // Lost connection (phone switching networks, Wi-Fi blip): keep trying
          // until the worker would give up on us. Server errors: a few retries.
          const network = !(e instanceof VisionError);
          const giveUp = network
            ? Date.now() - lastAck > RESUME_WINDOW_MS
            : !transient(e) || attempt >= 4;
          if (giveUp) throw e;
          await sleep(Math.min(15_000, 1000 * 2 ** attempt), signal);
        }
      }
      onProgress?.(Math.round((offset / file.size) * 100));
    }
    starting = true;
    return await startAnalysis(job.id, signal);
  } catch (e) {
    // Once /start has been sent the server may already be analysing, and a
    // cancel would stop it; the worker releases unstarted uploads by itself.
    if (!starting)
      // Free the worker straight away instead of waiting for the idle timeout.
      visionJson(`jobs/${job.id}/cancel`, { method: "POST" }).catch(() => {});
    throw e;
  }
}

/** /start is retried: a lost response must not throw away a complete upload. */
async function startAnalysis(id: string, signal?: AbortSignal): Promise<VisionJob> {
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted();
    try {
      return await visionJson<VisionJob>(`jobs/${id}/start`, {
        method: "POST",
        signal,
      });
    } catch (e) {
      if (signal?.aborted) throw e;
      // An earlier attempt may have started it and only the reply was lost.
      if (e instanceof VisionError && e.status === 409) {
        const current = await visionJson<VisionJob>(`jobs/${id}`).catch(() => null);
        if (current && ["processing", "completed"].includes(current.status))
          return current;
        throw e;
      }
      if (!transient(e) || attempt >= 4) throw e;
      await sleep(Math.min(15_000, 1000 * 2 ** attempt), signal);
    }
  }
}

/** The worker downloads the video itself; nothing large passes through the browser. */
export async function analyseYouTube(
  url: string,
  options: { title?: string; profile?: string; fps?: string },
): Promise<VisionJob> {
  const query = new URLSearchParams({
    url,
    title: options.title || "",
    profile: options.profile || "general",
    fps: options.fps || "3",
  });
  return visionJson<VisionJob>(`jobs/from-url?${query}`, { method: "POST" });
}
