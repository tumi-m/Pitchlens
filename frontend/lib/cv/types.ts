export type TeamSide = "home" | "away" | "unknown";

export interface TrackingPlayer {
  id: number;
  team: TeamSide;
  x: number;
  y: number;
  bx: number;
  by: number;
  bw: number;
  bh: number;
  conf: number;
}

export interface TrackingBall {
  x: number | null;
  y: number | null;
  bx: number;
  by: number;
  bw: number;
  bh: number;
  conf: number;
}

export interface TrackingFrame {
  t: number;
  frame: number;
  players: TrackingPlayer[];
  ball: TrackingBall | null;
}

export interface TrackingEvent {
  timestamp: number;
  type: string;
  team: string;
  track_id: number | null;
  x: number | null;
  y: number | null;
  confidence: number;
  space: "pitch" | "image";
  needs_review: boolean;
  reason: string;
}

export interface TrackingPayload {
  schemaVersion: number;
  provenance: string;
  detector: string;
  space: "pitch" | "image";
  calibrated: boolean;
  video: { fps: number; duration: number; width: number; height: number; framesProcessed: number; stride: number };
  pitch: { kind: string; length: number; width: number; unit: "m" | "px" };
  teams: Record<string, TeamSide>;
  tracks: Array<{ trackId: number; team: TeamSide; samples: number; durationSec: number; distance: number; distanceUnit: string; meanX: number; meanY: number }>;
  possession: { home: number | null; away: number | null; knownFrames: number; totalFrames: number; ballVisibleRate: number; unknownRate: number; note: string };
  events: TrackingEvent[];
  eventCounts: Record<string, Record<string, number>>;
  heatmaps: { home: Array<{ x: number; y: number; intensity: number }>; away: Array<{ x: number; y: number; intensity: number }> };
  timeline: TrackingFrame[];
  limitations: string[];
}

export function frameAt(payload: TrackingPayload, time: number): TrackingFrame | null {
  const frames = payload.timeline;
  if (!frames.length) return null;
  let best = frames[0];
  let bestD = Math.abs(frames[0].t - time);
  for (const f of frames) {
    const d = Math.abs(f.t - time);
    if (d < bestD) {
      best = f;
      bestD = d;
    }
    if (f.t > time + 0.25) break;
  }
  return best;
}
