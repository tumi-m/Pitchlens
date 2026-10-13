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
  /** Where the analysis started in the file, in seconds. */
  startSeconds?: number;
  /** Set for a short test section (seconds analysed); null for a full run. */
  maxSeconds?: number | null;
  /** Set when this run re-analysed another job's saved footage. */
  sourceJobId?: string | null;
  /** Ball model used: "none" is a player-load job. */
  ballSearch?: string;
  /** Bytes the worker holds of an upload in progress. */
  receivedBytes?: number;
  /** The worker can retry this stopped job from its saved upload (absent on older workers). */
  retryable?: boolean;
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
  /** Timings are written only for the steps that ran: a player-load job has no ball timing. */
  performance?: {
    totalSeconds: number;
    playerInferenceSeconds?: number;
    ballInferenceSeconds?: number;
    decodeSeconds?: number;
    cameraMotionSeconds?: number;
    device: string;
  };
  sampleFps: number;
  teams: { id: number; label: string; colour: string }[];
  /** A rerun whose Kit A / Kit B were put in this job's order (its team names apply). */
  teamsAlignedTo?: string;
  metrics: {
    sampledFrames: number;
    playerFrames: number;
    ballFrames: number;
    ballFramesInferred?: number;
    teamSeconds: number[];
    unknownSeconds: number;
    possessionShare: (number | null)[];
    possessionCoverage: number;
    trackCount: number;
    /** Metres each kit ran, with the camera removed. Absent on results from before 2.6. */
    playerLoad?: {
      metres: [number, number];
      maxSpeed: number;
      steps: number;
      method: string;
      playerHeightM: number;
      footNoisePx: number;
    };
    /** Added in pipeline 1.5; older results do not have it. */
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
    };
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
  /** Analysis runs on a Modal GPU (minutes rather than an hour). */
  gpu?: boolean;
  /** false when this deployment has no VISION_SERVICE_URL/TOKEN at all. */
  configured?: boolean;
  detail?: string;
  /** Largest video the worker accepts, in bytes. */
  maxBytes?: number;
};

/** The worker's default upload limit, used when /health does not say. */
export const DEFAULT_MAX_BYTES = 500 * 1024 * 1024;
export const clockTime = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export class VisionError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    /** The parsed error body (for example resetsAt, or the jobId of this browser's earlier upload). */
    readonly data?: Record<string, unknown>,
  ) {
    super(message);
  }
}

/** What to show for a failed request. The daily cap says when analysis resumes. */
export function visionErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof VisionError && e.code === "daily-limit") {
    const at = Number(e.data?.resetsAt);
    if (Number.isFinite(at) && at > 0) {
      const when = new Date(at * 1000);
      const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      const day = when.toDateString() === new Date().toDateString() ? "" : "tomorrow ";
      return `Analysis is paused until ${day}${time} (this server's daily limit).`;
    }
  }
  return e instanceof Error ? e.message : fallback;
}

/** The worker no longer has this match: deleted here, in another tab, or by retention. */
export const isMatchDeleted = (e: unknown) =>
  e instanceof VisionError && e.status === 404 && /job not found/i.test(e.message);

/** A cancel came after /start and the worker did not stop the analysis: it runs on. */
export class VisionStartedError extends Error {
  constructor(readonly jobId: string) {
    super("The analysis had already started.");
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

/** Fired when the site asks for an access code (none saved, or the saved one was refused). */
export const ACCESS_EVENT = "pitchlens:access-required";

function accessRejected() {
  // A refused code is dropped so the next prompt starts empty.
  setVisionAccessCode("");
  try {
    window.dispatchEvent(new CustomEvent(ACCESS_EVENT));
  } catch {}
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
  if (!response.ok) {
    if (data.code === "access") accessRejected();
    throw new VisionError(
      data.detail || "Vision request failed",
      response.status,
      data.code,
      data && typeof data === "object" ? (data as Record<string, unknown>) : undefined,
    );
  }
  return data as T;
}

/** Stays under Vercel's 4.5 MB function request limit, with room for headers. */
export const UPLOAD_CHUNK = 4 * 1024 * 1024;

/** A lost reply must navigate to the same run, not launch paid inference twice. */
export async function rerunSavedVision(
  id: string,
  mode: "section" | "full",
  start: number,
  requestId: string,
  /** Ball model for the new run; omitted = the same as the source job. */
  search?: "exhaustive" | "none",
) {
  const query = new URLSearchParams({ mode, start: String(start), requestId });
  if (search) query.set("search", search);
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
// The daily cap lasts hours: retrying it only delays the message.
const transient = (e: unknown) =>
  !(e instanceof VisionError) || e.status >= 500 || (e.status === 429 && e.code !== "daily-limit");
/** The worker (or the way to it) is down, not refusing: a redeploy or a dropped connection. */
const unreachable = (e: unknown) => !(e instanceof VisionError) || [502, 503, 504].includes(e.status);

/** The upload this browser last started, so a reloaded page can resume it (same file, same options). */
const PENDING_KEY = "pitchlens-vision-pending-upload";
type PendingUpload = { requestId: string; name: string; size: number; lastModified: number; optionsDigest: string };

function pendingUpload(): PendingUpload | null {
  try {
    const saved = JSON.parse(localStorage.getItem(PENDING_KEY) || "null");
    return saved && typeof saved.requestId === "string" ? saved : null;
  } catch {
    return null;
  }
}

function savePendingUpload(entry: PendingUpload | null) {
  try {
    if (entry) localStorage.setItem(PENDING_KEY, JSON.stringify(entry));
    else localStorage.removeItem(PENDING_KEY);
  } catch {}
}

/** The upload stopped on the way (connection or worker gone); the worker keeps what it received. */
export class UploadInterruptedError extends Error {}

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
  });
  // The same file with the same options reuses the stored request ID: after a
  // reload or a long drop the worker answers with the upload it already holds.
  const optionsDigest = JSON.stringify([...query.entries()]);
  const named = file as Partial<File>;
  const entry = (requestId: string): PendingUpload => ({
    requestId,
    name: named.name ?? "",
    size: file.size,
    lastModified: named.lastModified ?? 0,
    optionsDigest,
  });
  const previous = pendingUpload();
  const same =
    previous &&
    previous.name === (named.name ?? "") &&
    previous.size === file.size &&
    previous.lastModified === (named.lastModified ?? 0) &&
    previous.optionsDigest === optionsDigest;
  query.set("requestId", same ? previous.requestId : crypto.randomUUID());
  savePendingUpload(entry(query.get("requestId")!));
  signal?.throwIfAborted();
  // Never abort the reservation mid-flight: the worker would stay reserved
  // under an id this browser never learns. Cancellation is checked just after.
  let job: VisionJob;
  let renewed = false;
  for (let attempt = 0; ; attempt++) {
    try {
      job = await visionJson<VisionJob>(`jobs?${query}`, {
        method: "POST",
        headers: { "Content-Type": "video/mp4" },
      });
      break;
    } catch (e) {
      // The stored upload was released meanwhile: start a new one, once.
      if (!renewed && e instanceof VisionError && e.status === 409 && /previous upload stopped|already used for different options/i.test(e.message)) {
        renewed = true;
        query.set("requestId", crypto.randomUUID());
        savePendingUpload(entry(query.get("requestId")!));
        continue;
      }
      // Another file holds the worker: keep the entry that can still resume it.
      if (e instanceof VisionError && e.code === "own-upload") savePendingUpload(previous);
      if (signal?.aborted || !transient(e) || attempt >= 4) {
        // A raw "Failed to fetch" / "Load failed" says nothing; the worker's own 503s keep their words.
        if (!signal?.aborted && !(e instanceof VisionError))
          throw new UploadInterruptedError("Could not reach the analysis server. Check your connection and press Analyse again.");
        throw e;
      }
      // Reuse the request ID: a lost reply must not strand a reserved worker.
      await sleep(Math.min(15_000, 1000 * 2 ** attempt), signal);
    }
  }
  let starting = false;
  let offset = Math.min(file.size, Math.max(0, job.receivedBytes ?? 0));
  try {
    signal?.throwIfAborted();
    let lastAck = Date.now();
    onProgress?.(Math.round((offset / file.size) * 100));
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
          // Lost connection (phone switching networks, Wi-Fi blip) or a worker
          // redeploying (the site answers 502-504; the worker keeps the upload):
          // keep trying until the worker would give up on us. Server errors: a few retries.
          const giveUp = unreachable(e)
            ? Date.now() - lastAck > RESUME_WINDOW_MS
            : !transient(e) || attempt >= 4;
          if (giveUp) throw e;
          await sleep(Math.min(15_000, 1000 * 2 ** attempt), signal);
        }
      }
      onProgress?.(Math.round((offset / file.size) * 100));
    }
    starting = true;
    const started = await startAnalysis(job.id, signal);
    savePendingUpload(null);
    return started;
  } catch (e) {
    if (signal?.aborted) {
      // The user cancelled. After /start the worker may be analysing already:
      // only a confirmed stop may be reported as cancelled.
      const stopped = await requestCancel(job.id);
      if (stopped) savePendingUpload(null);
      if (starting && !stopped) throw new VisionStartedError(job.id);
      throw e instanceof DOMException && e.name === "AbortError"
        ? e
        : new DOMException("Upload cancelled.", "AbortError");
    }
    // Once /start has been sent the server may already be analysing, and a
    // cancel would stop it; the worker releases unstarted uploads by itself.
    if (!starting) {
      // Out of reach: keep the reservation and the stored request ID, so pressing
      // Analyse with the same file continues from what the worker received.
      if (unreachable(e))
        throw new UploadInterruptedError(
          `Connection lost at ${Math.round((offset / file.size) * 100)}%. Press Analyse with the same file to resume.`,
        );
      // Free the worker straight away instead of waiting for the idle timeout.
      requestCancel(job.id).then((stopped) => stopped && savePendingUpload(null));
    }
    throw e;
  }
}

/** Free the worker from this browser's earlier upload (the 409 "own-upload" answer). */
export async function cancelEarlierUpload(jobId: string): Promise<boolean> {
  const stopped = await requestCancel(jobId);
  if (stopped) savePendingUpload(null);
  return stopped;
}

/** Ask the worker to stop a job. No reply within a few seconds counts as not stopped. */
async function requestCancel(id: string): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const reply = await visionJson<{ requested?: boolean }>(`jobs/${id}/cancel`, {
      method: "POST",
      signal: controller.signal,
    });
    return reply.requested === true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
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
