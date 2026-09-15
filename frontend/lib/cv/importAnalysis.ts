import type { ReviewEvent } from "@/lib/review/types";
import type { TrackingPayload } from "./types";

const ALLOWED = new Set(["goal", "shot", "pass", "turnover"]);

export function isTrackingPayload(value: unknown): value is TrackingPayload {
  if (!value || typeof value !== "object") return false;
  const v = value as TrackingPayload;
  return v.schemaVersion >= 2 && Array.isArray(v.timeline) && Array.isArray(v.events) && !!v.video;
}

export function candidatesToEvents(payload: TrackingPayload): ReviewEvent[] {
  return payload.events
    .filter((e) => ALLOWED.has(e.type) && (e.team === "home" || e.team === "away"))
    .map((e, i) => ({
      id: `cv_${i}_${Math.round(e.timestamp * 1000)}`,
      timestamp: e.timestamp,
      team: e.team as "home" | "away",
      type: e.type as ReviewEvent["type"],
      note: e.reason,
      source: "cv-candidate",
      needsReview: true,
    }));
}
