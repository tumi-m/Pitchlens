import { uploadToVision, VisionJob, VisionStartedError } from "./vision";
import type { UploadAck } from "./uploadProgress";

/**
 * Uploads in flight in this tab, outside any page: the upload page can be left
 * for another page of the site (the navbar links use client-side navigation)
 * without stopping the upload, and shows it again on return. A reload or a
 * closed tab still ends it; the worker keeps what it received for a resume.
 */
export type UploadEntry = {
  requestId: string;
  fileName: string;
  fileSize: number;
  title: string;
  /** Whole percent acknowledged; 100 once every byte arrived and the analysis is being started. */
  percent: number;
  sentBytes: number;
  totalBytes: number;
  /** Acknowledged bytes over time, for the time-left estimate. */
  acks: UploadAck[];
  jobId: string | null;
  state: "uploading" | "starting" | "started" | "failed";
  /** The upload page was left while this ran: the navbar says where it went. */
  left: boolean;
  controller: AbortController;
  promise: Promise<VisionJob>;
  error?: unknown;
};

type Options = Omit<Parameters<typeof uploadToVision>[1], "signal" | "onProgress" | "onRequest" | "onReserved">;

const uploads = new Map<string, UploadEntry>();
const listeners = new Set<() => void>();
let version = 0;

function emit() {
  version++;
  listeners.forEach((listener) => listener());
}

export function subscribeUploads(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Changes whenever an upload moves on (the snapshot for useSyncExternalStore). */
export const uploadsVersion = () => version;

export const inFlight = (entry: UploadEntry | null | undefined) =>
  !!entry && (entry.state === "uploading" || entry.state === "starting");

/** The most recent upload of this tab that has not been dealt with yet. */
export function currentUpload(): UploadEntry | null {
  let last: UploadEntry | null = null;
  for (const entry of uploads.values()) last = entry;
  return last;
}

/** An upload of this file still running: pressing Analyse again joins it. */
export function findUpload(file: { name: string; size: number }): UploadEntry | null {
  for (const entry of uploads.values())
    if (inFlight(entry) && entry.fileName === file.name && entry.fileSize === file.size) return entry;
  return null;
}

/** The result was shown (the report opened, or the error was read): forget it. */
export function dismissUpload(entry: UploadEntry) {
  if (uploads.get(entry.requestId) !== entry) return;
  uploads.delete(entry.requestId);
  emit();
}

export function markLeft(entry: UploadEntry, left: boolean) {
  entry.left = left;
  emit();
}

export function wakeLockSupported() {
  return typeof navigator !== "undefined" && "wakeLock" in navigator;
}

/** Keeps the screen on while an upload runs (phones lock after 30 s in Low Power
 *  Mode, which suspends the page). The browser drops the lock whenever the page
 *  is hidden, so it is asked for again when the page is visible. */
function holdWakeLock(): () => void {
  if (!wakeLockSupported()) return () => {};
  let sentinel: WakeLockSentinel | null = null;
  let stopped = false;
  const request = async () => {
    try {
      const next = await navigator.wakeLock.request("screen");
      if (stopped) {
        next.release().catch(() => {});
        return;
      }
      const previous = sentinel;
      sentinel = next;
      previous?.release().catch(() => {});
    } catch {
      // NotAllowedError while hidden, or the browser refused: the upload goes on.
    }
  };
  const visible = () => {
    if (document.visibilityState === "visible") request();
  };
  request();
  document.addEventListener("visibilitychange", visible);
  return () => {
    stopped = true;
    document.removeEventListener("visibilitychange", visible);
    sentinel?.release().catch(() => {});
    sentinel = null;
  };
}

/** Starts an upload that outlives the page that started it. */
export function startUpload(file: File, options: Options): UploadEntry {
  const controller = new AbortController();
  const entry: UploadEntry = {
    requestId: "",
    fileName: file.name,
    fileSize: file.size,
    title: options.title,
    percent: 0,
    sentBytes: 0,
    totalBytes: file.size,
    acks: [],
    jobId: null,
    state: "uploading",
    left: false,
    controller,
    promise: Promise.resolve(null as unknown as VisionJob),
  };
  const release = holdWakeLock();
  entry.promise = uploadToVision(file, {
    ...options,
    signal: controller.signal,
    onRequest: (requestId) => {
      if (entry.requestId) uploads.delete(entry.requestId);
      entry.requestId = requestId;
      uploads.set(requestId, entry);
      emit();
    },
    onReserved: (job) => {
      entry.jobId = job.id;
      emit();
    },
    onProgress: (percent, sent, total) => {
      entry.percent = percent;
      entry.totalBytes = total;
      if (sent !== entry.sentBytes || !entry.acks.length) entry.acks = [...entry.acks.slice(-200), { at: Date.now(), sent }];
      entry.sentBytes = sent;
      // From 100% the analysis is being started on the server.
      entry.state = percent >= 100 ? "starting" : "uploading";
      emit();
    },
  });
  entry.promise
    .then(
      (job) => {
        entry.state = "started";
        entry.jobId = job.id;
      },
      (e) => {
        // Too late to stop: the analysis runs, so it is shown rather than called cancelled.
        if (e instanceof VisionStartedError) {
          entry.state = "started";
          entry.jobId = e.jobId;
        } else {
          entry.state = "failed";
          entry.error = e;
        }
      },
    )
    .finally(() => {
      release();
      emit();
    });
  return entry;
}
