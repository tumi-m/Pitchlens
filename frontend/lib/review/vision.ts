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
  /** Size of a chunked upload in progress (absent for a YouTube download). */
  expectedBytes?: number;
  /** The upload every re-analysis of the same video goes back to (absent on the original). */
  rootJobId?: string | null;
  /** The worker can retry this stopped job from its saved upload (absent on older workers). */
  retryable?: boolean;
  /** Team names the owner saved on the worker (Kit A first); absent until named. */
  teamNames?: [string, string] | null;
  /** The day the match was played, "YYYY-MM-DD", when the owner set it. */
  matchDate?: string | null;
};

/** What the owner can name after the upload; every key is optional. */
export type VisionDetails = { names?: [string, string]; title?: string; matchDate?: string | null };

/** Saves team names, title or match date on the worker for this browser's match. */
export const saveVisionDetails = (jobId: string, patch: VisionDetails) =>
  visionJson<{ teamNames: [string, string] | null; title: string | null; matchDate: string | null }>(`jobs/${jobId}/details`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });

/** One player in one of the ball-label frames (GET /frames/{index}), in video pixels. */
export type TeamCrop = { index: number; id: number; box: [number, number, number, number] };
export type TeamPick = Pick<TeamCrop, "index" | "id">;
/** Up to 4 crops per kit group, and spread over colour for players in neither kit. */
export type TeamGroups = {
  colours: [string, string] | null;
  teamsSetBy: "owner" | null;
  framesReady: boolean;
  size: [number | null, number | null];
  groups: { A: TeamCrop[]; B: TeamCrop[]; unsorted: TeamCrop[] };
};
export type TeamFix = { kitA: TeamPick; kitB: TeamPick } | { reset: true };

/** Player crops per kit group for the owner's team fix (409: no kit colours or no footage). */
export const getTeams = (jobId: string) => visionJson<TeamGroups>(`jobs/${jobId}/teams`);

/** Relabels the match from one tapped player per kit (no new analysis), or undoes that.
 *  swapped: Kit A is the player tapped for Kit B, so the names must swap to stay true. */
export const saveTeams = (jobId: string, body: TeamFix) =>
  visionJson<{ swapped: boolean; teams: VisionResult["teams"]; teamsSetBy: "owner" | null }>(`jobs/${jobId}/teams`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** "YYYY-MM-DD" as this browser writes dates, read as a local day (not UTC midnight). */
export function matchDateLabel(iso: string | null | undefined): string | null {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso) : null;
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString() : null;
}
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
  /** Timings as the engine measured them, written only for the steps that ran
   *  (a player-load job has no ball timing); any may be missing from older results. */
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
  /** A rerun whose Kit A / Kit B were put in this job's order (its team names apply). */
  teamsAlignedTo?: string;
  /** "owner": the owner fixed the teams from tapped players (POST /teams); undo restores the engine's. */
  teamsSetBy?: "owner";
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
  /** A paused upload gives way to another browser's analysis after this many seconds. */
  uploadIdleSeconds?: number;
  /** Alone, a paused upload is kept this long for its browser to continue it. */
  uploadAbandonSeconds?: number;
  /** Analysis runs on a Modal GPU (minutes rather than an hour). */
  gpu?: boolean;
  /** false: this worker runs only 20-second tests (absent on older workers: full matches allowed). */
  fullMatch?: boolean;
  /** false when this deployment has no VISION_SERVICE_URL/TOKEN at all. */
  configured?: boolean;
  /** The site may start ball-model training (an access code is set and the worker allows it). */
  siteTraining?: boolean;
  detail?: string;
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

/** Network drops shorter than this (while the page is visible) resume where they
 *  left off. It stays under the worker's 5-minute idle release (VISION_UPLOAD_IDLE_SECONDS). */
const RESUME_WINDOW_MS = 240_000;
/** Smallest piece a stalled upload retries with (the worker accepts any piece up to its maximum). */
const MIN_UPLOAD_CHUNK = 512 * 1024;
/** A piece gets at least this long, or three times the slowest acknowledged piece. */
const MIN_PIECE_TIMEOUT_MS = 120_000;

/** "about 2 hours" for how long the worker keeps a paused upload. */
function keptFor(seconds: number) {
  const hours = Math.round(seconds / 3600);
  if (hours >= 1) return `about ${hours} hour${hours === 1 ? "" : "s"}`;
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
}
// Not 429: the worker's only 429 here is its 24-hour cap (code "daily-limit"),
// which retrying cannot lift; it only delays the message. Nor 507 (code
// "out-of-storage"): a full disk does not free itself within seconds. Nor a 503
// "full-match-unavailable": the worker has no GPU set up for full matches.
const transient = (e: unknown) =>
  !(e instanceof VisionError) ||
  (e.status >= 500 && e.status !== 507 && e.code !== "out-of-storage" && e.code !== "full-match-unavailable");

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
 * The analysis detail an analyse button starts on: Balanced (6 frames/sec) on a GPU
 * or whenever the ball model runs, since at 3 frames/sec passes and shots are missed
 * far more often (pass recall roughly halved on labelled tracking data). Player load
 * alone stays on Quick on a CPU worker. Only callers that omit fps fall back to 3.
 */
export function defaultVisionFps(health: Pick<VisionHealth, "gpu"> | null, playerLoadOnly: boolean): "3" | "6" {
  return health?.gpu || !playerLoadOnly ? "6" : "3";
}
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

/** The reservation's own query (title, start, diagnostic...), as it was sent. */
function storedQuery(entry: PendingUpload): [string, string][] {
  try {
    const parsed = JSON.parse(entry.optionsDigest);
    return Array.isArray(parsed) ? parsed.filter((p) => Array.isArray(p) && p.length === 2).map(([k, v]) => [String(k), String(v)]) : [];
  } catch {
    return [];
  }
}

/** This browser's unfinished upload, for the upload page to name after a reload. */
export function pendingUploadInfo(): { name: string; size: number; query: [string, string][] } | null {
  const entry = pendingUpload();
  return entry ? { name: entry.name, size: entry.size, query: storedQuery(entry) } : null;
}

/** The unfinished upload this file can continue: same name and size. lastModified is
 *  only a tie-breaker, since an iPhone Photos pick can give the same video a fresh one. */
export function pendingUploadFor(file: { name: string; size: number }): { requestId: string; query: [string, string][] } | null {
  const entry = pendingUpload();
  if (!entry || entry.name !== file.name || entry.size !== file.size) return null;
  const query = storedQuery(entry);
  return query.length ? { requestId: entry.requestId, query } : null;
}

type UploadChoices = {
  title: string;
  profile?: string;
  fps?: string;
  diagnostic?: boolean;
  start?: number;
  search?: "exhaustive" | "adaptive" | "none";
};

/** The reservation query for a file and these choices (without the request ID). */
export function uploadQuery(file: { size: number }, options: UploadChoices): URLSearchParams {
  return new URLSearchParams({
    title: options.title,
    profile: options.profile || "general",
    fps: options.fps || "3",
    size: String(file.size),
    diagnostic: String(options.diagnostic ?? false),
    start: String(options.start ?? 0),
    search: options.search ?? "exhaustive",
  });
}

/** Whether these choices are the ones the stored reservation was made with. */
export const sameUploadOptions = (query: URLSearchParams, stored: [string, string][]) =>
  JSON.stringify([...query.entries()]) === JSON.stringify(stored);

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
  options: UploadChoices & {
    signal?: AbortSignal;
    /** Whole percent acknowledged, plus the bytes behind it. */
    onProgress?: (percent: number, sentBytes: number, totalBytes: number) => void;
    /** The request ID this upload uses (called again if it has to be renewed). */
    onRequest?: (requestId: string) => void;
    /** The worker reserved the job. */
    onReserved?: (job: VisionJob) => void;
    /** Continue this browser's earlier upload: exactly its stored query and request ID. */
    resume?: { requestId: string; query: [string, string][] };
    /** The worker's idle limit (health.uploadIdleSeconds): a stalled piece is given up before it. */
    uploadIdleSeconds?: number;
    /** How long the worker keeps a paused upload (health.uploadAbandonSeconds), for the give-up message. */
    uploadAbandonSeconds?: number;
    /** Per-piece stall timeout in ms instead of the adaptive one (tests). */
    pieceTimeoutMs?: number;
  },
): Promise<VisionJob> {
  const { signal, onProgress, resume } = options;
  const query = resume ? new URLSearchParams(resume.query.filter(([k]) => k !== "requestId")) : uploadQuery(file, options);
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
  query.set("requestId", resume ? resume.requestId : same ? previous.requestId : crypto.randomUUID());
  savePendingUpload(entry(query.get("requestId")!));
  options.onRequest?.(query.get("requestId")!);
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
        options.onRequest?.(query.get("requestId")!);
        continue;
      }
      // Another file holds the worker: keep the entry that can still resume it.
      if (e instanceof VisionError && e.code === "own-upload") savePendingUpload(previous);
      if (signal?.aborted || !transient(e) || attempt >= 4) {
        // A raw "Failed to fetch" / "Load failed" says nothing; the worker's own 503s keep their words.
        if (!signal?.aborted && !(e instanceof VisionError))
          throw new UploadInterruptedError(CONNECTION_LOST);
        throw e;
      }
      // Reuse the request ID: a lost reply must not strand a reserved worker.
      await sleep(Math.min(15_000, 1000 * 2 ** attempt), signal);
    }
  }
  options.onReserved?.(job);
  let starting = false;
  let offset = Math.min(file.size, Math.max(0, job.receivedBytes ?? 0));
  // Leaving the page does not cancel the upload: phones discard background tabs
  // (switching apps mid-upload), and a reload resumes from the last byte the
  // worker acknowledged. An abandoned upload frees the worker by itself after
  // VISION_UPLOAD_IDLE_SECONDS without data.
  // Time only counts toward giving up while the page is visible: a locked phone or
  // another app suspends the page, and the worker keeps the upload meanwhile.
  const page = typeof document === "undefined" ? null : document;
  let hiddenMs = 0;
  let hiddenSince = page?.visibilityState === "hidden" ? Date.now() : null;
  const visibleNow = () => Date.now() - hiddenMs - (hiddenSince === null ? 0 : Date.now() - hiddenSince);
  // Back online, or back on the page: retry at once instead of finishing a backoff.
  let wake: (() => void) | null = null;
  const onVisibility = () => {
    if (page?.visibilityState === "hidden") {
      if (hiddenSince === null) hiddenSince = Date.now();
      return;
    }
    if (hiddenSince !== null) hiddenMs += Date.now() - hiddenSince;
    hiddenSince = null;
    wake?.();
  };
  const onOnline = () => wake?.();
  const backoff = (ms: number) =>
    new Promise<void>((resolve, reject) => {
      const done = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", aborted);
        wake = null;
      };
      const aborted = () => {
        done();
        reject(new DOMException("Upload cancelled.", "AbortError"));
      };
      const timer = setTimeout(() => {
        done();
        resolve();
      }, ms);
      wake = () => {
        done();
        resolve();
      };
      signal?.addEventListener("abort", aborted, { once: true });
      if (signal?.aborted) aborted();
    });
  page?.addEventListener("visibilitychange", onVisibility);
  if (typeof window !== "undefined") window.addEventListener("online", onOnline);
  // A piece whose reply never comes (a silently dead connection, a Wi-Fi to mobile
  // handover) is abandoned after this and resent smaller; never a fixed minute: a
  // 4 MB piece takes about a minute at 0.5 Mbit/s.
  const idleLimitMs = Math.max(MIN_PIECE_TIMEOUT_MS, ((options.uploadIdleSeconds ?? 300) - 30) * 1000);
  let slowestPieceMs = 0;
  let chunkSize = UPLOAD_CHUNK;
  try {
    signal?.throwIfAborted();
    let lastAck = visibleNow();
    // Rounded down, as below: 100% means every byte arrived.
    onProgress?.(Math.floor((offset / file.size) * 100), offset, file.size);
    while (offset < file.size) {
      for (let attempt = 0; ; attempt++) {
        signal?.throwIfAborted();
        // Re-sliced on every attempt: a stalled piece is resent smaller from the same offset.
        const piece = file.slice(offset, Math.min(file.size, offset + chunkSize));
        // Its own controller, chained by hand to the user's (AbortSignal.any needs Safari 17.4).
        const controller = new AbortController();
        const userAbort = () => controller.abort();
        signal?.addEventListener("abort", userAbort, { once: true });
        let stalled = false;
        const limit = options.pieceTimeoutMs ?? Math.min(idleLimitMs, Math.max(MIN_PIECE_TIMEOUT_MS, 3 * slowestPieceMs));
        const stall = setTimeout(() => {
          stalled = true;
          controller.abort();
        }, limit);
        const sentAt = Date.now();
        try {
          const { received } = await visionJson<{ received: number }>(
            `jobs/${job.id}/video?offset=${offset}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/octet-stream" },
              body: piece,
              signal: controller.signal,
            },
          );
          if (received > offset) slowestPieceMs = Math.max(slowestPieceMs, Date.now() - sentAt);
          offset = received;
          lastAck = visibleNow();
          // Acknowledged again: grow back towards full-size pieces.
          chunkSize = Math.min(UPLOAD_CHUNK, chunkSize * 2);
          break;
        } catch (e) {
          // Only the user's cancel ends the upload; a stall is a dropped connection.
          if (signal?.aborted) throw e;
          const resync =
            e instanceof VisionError &&
            e.status === 409 &&
            /Expected offset (\d+)/.exec(e.message);
          if (resync) {
            offset = Number(resync[1]);
            break;
          }
          if (stalled) chunkSize = Math.max(MIN_UPLOAD_CHUNK, Math.floor(chunkSize / 2));
          // Lost connection (phone switching networks, Wi-Fi blip) or a worker
          // redeploying (the site answers 502-504; the worker keeps the upload):
          // keep trying for a few visible minutes. Server errors: a few retries.
          const giveUp = stalled || unreachable(e)
            ? visibleNow() - lastAck > RESUME_WINDOW_MS
            : !transient(e) || attempt >= 4;
          if (giveUp) throw stalled ? new TypeError("Upload stalled") : e;
          // A stalled piece already waited its timeout: resend at once.
          if (!stalled) await backoff(Math.min(15_000, 1000 * 2 ** attempt));
        } finally {
          clearTimeout(stall);
          signal?.removeEventListener("abort", userAbort);
        }
      }
      // Rounded down: 100% means every byte arrived and the analysis is starting.
      onProgress?.(Math.floor((offset / file.size) * 100), offset, file.size);
    }
    // From here the server may already be analysing (paid GPU time): closing the
    // tab or leaving the page must not cancel it. An explicit cancel (the upload
    // page's "Cancel analysis") asks the worker, and only a confirmed stop is
    // reported as cancelled; the report page also offers a cancel.
    signal?.throwIfAborted();
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
          `Connection lost at ${Math.floor((offset / file.size) * 100)}%. Press Analyse with the same file to continue.${
            options.uploadAbandonSeconds
              ? ` What was sent is kept for ${keptFor(options.uploadAbandonSeconds)} unless someone else starts an analysis.`
              : ""
          }`,
        );
      // Free the worker straight away instead of waiting for the idle timeout.
      requestCancel(job.id).then((stopped) => stopped && savePendingUpload(null));
    }
    throw e;
  } finally {
    page?.removeEventListener("visibilitychange", onVisibility);
    if (typeof window !== "undefined") window.removeEventListener("online", onOnline);
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

/** /start is retried: a lost response must not throw away a complete upload.
 *  Only an explicit cancel aborts it (leaving the upload page does not), and the
 *  worker is then asked whether it stopped: the analysis may already be running. */
async function startAnalysis(id: string, signal?: AbortSignal): Promise<VisionJob> {
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted();
    try {
      return await visionJson<VisionJob>(`jobs/${id}/start`, { method: "POST", signal });
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

/** "2 GB" / "500 MB" for an upload limit in bytes. */
export function sizeLabel(bytes: number) {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}
