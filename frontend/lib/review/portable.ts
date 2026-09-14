import type { Detection, ReviewEvent, VideoReview } from "./types";

export const MAX_IMPORT_BYTES = 3 * 1024 * 1024;
export const EVENT_TYPES = [
  "goal",
  "shot",
  "save",
  "pass",
  "foul",
  "corner",
] as const;
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Expected a review object.");
  return value as Record<string, unknown>;
}
function text(
  value: unknown,
  field: string,
  limit: number,
  allowEmpty = false,
): string {
  if (
    typeof value !== "string" ||
    value.length > limit ||
    (!allowEmpty && !value.trim())
  )
    throw new Error(`Invalid ${field}.`);
  return value;
}
function number(
  value: unknown,
  field: string,
  min: number,
  max: number,
): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < min ||
    value > max
  )
    throw new Error(`Invalid ${field}.`);
  return value;
}
function list(value: unknown, field: string, max: number): unknown[] {
  if (!Array.isArray(value) || value.length > max)
    throw new Error(`Invalid ${field}.`);
  return value;
}
export function validateEvent(value: unknown, duration: number): ReviewEvent {
  const e = object(value);
  if (
    e.source !== "manual" ||
    (e.team !== "home" && e.team !== "away") ||
    !EVENT_TYPES.includes(e.type as ReviewEvent["type"])
  )
    throw new Error("Invalid event type, team or source.");
  const id = text(e.id, "event ID", 128);
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) throw new Error("Invalid event ID.");
  return {
    id,
    timestamp: number(e.timestamp, "event timestamp", 0, duration),
    type: e.type as ReviewEvent["type"],
    team: e.team,
    source: "manual",
    note: text(e.note, "event note", 300, true),
  };
}
export function parseReviewExport(raw: string) {
  if (new TextEncoder().encode(raw).byteLength > MAX_IMPORT_BYTES)
    throw new Error("Review files must be 3 MB or smaller.");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("This file is not valid JSON.");
  }
  const data = object(parsed);
  const r = object(data.review);
  if (
    data.schemaVersion !== 1 ||
    r.schemaVersion !== 1 ||
    r.source !== "local-video"
  )
    throw new Error(
      "Unsupported review format. Import a Pitchlens version 1 review.",
    );
  const duration = number(r.duration, "video duration", 0.001, 86400);
  const width = number(r.width, "video width", 1, 16384);
  const height = number(r.height, "video height", 1, 16384);
  if (!Number.isInteger(width) || !Number.isInteger(height))
    throw new Error("Invalid video dimensions.");
  const fileSize = number(r.fileSize, "video size", 1, 500 * 1024 * 1024);
  if (!Number.isInteger(fileSize)) throw new Error("Invalid video size.");
  const events = list(r.events, "events", 10000).map((e) =>
    validateEvent(e, duration),
  );
  if (new Set(events.map((e) => e.id)).size !== events.length)
    throw new Error("Duplicate event IDs in review.");
  if (
    !["not-requested", "completed", "partial", "failed"].includes(
      String(r.aiStatus),
    )
  )
    throw new Error("Invalid AI status.");
  const frames = list(r.frames, "sample frames", 6).map((value) => {
    const f = object(value);
    const image = text(f.image, "frame image", 1_000_000);
    if (!/^data:image\/jpeg;base64,\/9j\/[A-Za-z0-9+/]*={0,2}$/.test(image))
      throw new Error("Only embedded JPEG frame images are supported.");
    const frameWidth = number(f.width, "frame width", 1, 16384);
    const frameHeight = number(f.height, "frame height", 1, 16384);
    const predictions: Detection[] = list(f.predictions, "detections", 100).map(
      (value) => {
        const d = object(value);
        return {
          x: number(d.x, "detection x", 0, frameWidth),
          y: number(d.y, "detection y", 0, frameHeight),
          width: number(d.width, "detection width", 0.001, frameWidth * 2),
          height: number(d.height, "detection height", 0.001, frameHeight * 2),
          confidence: number(d.confidence, "confidence", 0, 1),
          class_id: number(d.class_id, "class ID", 0, 10000),
          class: text(d.class, "class label", 100),
        };
      },
    );
    return {
      timestamp: number(f.timestamp, "frame timestamp", 0, duration),
      image,
      width: frameWidth,
      height: frameHeight,
      predictions,
    };
  });
  if (
    r.videoFingerprint !== undefined &&
    (typeof r.videoFingerprint !== "string" ||
      !/^[a-f0-9]{64}$/.test(r.videoFingerprint))
  )
    throw new Error("Invalid video fingerprint.");
  const review: VideoReview = {
    schemaVersion: 1,
    source: "local-video",
    fileName: text(r.fileName, "video filename", 255),
    fileSize,
    duration,
    width,
    height,
    aiStatus: r.aiStatus as VideoReview["aiStatus"],
    ...(r.aiMessage === undefined
      ? {}
      : { aiMessage: text(r.aiMessage, "AI message", 2000, true) }),
    frames,
    events: events.sort((a, b) => a.timestamp - b.timestamp),
    notes: text(r.notes, "coaching notes", 10000, true),
    ...(r.videoFingerprint
      ? { videoFingerprint: r.videoFingerprint as string }
      : {}),
    importedAt: new Date().toISOString(),
  };
  return {
    title: text(data.title, "title", 200),
    homeTeamName: text(data.homeTeam, "home team", 80),
    awayTeamName: text(data.awayTeam, "away team", 80),
    review,
  };
}

/** A bounded sample fingerprint: first/last 64 KiB plus file size, not a full-file hash. */
export async function fingerprintVideo(file: Blob): Promise<string> {
  const [head, tail] = await Promise.all([
    file.slice(0, 65536).arrayBuffer(),
    file.slice(Math.max(0, file.size - 65536)).arrayBuffer(),
  ]);
  const size = new TextEncoder().encode(String(file.size));
  const sample = new Uint8Array(
    head.byteLength + tail.byteLength + size.length,
  );
  sample.set(new Uint8Array(head));
  sample.set(new Uint8Array(tail), head.byteLength);
  sample.set(size, head.byteLength + tail.byteLength);
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", sample)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
}
export function validateLinkedVideo(
  original: VideoReview,
  candidate: VideoReview,
): void {
  if (
    original.fileSize !== candidate.fileSize ||
    original.width !== candidate.width ||
    original.height !== candidate.height ||
    Math.abs(original.duration - candidate.duration) > 0.15 ||
    (original.videoFingerprint &&
      original.videoFingerprint !== candidate.videoFingerprint)
  )
    throw new Error(
      "This video does not match the review. Choose the original clip with the same size, duration and resolution.",
    );
}
