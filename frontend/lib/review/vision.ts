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
  ball: {
    x: number;
    y: number;
    box: number[] | null;
    confidence: number;
    /** Bridged between two observations on a confirmed path; not seen by the detector. */
    inferred?: boolean;
    /** Confirmed by consistency across frames rather than a single-frame detection. */
    recovered?: boolean;
  } | null;
};
export type VisionResult = {
  schemaVersion: 1;
  source: "computer-vision";
  model: string;
  modelSha256: string;
  ballModel?: string;
  ballModelSha256?: string;
  /** "none" is a player-load job: the ball model was not run. */
  ballSearch?: string;
  video: { duration: number; width: number; height: number; fps: number };
  analysedDuration: number;
  analysedStart?: number;
  /** Timings as the engine measured them; any may be missing from older results. */
  performance?: {
    totalSeconds?: number;
    playerInferenceSeconds?: number;
    playerInferenceCalls?: number;
    /** null (or absent, before 2.7) on player-load jobs: the ball model was not run. */
    ballInferenceSeconds?: number | null;
    setupSeconds?: number;
    decodeSeconds?: number;
    cameraMotionSeconds?: number;
    ballRecoverySeconds?: number;
    device?: string;
    batchSize?: number;
    analysedFrames?: number;
  };
  sampleFps: number;
  teams: { id: number; label: string; colour: string }[];
  metrics: {
    sampledFrames: number;
    playerFrames: number;
    ballFrames: number;
    ballFramesInferred?: number;
    /** Possession figures are null on player-load jobs: the ball was not looked for. */
    teamSeconds: number[] | null;
    unknownSeconds: number;
    possessionShare: (number | null)[];
    possessionCoverage: number | null;
    trackCount: number;
    /** Metres each kit ran, with the camera removed. Absent on results from before 2.6.
     *  A kit with no usable track has null (not measured), never 0. */
    playerLoad?: {
      metres: [number | null, number | null];
      maxSpeed: number | null;
      steps: number;
      /** Samples per kit that passed the scene, camera, height and gap checks. */
      measuredSamples?: [number, number];
      method: string;
      playerHeightM: number;
      footNoisePx: number;
    };
    /** Added in pipeline 1.5; older results do not have it; null on player-load jobs. */
    possessions?: {
      teams: {
        count: number;
        averageSeconds: number | null;
        longestSeconds: number | null;
        passesPerPossession: number | null;
        passesAllowedPerRegain: number | null;
      }[];
      chains: {
        team: number;
        start: number;
        end: number;
        controlSeconds: number;
        passes: number;
      }[];
    } | null;
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
  diagnostics?: boolean;
  available: boolean;
  profiles?: string[];
  /** Worker runs on a server (not this computer). */
  hosted?: boolean;
  /** The site owner requires an access code before videos can be analysed. */
  accessRequired?: boolean;
  retentionHours?: number | null;
  /** Largest upload the worker accepts, in bytes. */
  maxBytes?: number;
  /** Analysis runs on a Modal GPU (minutes rather than an hour). */
  gpu?: boolean;
  /** false when this deployment has no VISION_SERVICE_URL/TOKEN at all. */
  configured?: boolean;
  /** The site may start ball-model training (an access code is set and the worker allows it). */
  siteTraining?: boolean;
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
const OWNER = /^[a-f0-9]{32}$/;

/** Random per-browser key: the hosted worker only lists this browser's analyses. */
export function visionOwner(): string {
  try {
    const saved = localStorage.getItem(OWNER_KEY);
    if (saved && OWNER.test(saved)) return saved;
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const created = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    localStorage.setItem(OWNER_KEY, created);
    return created;
  } catch {
    return "";
  }
}

/** The proxy answers with the key it used: its HttpOnly cookie wins over a key
 *  minted after script storage was cleared, so copy that one back. */
function adoptOwner(response: Response) {
  const used = response.headers.get("x-pitchlens-owner");
  if (!used || !OWNER.test(used)) return;
  try {
    if (localStorage.getItem(OWNER_KEY) !== used) localStorage.setItem(OWNER_KEY, used);
  } catch {}
}

/** Zero-width, bidi and BOM marks ride along with pasted codes; they are never part of one. */
export function cleanAccessCode(code: string): string {
  return code.replace(/\p{Cf}/gu, "").trim();
}

/** Request headers carry Latin-1 only: a code with ’, — or emoji cannot be sent at all. */
export const accessCodeSendable = (code: string) =>
  Array.from(code).every((c) => (c.codePointAt(0) ?? 0) <= 0xff);

export const ACCESS_CODE_CHARACTERS = "Codes use letters, numbers and symbols only.";

export function visionAccessCode(): string {
  try {
    return cleanAccessCode(localStorage.getItem(ACCESS_KEY) || "");
  } catch {
    return "";
  }
}

/** Fired when the site asks for an access code (none saved, or the saved one was refused). */
export const ACCESS_EVENT = "pitchlens:access-required";

function accessRejected() {
  // A refused code is dropped so the next prompt starts empty.
  setVisionAccessCode("");
  try {
    window.dispatchEvent(new CustomEvent(ACCESS_EVENT));
  } catch {}
}

/** Saves the code as it will be sent. Returns false (and keeps nothing) for a
 *  code that cannot be sent; the field then says which characters work. */
export function setVisionAccessCode(code: string): boolean {
  const clean = cleanAccessCode(code);
  const sendable = accessCodeSendable(clean);
  try {
    if (clean && sendable) localStorage.setItem(ACCESS_KEY, clean);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {}
  return sendable;
}

function visionHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const owner = visionOwner();
  if (owner) headers.set("x-pitchlens-owner", owner);
  const access = visionAccessCode();
  try {
    if (access) headers.set("x-pitchlens-access", access);
  } catch {
    // A code saved before it was checked (’ or — pasted in): drop it and ask
    // again, rather than failing every request with a TypeError.
    accessRejected();
  }
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
  adoptOwner(response);
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
  if (!response.ok) {
    if (data.code === "access") accessRejected();
    throw new VisionError(
      data.detail || "Vision request failed",
      response.status,
      data.code,
    );
  }
  return data as T;
}

/** Stays under Vercel's 4.5 MB function request limit, with room for headers. */
export const UPLOAD_CHUNK = 4 * 1024 * 1024;

/** A lost reply must navigate to the same run, not launch paid inference twice. */
export async function rerunSavedVision(id: string, mode: "section" | "full", start: number, requestId: string) {
  const query = new URLSearchParams({ mode, start: String(start), requestId });
  for (let attempt = 0; ; attempt++) {
    try {
      return await visionJson<VisionJob>(`jobs/${id}/rerun?${query}`, { method: "POST" });
    } catch (e) {
      if (!transient(e) || attempt >= 3) throw e;
      await sleep(1000 * 2 ** attempt);
    }
  }
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const aborted = () => {
      clearTimeout(timer);
      reject(new DOMException("Upload cancelled.", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", aborted);
      resolve();
    }, ms);
    signal?.addEventListener("abort", aborted, { once: true });
    if (signal?.aborted) aborted();
  });

/** Network drops shorter than this resume where they left off. It stays under
 *  the worker's 5-minute idle release (VISION_UPLOAD_IDLE_SECONDS). */
const RESUME_WINDOW_MS = 240_000;
// Not 429: the worker's only 429 here is its 24-hour cap, which retrying cannot lift.
const transient = (e: unknown) =>
  !(e instanceof VisionError) || e.status >= 500;

/** fetch rejects with a TypeError when the connection drops ("Failed to fetch",
 *  "Load failed", "NetworkError…"): say so in words people recognise. */
export const CONNECTION_LOST =
  "Connection lost. Check your signal and press Analyse again; your file is still selected.";
export const connectionLost = (e: unknown) => e instanceof TypeError;

/**
 * Choices shared by every analyse button: the football-trained models when the
 * worker has them, and player load unless the ball model is asked for (it costs
 * about 55% more GPU and followed a graphic on real tape).
 */
export function defaultVisionOptions(
  health: Pick<VisionHealth, "profiles"> | null,
  choice: { playerLoadOnly: boolean; adaptive?: boolean; profile?: string },
): { profile: string; search: "none" | "adaptive" | "exhaustive" } {
  const profile = choice.profile ?? (health?.profiles?.includes("broadcast") ? "broadcast" : "general");
  const search = choice.playerLoadOnly
    ? "none"
    : choice.adaptive && profile !== "general"
      ? "adaptive"
      : "exhaustive";
  return { profile, search };
}

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
    diagnostic?: boolean;
    start?: number;
    search?: "exhaustive" | "adaptive" | "none";
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
    diagnostic: String(options.diagnostic ?? false),
    start: String(options.start ?? 0),
    search: options.search ?? "exhaustive",
    requestId: crypto.randomUUID(),
  });
  signal?.throwIfAborted();
  // Never abort the reservation mid-flight: the worker would stay reserved
  // under an id this browser never learns. Cancellation is checked just after.
  let job: VisionJob;
  for (let attempt = 0; ; attempt++) {
    try {
      job = await visionJson<VisionJob>(`jobs?${query}`, {
        method: "POST",
        headers: { "Content-Type": "video/mp4" },
      });
      break;
    } catch (e) {
      if (signal?.aborted || !transient(e) || attempt >= 4) throw e;
      // Reuse the request ID: a lost reply must not strand a reserved worker.
      await sleep(Math.min(15_000, 1000 * 2 ** attempt), signal);
    }
  }
  let starting = false;
  // Closing or reloading the tab runs no cleanup: free the single worker with a
  // keepalive cancel instead of leaving it reserved until the idle timeout. An
  // upload cannot resume after a reload, so nothing is lost.
  const leave = () => {
    fetch(`/api/vision/jobs/${job.id}/cancel`, {
      method: "POST",
      keepalive: true,
      headers: visionHeaders(),
    }).catch(() => {});
  };
  const listen = typeof window !== "undefined";
  if (listen) window.addEventListener("pagehide", leave);
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
      // Rounded down: 100% means every byte arrived and the analysis is starting.
      onProgress?.(Math.floor((offset / file.size) * 100));
    }
    // Last moment a cancel applies. From here the server may already be analysing
    // (paid GPU time): closing the tab, leaving the page or a late abort must not
    // cancel it. The report page offers a cancel for a running analysis.
    signal?.throwIfAborted();
    if (listen) window.removeEventListener("pagehide", leave);
    starting = true;
    return await startAnalysis(job.id);
  } catch (e) {
    // The worker releases unstarted uploads by itself; once /start has been sent
    // a cancel could stop an analysis that is already running.
    if (!starting)
      // Free the worker straight away instead of waiting for the idle timeout.
      visionJson(`jobs/${job.id}/cancel`, { method: "POST" }).catch(() => {});
    throw e;
  } finally {
    if (listen) window.removeEventListener("pagehide", leave);
  }
}

/** /start is retried: a lost response must not throw away a complete upload.
 *  It takes no abort signal: once sent, the analysis may already be running. */
async function startAnalysis(id: string): Promise<VisionJob> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await visionJson<VisionJob>(`jobs/${id}/start`, { method: "POST" });
    } catch (e) {
      // An earlier attempt may have started it and only the reply was lost.
      if (e instanceof VisionError && e.status === 409) {
        const current = await visionJson<VisionJob>(`jobs/${id}`).catch(() => null);
        if (current && ["processing", "completed"].includes(current.status))
          return current;
        throw e;
      }
      if (!transient(e) || attempt >= 4) throw e;
      await sleep(Math.min(15_000, 1000 * 2 ** attempt));
    }
  }
}

/** The worker downloads the video itself; nothing large passes through the browser. */
export async function analyseYouTube(
  url: string,
  options: { title?: string; profile?: string; fps?: string; search?: string },
): Promise<VisionJob> {
  const query = new URLSearchParams({
    url,
    title: options.title || "",
    profile: options.profile || "general",
    fps: options.fps || "3",
    search: options.search || "none",
  });
  return visionJson<VisionJob>(`jobs/from-url?${query}`, { method: "POST" });
}

/** "2 GB" / "500 MB" for an upload limit in bytes. */
export function sizeLabel(bytes: number) {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}
